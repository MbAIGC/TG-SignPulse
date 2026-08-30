from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.orm import Session

from backend.core.auth import get_current_user, verify_token
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.models.account import Account
from backend.models.task_log import TaskLog
from backend.scheduler import sync_jobs
from backend.schemas.task import TaskCreate, TaskOut, TaskUpdate
from backend.schemas.task_log import TaskLogOut
from backend.services import tasks as task_service

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return task_service.list_tasks(db)


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    account = db.query(Account).filter(Account.id == payload.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    task = task_service.create_task(
        db,
        name=payload.name,
        cron=payload.cron,
        enabled=payload.enabled,
        account_id=payload.account_id,
    )
    await sync_jobs()
    return task


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    task = task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    task = task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.account_id is not None:
        account = db.query(Account).filter(Account.id == payload.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
    updated = task_service.update_task(
        db,
        task,
        name=payload.name,
        cron=payload.cron,
        enabled=payload.enabled,
        account_id=payload.account_id,
    )
    await sync_jobs()
    return updated


@router.delete("/{task_id}", status_code=status.HTTP_200_OK)
async def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    task = task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task_service.delete_task(db, task)
    await sync_jobs()
    return {"ok": True}


@router.post("/{task_id}/run", response_model=TaskLogOut)
async def run_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    task = task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    log = await task_service.run_task_once(db, task)
    return log


@router.get("/{task_id}/logs", response_model=list[TaskLogOut])
def list_logs(
    task_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    task = task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    logs = task_service.list_task_logs(db, task_id, limit=limit)
    return logs


@router.websocket("/ws/{task_id}")
async def task_logs_ws(
    websocket: WebSocket,
    task_id: int,
    db: Session = Depends(get_db),
):
    """
    WebSocket 实时推送数据库任务日志
    认证走首帧消息 {"token": "..."}，避免 token 暴露在 URL 查询参数
    """
    # 先接受连接，再读取首帧认证（token 不进 URL，防止代理日志泄密）
    await websocket.accept()

    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        token = (auth_msg or {}).get("token") if isinstance(auth_msg, dict) else None
        user = verify_token(token, db) if token else None
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    last_idx = 0
    try:
        while True:
            # 获取当前所有日志
            active_logs = task_service.get_active_logs(task_id)

            # 如果有新内容，则推送
            if len(active_logs) > last_idx:
                new_logs = active_logs[last_idx:]
                await websocket.send_json(
                    {
                        "type": "logs",
                        "data": new_logs,
                        "is_running": task_service.is_task_running(task_id),
                    }
                )
                last_idx = len(active_logs)

            # 如果任务已结束且日志已推完
            if not task_service.is_task_running(task_id) and last_idx >= len(
                active_logs
            ):
                await websocket.send_json({"type": "done", "is_running": False})
                break

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS Error for Task %s", task_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/logs/{log_id}/output")
def get_log_output(
    log_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取任务日志的完整输出文件内容"""
    log = db.query(TaskLog).filter(TaskLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")

    if not log.log_path:
        return {"output": log.output or "No detailed log file available."}

    settings = get_settings()
    logs_dir = settings.resolve_logs_dir().resolve()
    target_path = Path(log.log_path).resolve()

    try:
        target_path.relative_to(logs_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to log file outside logs directory is forbidden",
        )

    # 拒绝符号链接与非普通文件，防止日志读取越界
    if target_path.is_symlink() or not target_path.is_file():
        return {"output": log.output or "No detailed log file available."}

    try:
        # 限制完整日志读取大小（默认读取末尾 MAX_LOG_READ_BYTES 字节，
        # 避免大日志一次性读入内存造成 OOM）
        max_bytes = int(os.environ.get("TASK_LOG_READ_MAX_BYTES", str(2 * 1024 * 1024)))
        with open(target_path, "r", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            truncated = size > max_bytes
            if truncated:
                f.seek(size - max_bytes)
                # 丢弃第一行可能被截断的半行
                f.readline()
            else:
                f.seek(0)
            content = f.read()
        return {"output": content, "truncated": truncated}
    except Exception:
        logger.exception("Failed to read log file %s", target_path)
        raise HTTPException(status_code=500, detail="Failed to read log file")
