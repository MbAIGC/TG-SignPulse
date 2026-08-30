import asyncio

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
from backend.services.tasks import _bounded_log_output, _create_log_file, run_task_once


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


def _make_task(db, suffix=""):
    account = Account(
        account_name=f"acct_log{suffix}", api_id="12345", api_hash="a" * 32
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    task = Task(
        name=f"task_log{suffix}", cron="0 9 * * *", enabled=True, account_id=account.id
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_db_task_log_paths_are_unique(db_session):
    task = _make_task(db_session)

    assert _create_log_file(task) != _create_log_file(task)


def test_task_log_output_is_bounded(monkeypatch):
    monkeypatch.setenv("TASK_LOG_MAX_BYTES", "1024")
    output = _bounded_log_output("x" * 4096)

    assert len(output.encode("utf-8")) <= 1024 + len(
        "[日志已截断，仅保留末尾内容]\n".encode()
    )
    assert output.startswith("[日志已截断，仅保留末尾内容]\n")


@pytest.mark.asyncio
async def test_db_task_cancellation_persists_terminal_status(db_session, monkeypatch):
    task = _make_task(db_session, suffix="_cancel")

    async def cancelled_run(*args, **kwargs):
        raise asyncio.CancelledError

    service = get_sign_task_service()
    monkeypatch.setattr(service, "run_task_with_logs", cancelled_run)

    with pytest.raises(asyncio.CancelledError):
        await run_task_once(db_session, task)

    log = db_session.query(tasks_mod.TaskLog).filter_by(task_id=task.id).one()
    assert log.status == "cancelled"
    assert log.finished_at is not None
