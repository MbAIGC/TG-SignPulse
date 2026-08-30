from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from backend.cli.tasks import async_run_task_cli
from backend.core.config import get_settings
from backend.models.account import Account
from backend.models.task import Task
from backend.models.task_log import TaskLog
from backend.utils.account_locks import AccountLockTimeout, get_account_lock
from backend.utils.time import utc_now_naive
from tg_signer.async_utils import create_logged_task

settings = get_settings()

logger = logging.getLogger(__name__)

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
    count = db.query(TaskLog).filter(TaskLog.started_at < cutoff).delete(synchronize_session=False)
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


def _create_log_file(task: Task) -> Path:
    logs_dir = settings.resolve_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = utc_now_naive().strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"task_{task.id}_{ts}.log"


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


async def _run_task_once_locked(
    db: Session, task: Task, account: Account
) -> TaskLog:
    log_file = _create_log_file(task)

    _active_tasks[task.id] = True
    _active_logs[task.id] = []

    task_log = TaskLog(
        task_id=task.id,
        run_id=uuid.uuid4().hex,
        worker_id=_current_worker_id(),
        status="running",
        log_path=str(log_file),
        started_at=utc_now_naive(),
    )
    db.add(task_log)
    db.commit()
    db.refresh(task_log)

    def log_callback(line: str):
        _active_logs[task.id].append(line)
        if len(_active_logs[task.id]) > 500:
            _active_logs[task.id].pop(0)

    try:
        # 使用异步执行调用，并注入回调
        returncode, stdout, stderr = await async_run_task_cli(
            account_name=account.account_name,
            task_name=task.name,
            callback=log_callback,
        )

        full_output = (stdout or "") + "\n" + (stderr or "")

        # 写入日志文件（完整内容）
        with open(log_file, "w", encoding="utf-8") as fp:
            fp.write(full_output)

        # 更新数据库记录
        task_log.finished_at = utc_now_naive()
        task_log.status = "success" if returncode == 0 else "failed"
        if returncode != 0:
            task_log.output = (
                stderr[-1000:] if stderr else "Failed with exit code " + str(returncode)
            )
        else:
            task_log.output = "Success"

        db.commit()
        db.refresh(task_log)

        task.last_run_at = task_log.finished_at
        db.commit()
    except Exception as e:
        msg = f"Error running task: {e}"
        _active_logs[task.id].append(msg)
        task_log.status = "failed"
        task_log.output = msg[-1000:]
        db.commit()
    finally:
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
