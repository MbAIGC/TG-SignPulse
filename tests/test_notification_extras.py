"""通知扩展：任务成功通知开关、机器人测试发送。"""

import pytest
from fastapi.testclient import TestClient

from backend.core.auth import create_access_token
from backend.core.database import Base, ensure_schema_upgrades, get_db, get_engine
from backend.core.security import hash_password
from backend.main import app
from backend.models.user import User
from backend.services import push_notifications
from backend.services.sign_tasks import SignTaskService


@pytest.fixture
def client_with_user():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    db = next(get_db())
    user = db.query(User).filter(User.username == "notify_user").first()
    if not user:
        user = User(
            username="notify_user",
            password_hash=hash_password("ValidPassword123!"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(data={"sub": user.username})
    yield TestClient(app), {"Authorization": f"Bearer {token}"}, user, db


def _stub_config(monkeypatch, settings_dict):
    class _StubConfigService:
        def get_global_settings(self):
            return dict(settings_dict)

    monkeypatch.setattr(
        "backend.services.config.get_config_service", lambda: _StubConfigService()
    )


def test_test_bot_endpoint_requires_credentials(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user

    assert client.post("/api/config/telegram/test", json={}).status_code in (401, 403)

    res = client.post(
        "/api/config/telegram/test",
        json={"bot_token": "", "chat_id": ""},
        headers=headers,
    )
    assert res.status_code == 400
    assert "Bot Token" in res.json()["detail"]


def test_test_bot_endpoint_sends_message(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr(push_notifications, "send_telegram_bot_message", fake_send)

    res = client.post(
        "/api/config/telegram/test",
        json={"bot_token": "123:abc", "chat_id": "-100123", "message_thread_id": 7},
        headers=headers,
    )

    assert res.status_code == 200
    assert res.json()["success"] is True
    assert sent and sent[0]["bot_token"] == "123:abc"
    assert sent[0]["message_thread_id"] == 7


def test_test_bot_endpoint_reports_send_failure(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user

    async def boom(**_kwargs):
        raise RuntimeError("chat not found")

    monkeypatch.setattr(push_notifications, "send_telegram_bot_message", boom)

    res = client.post(
        "/api/config/telegram/test",
        json={"bot_token": "123:abc", "chat_id": "-100123"},
        headers=headers,
    )

    assert res.status_code == 400
    assert "chat not found" in res.json()["detail"]


@pytest.mark.asyncio
async def test_success_notification_respects_flag(monkeypatch):
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(push_notifications, "send_telegram_bot_message", fake_send)
    base = {
        "telegram_bot_notify_enabled": True,
        "telegram_bot_token": "123:abc",
        "telegram_bot_chat_id": "-100123",
    }
    service = SignTaskService()

    # 默认关闭：不发
    _stub_config(monkeypatch, {**base, "telegram_bot_task_success_enabled": False})
    await service._send_success_notification("acct", "task", "签到成功")
    assert sent == []

    # 开启后发送，且带上机器人回复
    _stub_config(monkeypatch, {**base, "telegram_bot_task_success_enabled": True})
    await service._send_success_notification("acct", "task", "签到成功")
    assert len(sent) == 1
    assert "签到成功" in sent[0]["text"]
    assert "acct" in sent[0]["text"]
