"""回归测试：5.3 —— DB task 与 SignTask 持久化状态模型统一。

4.2/5.3 起，run_task_once 复用进程内 SignTaskService.run_task_with_logs：
- 调用方持锁（lock_already_held=True）避免同锁复入；
- 传入 run_id，使 TaskLog 与该次运行在 SignTask 状态机/历史中的 run_id 对齐；
- run_task_with_logs 在 manage_run_status 时写入 running/finished 状态机。
"""

import asyncio
import uuid

import pytest

from backend.core.database import (
    Base,
    ensure_schema_upgrades,
    get_engine,
    get_session_local,
)
from backend.models.account import Account
from backend.models.task import Task
from backend.services import tasks as tasks_mod
from backend.services.sign_tasks import get_sign_task_service
from backend.services.tasks import run_task_once


@pytest.fixture
def db_session():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    session = get_session_local()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    yield
    import backend.utils.account_locks as al

    al._ACCOUNT_LOCKS.clear()
    al._LOCK_DIR = None
    tasks_mod._active_tasks.clear()
    tasks_mod._active_logs.clear()
    import backend.services.sign_tasks as st

    st._sign_task_service = None


def _make_task(db, account_name: str = "acct_unify", task_name: str = "task1"):
    account = db.query(Account).filter(Account.account_name == account_name).first()
    if not account:
        account = Account(account_name=account_name, api_id="12345", api_hash="a" * 32)
        db.add(account)
        db.commit()
        db.refresh(account)
    task = Task(name=task_name, cron="0 9 * * *", enabled=True, account_id=account.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_db_task_run_id_shared_with_sign_task_status(db_session, monkeypatch):
    """run_task_once 传入的 run_id 同时落在 TaskLog 与 SignTask 状态机。"""
    task = _make_task(db_session)
    account = task.account

    captured: dict = {}

    async def fake_run(account_name, task_name, **kwargs):
        captured["account_name"] = account_name
        captured["task_name"] = task_name
        captured["lock_already_held"] = kwargs.get("lock_already_held")
        captured["run_id"] = kwargs.get("run_id")
        # 模拟进程内执行：写 running + finished 状态机（manage_run_status 路径）
        svc = get_sign_task_service()
        svc._set_run_status(
            account_name,
            task_name,
            run_id=captured["run_id"],
            state="running",
            success=None,
            error="",
            output="",
            started_at="2026-09-01T00:00:00",
            finished_at=None,
        )
        await asyncio.sleep(0.01)
        svc._set_run_status(
            account_name,
            task_name,
            run_id=captured["run_id"],
            state="finished",
            success=True,
            error="",
            output="done",
            started_at="2026-09-01T00:00:00",
            finished_at="2026-09-01T00:00:01",
        )
        return {"success": True, "output": "done", "error": ""}

    svc = get_sign_task_service()
    monkeypatch.setattr(svc, "run_task_with_logs", fake_run)

    async def main():
        log = await run_task_once(db_session, task)
        return log

    log = asyncio.run(main())

    # TaskLog 已持久化并携带 run_id/worker_id
    assert log.status == "success"
    assert log.run_id and len(log.run_id) == 32

    # 传给 SignTaskService 的 run_id 与 TaskLog 一致，且调用方已持锁
    assert captured.get("run_id") == log.run_id
    assert captured.get("lock_already_held") is True
    assert captured.get("account_name") == account.account_name
    assert captured.get("task_name") == task.name


def test_db_task_run_id_matches_history_entry(db_session, monkeypatch, tmp_path):
    """进程内执行结束后，SignTask 历史（SQLite）条目的 run_id 与 TaskLog 一致。"""
    from backend.core.config import Settings

    monkeypatch.setattr(
        Settings,
        "resolve_workdir",
        lambda self: tmp_path / "workdir",
        raising=False,
    )
    task = _make_task(db_session, account_name="acct_hist", task_name="task_hist")
    account = task.account

    svc = get_sign_task_service()

    async def fake_run(account_name, task_name, **kwargs):
        run_id = kwargs.get("run_id") or uuid.uuid4().hex
        # 模拟真实 run_task_with_logs(manage_run_status=True) 路径：
        # 先设 running 状态机（写入 run_id），_save_run_info 再从状态机复用同一 run_id
        svc._set_run_status(
            account_name,
            task_name,
            run_id=run_id,
            state="running",
            success=None,
            error="",
            output="",
            started_at="2026-09-01T00:00:00",
            finished_at=None,
        )
        svc._save_run_info(
            task_name,
            success=True,
            message="ok",
            account_name=account_name,
            flow_logs=["line"],
        )
        return {"success": True, "output": "done", "error": ""}

    monkeypatch.setattr(svc, "run_task_with_logs", fake_run)

    async def main():
        return await run_task_once(db_session, task)

    log = asyncio.run(main())

    entries = svc._load_history_entries(task.name, account_name=account.account_name)
    assert entries, "历史应有 SQLite 条目"
    # 历史条目的 run_id 与 TaskLog 对齐（统一状态模型核心）
    assert entries[0].get("run_id") == log.run_id
    assert log.run_id and len(log.run_id) == 32
