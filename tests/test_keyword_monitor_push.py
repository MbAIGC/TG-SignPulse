"""关键词命中推送：失败必须写入规则日志，且不阻断后续动作。"""

from types import SimpleNamespace

import pytest

from backend.services import keyword_monitor as monitor_mod
from backend.services.keyword_monitor import KeywordMonitorService


def _rule():
    return SimpleNamespace(
        account_name="acct_push",
        task_name="task_push",
        action={"bark_url": "https://example.com/bark", "custom_url": ""},
    )


@pytest.mark.asyncio
async def test_push_failure_is_logged_and_not_raised(monkeypatch):
    service = KeywordMonitorService()
    rule = _rule()

    async def failing_push(*_args, **_kwargs):
        raise RuntimeError("bark 服务不可用")

    monkeypatch.setattr(monitor_mod, "send_keyword_push", failing_push)

    # 不应抛出异常：推送失败不能中断该规则的后续动作
    await service._push_keyword_match(
        rule=rule,
        push_channel="bark",
        global_settings={},
        payload={"title": "t", "body": "b"},
    )

    logs = service.get_task_logs("task_push", "acct_push")
    assert any("关键词命中通知发送失败" in line for line in logs)
    assert any("bark 服务不可用" in line for line in logs)


@pytest.mark.asyncio
async def test_push_success_is_logged(monkeypatch):
    service = KeywordMonitorService()
    rule = _rule()
    sent = []

    async def ok_push(settings, payload):
        sent.append((settings, payload))

    monkeypatch.setattr(monitor_mod, "send_keyword_push", ok_push)

    await service._push_keyword_match(
        rule=rule,
        push_channel="bark",
        global_settings={"global_proxy": "socks5://127.0.0.1:1080"},
        payload={"title": "t", "body": "b"},
    )

    assert sent and sent[0][0]["keyword_monitor_push_channel"] == "bark"
    assert sent[0][0]["keyword_monitor_bark_url"] == "https://example.com/bark"
    logs = service.get_task_logs("task_push", "acct_push")
    assert any("关键词命中通知已处理" in line for line in logs)
