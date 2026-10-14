"""关键词监听看门狗：真实 RPC 探测、避开签到锁、连续失败才重建、失败退避。"""

import asyncio

import pytest
from pyrogram import errors

from backend.services.keyword_monitor import (
    MONITOR_HEALTH_FAILURE_THRESHOLD,
    KeywordMonitorRule,
    KeywordMonitorService,
)
from backend.utils.account_locks import get_account_lock


def _rule(account_name: str = "acct_wd") -> KeywordMonitorRule:
    return KeywordMonitorRule(
        account_name=account_name,
        task_name="task_wd",
        chat_id=-100123,
        chat_name="chat",
        message_thread_id=None,
        action={"keywords": "签到"},
    )


class _FakeClient:
    def __init__(self, error: Exception | None = None):
        self.is_connected = True
        self.is_initialized = True
        self._tg_signpulse_no_updates = False
        self._error = error
        self.get_me_calls = 0

    async def get_me(self):
        self.get_me_calls += 1
        if self._error is not None:
            raise self._error
        return {"id": 1}


def _service_with(client: _FakeClient, account: str = "acct_wd"):
    service = KeywordMonitorService()
    service._rules = [_rule(account)]
    service._handler_refs = [(account, client, ("handler",))]
    return service


@pytest.mark.asyncio
async def test_probe_skips_account_busy_with_signing(monkeypatch):
    client = _FakeClient()
    service = _service_with(client)
    restarts = []

    async def fake_restart():
        restarts.append(1)

    monkeypatch.setattr(service, "restart_from_tasks", fake_restart)

    lock = get_account_lock("acct_wd")
    await lock.acquire()
    try:
        await service._watchdog_tick()
    finally:
        lock.release()

    # 账号被签到任务占用时不探测，也不触发重启
    assert client.get_me_calls == 0
    assert restarts == []


@pytest.mark.asyncio
async def test_restart_only_after_consecutive_failures(monkeypatch):
    client = _FakeClient(error=OSError("connection reset"))
    service = _service_with(client)
    restarts = []

    async def fake_restart():
        restarts.append(1)

    monkeypatch.setattr(service, "restart_from_tasks", fake_restart)

    await service._watchdog_tick()
    assert client.get_me_calls == 1
    assert restarts == []

    await service._watchdog_tick()
    assert client.get_me_calls == 2
    assert restarts == [1]
    assert MONITOR_HEALTH_FAILURE_THRESHOLD == 2


@pytest.mark.asyncio
async def test_healthy_probe_resets_failure_counter(monkeypatch):
    client = _FakeClient(error=OSError("boom"))
    service = _service_with(client)
    restarts = []

    async def fake_restart():
        restarts.append(1)

    monkeypatch.setattr(service, "restart_from_tasks", fake_restart)

    await service._watchdog_tick()
    assert service._health_failures["acct_wd"] == 1

    client._error = None
    await service._watchdog_tick()
    assert service._health_failures["acct_wd"] == 0

    client._error = OSError("boom")
    await service._watchdog_tick()
    assert restarts == []


@pytest.mark.asyncio
async def test_flood_wait_counts_as_reachable(monkeypatch):
    client = _FakeClient(error=errors.FloodWait(42))
    service = _service_with(client)
    restarts = []

    async def fake_restart():
        restarts.append(1)

    monkeypatch.setattr(service, "restart_from_tasks", fake_restart)

    await service._watchdog_tick()
    await service._watchdog_tick()

    # 限流响应说明服务可达，不应触发重建
    assert service._health_failures["acct_wd"] == 0
    assert restarts == []


@pytest.mark.asyncio
async def test_stop_does_not_cancel_running_watchdog():
    service = KeywordMonitorService()
    current = asyncio.current_task()
    service._watchdog_task = current

    # 看门狗自身触发 stop() 时不能取消自己，否则重启会被打断
    await service.stop()

    assert not current.cancelled()
    assert service._watchdog_task is current


@pytest.mark.asyncio
async def test_stop_cancels_external_watchdog():
    service = KeywordMonitorService()
    started = asyncio.Event()

    async def loop():
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(loop())
    service._watchdog_task = task
    await started.wait()

    await service.stop()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert service._watchdog_task is None
