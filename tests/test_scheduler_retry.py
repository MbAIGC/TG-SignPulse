"""调度层失败重试：失败后一次性重试一次，且不覆盖/误删待执行的重试 job。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

import backend.scheduler as sched


class _StubSignService:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def get_task(self, task_name, account_name):
        return {"name": task_name, "account_name": account_name}

    async def run_task_with_logs(self, account_name, task_name, source=None):
        self.calls += 1
        return self.result


class _FakeQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []


class _FakeSession:
    def query(self, *_args, **_kwargs):
        return _FakeQuery()

    def close(self):
        pass


@pytest.fixture
def bare_scheduler(monkeypatch):
    scheduler = AsyncIOScheduler()
    monkeypatch.setattr(sched, "scheduler", scheduler)
    yield scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_failed_run_schedules_single_retry(monkeypatch, bare_scheduler):
    stub = _StubSignService({"success": False, "error": "网络超时"})
    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: stub
    )

    await sched._job_run_sign_task("acct", "task", 0, 1)

    job = bare_scheduler.get_job("sign-acct-task#0-retry")
    assert job is not None
    assert job.kwargs.get("is_retry") is True
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_retry_run_does_not_schedule_another_retry(monkeypatch, bare_scheduler):
    stub = _StubSignService({"success": False, "error": "仍然失败"})
    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: stub
    )

    await sched._job_run_sign_task("acct", "task", 0, 1, is_retry=True)

    assert bare_scheduler.get_job("sign-acct-task#0-retry") is None


@pytest.mark.asyncio
async def test_account_invalid_failure_is_not_retried(monkeypatch, bare_scheduler):
    stub = _StubSignService(
        {"success": False, "error": "登录已失效", "account_invalid": True}
    )
    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: stub
    )

    await sched._job_run_sign_task("acct", "task", 0, 1)

    assert bare_scheduler.get_job("sign-acct-task#0-retry") is None


def test_retry_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SIGN_TASK_RETRY_DELAY_SECONDS", "0")
    assert sched._retry_delay_seconds() == 0
    monkeypatch.setenv("SIGN_TASK_RETRY_DELAY_SECONDS", "abc")
    assert sched._retry_delay_seconds() == 600


@pytest.mark.asyncio
async def test_sync_jobs_keeps_pending_retry_for_active_task(
    monkeypatch, bare_scheduler
):
    class _StubTaskService:
        def _expand_wildcard_tasks(self):
            return None

        def list_tasks(self, force_refresh=False):
            return [
                {
                    "account_name": "acct",
                    "name": "task",
                    "sign_at": "09:00",
                    "enabled": True,
                    "daily_times": 1,
                    "execution_mode": "fixed",
                }
            ]

    monkeypatch.setattr(sched, "get_session_local", lambda: _FakeSession)
    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: _StubTaskService()
    )

    bare_scheduler.add_job(
        lambda: None,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(hours=1)),
        id="sign-acct-task#0-retry",
    )

    await sched.sync_jobs()

    assert bare_scheduler.get_job("sign-acct-task#0") is not None
    # 待执行的一次性重试 job 不能被同步清理误删
    assert bare_scheduler.get_job("sign-acct-task#0-retry") is not None


def _range_task(**overrides):
    config = {
        "name": "task",
        "account_name": "acct",
        "execution_mode": "range",
        "range_start": "09:00",
        "range_end": "10:00",
        "daily_times": 1,
    }
    config.update(overrides)
    return config


def _freeze_now(monkeypatch, moment):
    """把调度器眼中的“现在”固定到指定时刻。"""
    monkeypatch.setattr(sched, "_now", lambda: moment)


def _at(hour, minute=0):
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def test_range_catchup_scheduled_inside_window_without_today_run(
    monkeypatch, bare_scheduler
):
    _freeze_now(monkeypatch, _at(9, 30))

    sched.schedule_range_catchup("acct", "task", _range_task())

    job = bare_scheduler.get_job("sign-acct-task#0-catchup")
    assert job is not None
    assert job.kwargs.get("is_catchup") is True
    # 补执行时间必须落在窗口内（09:30 - 10:00）
    assert job.trigger.run_date <= _at(10, 0)


def test_range_catchup_skipped_when_already_ran_today(monkeypatch, bare_scheduler):
    _freeze_now(monkeypatch, _at(9, 30))
    today = _at(9, 30).date().isoformat()

    sched.schedule_range_catchup(
        "acct", "task", _range_task(last_run={"time": f"{today}T09:05:00+08:00"})
    )

    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is None


def test_range_catchup_skipped_outside_window(monkeypatch, bare_scheduler):
    # 窗口尚未开始
    _freeze_now(monkeypatch, _at(8, 30))
    sched.schedule_range_catchup("acct", "task", _range_task())
    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is None

    # 窗口已经结束
    _freeze_now(monkeypatch, _at(11, 0))
    sched.schedule_range_catchup("acct", "task", _range_task())
    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is None


def test_range_catchup_skipped_while_same_job_is_waiting(monkeypatch, bare_scheduler):
    _freeze_now(monkeypatch, _at(9, 30))
    # 模拟 cron 触发的任务正在窗口内随机等待
    sched._active_sign_jobs.add(("acct", "task", 0))
    try:
        sched.schedule_range_catchup("acct", "task", _range_task())
    finally:
        sched._active_sign_jobs.discard(("acct", "task", 0))

    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is None


def test_range_catchup_only_for_range_mode(monkeypatch, bare_scheduler):
    _freeze_now(monkeypatch, _at(9, 30))

    sched.schedule_range_catchup("acct", "task", _range_task(execution_mode="fixed"))

    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is None


@pytest.mark.asyncio
async def test_sync_jobs_keeps_pending_catchup_for_active_task(
    monkeypatch, bare_scheduler
):
    class _StubTaskService:
        def _expand_wildcard_tasks(self):
            return None

        def list_tasks(self, force_refresh=False):
            return [_range_task(sign_at="09:00", enabled=True)]

    monkeypatch.setattr(sched, "get_session_local", lambda: _FakeSession)
    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: _StubTaskService()
    )
    _freeze_now(monkeypatch, _at(9, 30))

    bare_scheduler.add_job(
        lambda: None,
        trigger=DateTrigger(run_date=datetime.now() + timedelta(hours=1)),
        id="sign-acct-task#0-catchup",
    )

    await sched.sync_jobs()

    assert bare_scheduler.get_job("sign-acct-task#0") is not None
    # 待执行的补执行 job 不能被同步清理误删
    assert bare_scheduler.get_job("sign-acct-task#0-catchup") is not None
