"""回归测试：第三档后续 3.1 —— DB task (run_task_once) 接入统一账号级 AccountLock。

覆盖：
- 同账号锁被占用时，run_task_once 等待锁释放后执行（互斥串行）；
- 文件锁超时（AccountLockTimeout）时，写入失败记录并跳过子进程，不静默；
- 互斥期间 async_run_task_cli 不会与锁持有方并发执行。
"""

import asyncio
import subprocess
import sys
from pathlib import Path

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
from backend.services.tasks import run_task_once
from backend.utils import account_locks


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
    account_locks._ACCOUNT_LOCKS.clear()
    account_locks._LOCK_DIR = None
    tasks_mod._active_tasks.clear()
    tasks_mod._active_logs.clear()
    monkeypatch.delenv("ACCOUNT_LOCK_FILE", raising=False)
    monkeypatch.delenv("ACCOUNT_LOCK_TIMEOUT", raising=False)


def _make_task(db, account_name: str = "acct_lock_test", task_name: str = "task1"):
    account = (
        db.query(Account).filter(Account.account_name == account_name).first()
    )
    if not account:
        account = Account(
            account_name=account_name,
            api_id="12345",
            api_hash="a" * 32,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    task = Task(name=task_name, cron="0 9 * * *", enabled=True, account_id=account.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_run_task_once_waits_for_same_account_lock(db_session, monkeypatch):
    """同账号锁被占用时，run_task_once 等待锁释放后才执行子进程。"""
    task = _make_task(db_session)
    lock = account_locks.get_account_lock("acct_lock_test")

    calls: list[str] = []

    async def fake_cli(**kwargs):
        # 记录调用时锁的占用状态
        calls.append(("cli_start", lock.locked()))
        await asyncio.sleep(0.05)
        calls.append(("cli_end", lock.locked()))
        return (0, "done", "")

    monkeypatch.setattr(tasks_mod, "async_run_task_cli", fake_cli)

    async def hold_lock():
        async with lock:
            await asyncio.sleep(0.3)

    async def main():
        # 先在测试协程持有同账号锁
        holder = asyncio.create_task(hold_lock())
        await asyncio.sleep(0.05)  # 确保锁已被持有
        log = await run_task_once(db_session, task)
        assert log.status in ("success", "failed")
        await holder
        # run_task_once 执行期间锁是被占用的（互斥），子进程调用点也持锁
        cli_calls = [c for c in calls if c[0] == "cli_start"]
        assert cli_calls, "async_run_task_cli 应被执行"
        assert cli_calls[0][1] is True

    asyncio.run(main())


def test_run_task_once_writes_failure_on_lock_timeout(db_session, monkeypatch, tmp_path):
    """文件锁超时：写入失败记录并跳过子进程，不静默跳过。"""
    task = _make_task(db_session)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(exist_ok=True)
    account_locks._LOCK_DIR = lock_dir
    monkeypatch.setenv("ACCOUNT_LOCK_TIMEOUT", "0.3")

    lock = account_locks.get_account_lock("acct_lock_test")
    assert lock._lock_path is not None

    cli_called = []

    async def fake_cli(**kwargs):
        cli_called.append(True)
        return (0, "should not run", "")

    monkeypatch.setattr(tasks_mod, "async_run_task_cli", fake_cli)

    # 子进程持有文件锁
    holder_code = (
        "import fcntl,os,sys,time\n"
        "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "time.sleep(float(sys.argv[2]))\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(lock._lock_path), "5.0"],
    )
    try:
        # 等待 holder 就绪（拿到锁）
        import time as _time

        deadline = _time.monotonic() + 5
        while not _probe_held(lock._lock_path):
            if _time.monotonic() > deadline:
                raise TimeoutError("holder 未就绪")
            _time.sleep(0.02)

        async def main():
            log = await run_task_once(db_session, task)
            return log

        log = asyncio.run(main())
        assert log.status == "failed"
        assert "Account lock timeout" in (log.output or "")
        assert not cli_called, "锁超时时不应启动子进程"
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def _probe_held(lock_file: Path, timeout: float = 5.0) -> bool:
    code = (
        "import fcntl,os,sys\n"
        "fd=os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('free')\n"
        "except OSError:\n"
        "    print('held')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(lock_file)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip() == "held"
