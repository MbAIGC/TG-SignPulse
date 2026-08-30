from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.account import Account
from backend.models.task import Task
from backend.models.task_log import TaskLog
from backend.utils.account_locks import AccountLockTimeout, get_account_lock
from backend.utils.time import utc_now_naive
from tg_signer.async_utils import create_logged_task
from tg_signer.utils import atomic_write_text

settings = get_settings()

logger = logging.getLogger(__name__)

_MAX_TASK_LOG_BYTES = 4 * 1024 * 1024


# 用于实时日志推送的状态跟踪
_active_tasks: dict[int, bool] = {}
_active_logs: dict[int, list[str]] = {}


def _current_worker_id() -> str:
    """当前进程标识：pid@hostname，便于多 worker 排查并发来源。"""
    return f"{os.getpid()}@{socket.gethostname()}"


def get_active_logs(task_id: int) -> list[str]:
    return _active_logs.get(task_id, [])


def is_task_running(task_id: int) -> bool:
    return _active_tasks.get(task_id, False)


def list_tasks(db: Session) -> List[Task]:
    return db.query(Task).order_by(Task.id.desc()).all()


def cleanup_old_logs(db: Session, days: int = 3) -> int:
    """清理超过指定天数的任务日志和文件"""
    cutoff = utc_now_naive() - timedelta(days=days)

    # 仅查询文件路径以删除磁盘文件，避免加载大对象到内存
    old_log_paths = (
        db.query(TaskLog.log_path)
        .filter(TaskLog.started_at < cutoff, TaskLog.log_path.isnot(None))
        .all()
    )

    logs_dir = settings.resolve_logs_dir().resolve()
    for (log_path,) in old_log_paths:
        if log_path:
            try:
                p = Path(log_path).resolve()
                # 只允许删除日志目录内的普通文件，拒绝符号链接/目录/越界路径
                if not p.is_relative_to(logs_dir):
                    logger.warning("拒绝删除日志目录外的文件: %s", log_path)
                    continue
                if p.is_symlink() or not p.is_file():
                    logger.warning("拒绝删除符号链接/非普通文件: %s", log_path)
                    continue
                p.unlink()
            except Exception:
                pass

    # 批量删除数据库记录
    count = (
        db.query(TaskLog)
        .filter(TaskLog.started_at < cutoff)
        .delete(synchronize_session=False)
    )
    if count > 0:
        db.commit()
    return count


def get_task(db: Session, task_id: int) -> Optional[Task]:
    return db.query(Task).filter(Task.id == task_id).first()


def create_task(
    db: Session,
    name: str,
    cron: str,
    enabled: bool,
    account_id: int,
) -> Task:
    task = Task(name=name, cron=cron, enabled=enabled, account_id=account_id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(
    db: Session,
    task: Task,
    *,
    name: Optional[str] = None,
    cron: Optional[str] = None,
    enabled: Optional[bool] = None,
    account_id: Optional[int] = None,
) -> Task:
    if name is not None:
        task.name = name
    if cron is not None:
        task.cron = cron
    if enabled is not None:
        task.enabled = enabled
    if account_id is not None:
        task.account_id = account_id
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task: Task) -> None:
    db.delete(task)
    db.commit()


def _bounded_log_output(output: str) -> str:
    """Keep diagnostic files bounded while retaining their most recent lines."""
    max_bytes = int(os.getenv("TASK_LOG_MAX_BYTES", str(_MAX_TASK_LOG_BYTES)))
    max_bytes = max(1024, max_bytes)
    encoded = output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return output
    tail = encoded[-max_bytes:].decode("utf-8", errors="replace")
    return "[日志已截断，仅保留末尾内容]\n" + tail


def _create_log_file(task: Task) -> Path:
    logs_dir = settings.resolve_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = utc_now_naive().strftime("%Y%m%d_%H%M%S_%f")
    return logs_dir / f"task_{task.id}_{ts}_{uuid.uuid4().hex[:8]}.log"


async def run_task_once(db: Session, task: Task) -> TaskLog:
    if is_task_running(task.id):
        # 如果已经在运行，返回最新的运行记录（或者抛出异常）
        last_log = (
            db.query(TaskLog)
            .filter(TaskLog.task_id == task.id)
            .order_by(TaskLog.id.desc())
            .first()
        )
        return last_log

    account: Account = task.account  # type: ignore[assignment]

    # 账号级互斥：与进程内 SignTask/Telegram 流程共享同一把锁，
    # 覆盖「创建运行记录 -> 启动子进程 -> 等待完成」完整区间，
    # 避免同账号 DB task 与 SignTask 同时执行（跨进程由文件锁保证）。
    account_lock = get_account_lock(account.account_name)
    try:
        async with account_lock:
            # The initial check is only an optimization; another caller may have
            # started while waiting for the account lock.
            if is_task_running(task.id):
                existing = (
                    db.query(TaskLog)
                    .filter(TaskLog.task_id == task.id, TaskLog.status == "running")
                    .order_by(TaskLog.id.desc())
                    .first()
                )
                if existing is not None:
                    return existing
                return _lock_timeout_task_log(db, task, "task is already running")
            return await _run_task_once_locked(db, task, account)
    except AccountLockTimeout as e:
        logger.warning("任务 %s (账号 %s) 跳过：%s", task.id, account.account_name, e)
        return _lock_timeout_task_log(db, task, str(e))


def _lock_timeout_task_log(db: Session, task: Task, reason: str) -> TaskLog:
    """锁超时时写入一条失败记录，避免任务静默跳过且无迹可查。"""
    task_log = TaskLog(
        task_id=task.id,
        run_id=uuid.uuid4().hex,
        worker_id=_current_worker_id(),
        status="failed",
        log_path=None,
        output=f"Account lock timeout: {reason}"[-1000:],
        started_at=utc_now_naive(),
        finished_at=utc_now_naive(),
    )
    db.add(task_log)
    db.commit()
    db.refresh(task_log)
    return task_log


async def _run_task_once_locked(db: Session, task: Task, account: Account) -> TaskLog:
    log_file = _create_log_file(task)

    _active_tasks[task.id] = True
    _active_logs[task.id] = []

    # 运行标识：与 SignTask 状态机/历史条目对齐（run_task_with_logs 复用该 run_id）
    run_id = uuid.uuid4().hex

    task_log = TaskLog(
        task_id=task.id,
        run_id=run_id,
        worker_id=_current_worker_id(),
        status="running",
        log_path=str(log_file),
        started_at=utc_now_naive(),
    )
    db.add(task_log)
    db.commit()
    db.refresh(task_log)
    logger.info(
        "Task run started task_id=%s account=%s run_id=%s worker_id=%s",
        task.id,
        account.account_name,
        run_id,
        task_log.worker_id,
    )

    def log_callback(line: str):
        _active_logs[task.id].append(line)
        if len(_active_logs[task.id]) > 500:
            _active_logs[task.id].pop(0)

    bridge_task = None
    try:
        # 4.2 统一到进程内 SignTaskService 执行链路（不再 spawn tg-signer 子进程）：
        # - 外层 run_task_once 已持账号锁，此处 lock_already_held=True 避免同锁复入；
        # - 传入 run_id 使 TaskLog 与 SignTask 运行状态机/历史条目的 run_id 对齐；
        # - 执行期间实时日志由 SignTaskService 维护，这里周期性桥接到 _active_logs[task.id]
        #   供 DB task 的 WebSocket 实时推送继续使用。
        from backend.services.sign_tasks import get_sign_task_service

        sign_service = get_sign_task_service()

        async def bridge_logs() -> None:
            task_key_last = 0
            while True:
                try:
                    entries = sign_service.get_active_logs(
                        account.account_name, task.name
                    )
                    for line in entries[task_key_last:]:
                        log_callback(line)
                    task_key_last = len(entries)
                except Exception:
                    pass
                await asyncio.sleep(0.3)
                if not _active_tasks.get(task.id):
                    break

        bridge_task = create_logged_task(
            bridge_logs(),
            description=f"db task log bridge {task.id}",
        )

        result = await sign_service.run_task_with_logs(
            account.account_name,
            task.name,
            lock_already_held=True,
            run_id=run_id,
            source="db_task",
        )

        success = bool(result.get("success"))
        full_output = result.get("output") or ""

        # Write the complete output atomically so readers never observe a
        # partially written log file after process interruption.
        atomic_write_text(log_file, _bounded_log_output(full_output))

        # 更新数据库记录
        task_log.finished_at = utc_now_naive()
        task_log.status = "success" if success else "failed"
        if not success:
            task_log.output = (result.get("error") or full_output or "Failed")[-1000:]
        else:
            task_log.output = "Success"

        db.commit()
        db.refresh(task_log)

        task.last_run_at = task_log.finished_at
        db.commit()
        logger.info(
            "Task run finished task_id=%s account=%s run_id=%s status=%s",
            task.id,
            account.account_name,
            run_id,
            task_log.status,
        )
    except asyncio.CancelledError:
        msg = "Task execution cancelled"
        _active_logs[task.id].append(msg)
        task_log.status = "cancelled"
        task_log.output = msg
        task_log.finished_at = utc_now_naive()
        db.commit()
        logger.info(
            "Task run cancelled task_id=%s account=%s run_id=%s",
            task.id,
            account.account_name,
            run_id,
        )
        raise
    except Exception as e:
        msg = f"Error running task: {e}"
        _active_logs[task.id].append(msg)
        task_log.status = "failed"
        task_log.output = msg[-1000:]
        task_log.finished_at = utc_now_naive()
        db.commit()
        logger.error(
            "Task run failed task_id=%s account=%s run_id=%s error=%s",
            task.id,
            account.account_name,
            run_id,
            msg,
        )
    finally:
        # Ensure the bridge has stopped before a subsequent run may reuse the
        # same task log buffer. Merely calling cancel() leaves a scheduling
        # window where an old bridge can append stale lines.
        if bridge_task is not None:
            bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await bridge_task
        _active_tasks[task.id] = False

        # 延迟清理日志
        async def cleanup():
            await asyncio.sleep(60)
            if not is_task_running(task.id):
                _active_logs.pop(task.id, None)

        create_logged_task(
            cleanup(),
            description=f"legacy task log cleanup {task.id}",
        )

    return task_log


def list_task_logs(db: Session, task_id: int, limit: int = 50) -> List[TaskLog]:
    return (
        db.query(TaskLog)
        .filter(TaskLog.task_id == task_id)
        .order_by(TaskLog.id.desc())
        .limit(limit)
        .all()
    )
