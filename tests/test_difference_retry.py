"""差异同步重试：瞬时失败要重试，限流要遵守等待，彻底失败必须留下 ERROR。"""

import asyncio
import logging

import pytest
from pyrogram import errors, raw

import tg_signer.core as core


def _difference_query() -> raw.functions.updates.GetDifference:
    return raw.functions.updates.GetDifference(pts=100, date=0, qts=-1)


class _FakeDiff:
    def __init__(self):
        self.state = "final"


@pytest.fixture
def no_sleep(monkeypatch):
    async def _noop(_seconds):
        return None

    monkeypatch.setattr(core.asyncio, "sleep", _noop)


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds(monkeypatch, no_sleep):
    calls = {"count": 0}
    expected = _FakeDiff()

    async def fake_invoke(_client, _query, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise asyncio.TimeoutError("request timed out")
        return expected

    monkeypatch.setattr(core, "_original_invoke", fake_invoke)
    monkeypatch.setattr(core, "_DIFFERENCE_MAX_RETRIES", 3)

    result = await core._patched_invoke(object(), _difference_query())

    assert result is expected
    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_permanent_failure_returns_fallback_and_logs_error(
    monkeypatch, no_sleep, caplog
):
    async def always_fail(_client, _query, *_args, **_kwargs):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(core, "_original_invoke", always_fail)
    monkeypatch.setattr(core, "_DIFFERENCE_MAX_RETRIES", 2)

    with caplog.at_level(logging.ERROR, logger="tg-signer"):
        result = await core._patched_invoke(object(), _difference_query())

    # 兜底响应保证 SDK 不崩，但必须留下可见的错误日志
    assert result is not None
    assert any("可能丢失消息" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_flood_wait_beyond_cap_gives_up_without_violating_limit(
    monkeypatch, caplog
):
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(core.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(core, "_DIFFERENCE_MAX_WAIT", 30.0)
    calls = {"count": 0}

    async def flood(_client, _query, *_args, **_kwargs):
        calls["count"] += 1
        raise errors.FloodWait(120)

    monkeypatch.setattr(core, "_original_invoke", flood)

    with caplog.at_level(logging.ERROR, logger="tg-signer"):
        await core._patched_invoke(object(), _difference_query())

    # 需要等待 120s > 上限 30s：直接放弃，不能缩短等待去违规重试
    assert calls["count"] == 1
    assert sleeps == []
    assert any("超过上限" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_flood_wait_within_cap_is_respected(monkeypatch):
    sleeps = []

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(core.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(core, "_DIFFERENCE_MAX_WAIT", 30.0)
    calls = {"count": 0}

    async def flood_then_ok(_client, _query, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise errors.FloodWait(5)
        return _FakeDiff()

    monkeypatch.setattr(core, "_original_invoke", flood_then_ok)

    await core._patched_invoke(object(), _difference_query())

    assert calls["count"] == 2
    assert sleeps and sleeps[0] >= 5
