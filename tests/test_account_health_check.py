"""账号健康巡检：跳过在跑任务的账号、失效账号走既有标记与通知去重。"""

import pytest

from backend.services.sign_tasks import SignTaskService
from backend.utils.account_locks import get_account_lock
from backend.utils.tg_session import get_account_status


class _FakeTelegramService:
    def __init__(self, accounts, results, fail_for=()):
        self._accounts = accounts
        self._results = results
        self._fail_for = set(fail_for)
        self.checked = []

    def list_accounts(self):
        return [{"name": name} for name in self._accounts]

    async def check_account_status(self, account_name, **_kwargs):
        self.checked.append(account_name)
        if account_name in self._fail_for:
            raise OSError("network down")
        return self._results.get(account_name, {"ok": True})


@pytest.fixture
def service(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    return SignTaskService()


@pytest.mark.asyncio
async def test_health_check_marks_invalid_and_notifies(monkeypatch, service):
    fake = _FakeTelegramService(
        accounts=["healthy", "broken"],
        results={
            "healthy": {"ok": True},
            "broken": {"needs_relogin": True, "message": "Session 已失效"},
        },
    )
    monkeypatch.setattr("backend.services.telegram.get_telegram_service", lambda: fake)
    notified = []

    async def fake_notify(**kwargs):
        notified.append(kwargs["account_name"])

    monkeypatch.setattr(service, "_send_account_invalid_notification", fake_notify)

    summary = await service.health_check_accounts()

    assert summary["checked"] == 2
    assert summary["invalid"] == ["broken"]
    assert notified == ["broken"]
    status = get_account_status("broken")
    assert status.get("needs_relogin") is True
    # 第二次巡检不重复通知（invalid_notified_at 去重）
    notified.clear()
    await service.health_check_accounts()
    assert notified == []


@pytest.mark.asyncio
async def test_health_check_skips_account_busy_with_task(monkeypatch, service):
    fake = _FakeTelegramService(
        accounts=["busy", "idle"], results={"idle": {"ok": True}}
    )
    monkeypatch.setattr("backend.services.telegram.get_telegram_service", lambda: fake)

    lock = get_account_lock("busy")
    await lock.acquire()
    try:
        summary = await service.health_check_accounts()
    finally:
        lock.release()

    assert summary["skipped"] == ["busy"]
    assert fake.checked == ["idle"]


@pytest.mark.asyncio
async def test_health_check_survives_single_account_error(monkeypatch, service):
    fake = _FakeTelegramService(
        accounts=["boom", "ok"],
        results={"ok": {"ok": True}},
        fail_for={"boom"},
    )
    monkeypatch.setattr("backend.services.telegram.get_telegram_service", lambda: fake)

    summary = await service.health_check_accounts()

    # 单个账号探测异常不影响其余账号
    assert summary["checked"] == 2
    assert summary["invalid"] == []
    assert fake.checked == ["boom", "ok"]


@pytest.mark.asyncio
async def test_health_check_endpoint_returns_summary(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.core.auth import create_access_token
    from backend.core.database import Base, ensure_schema_upgrades, get_db, get_engine
    from backend.core.security import hash_password
    from backend.main import app
    from backend.models.user import User

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    db = next(get_db())
    user = db.query(User).filter(User.username == "hc_user").first()
    if not user:
        user = User(
            username="hc_user", password_hash=hash_password("ValidPassword123!")
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    class _StubService:
        async def health_check_accounts(self):
            return {"checked": 2, "invalid": ["a"], "skipped": ["b"]}

    monkeypatch.setattr(
        "backend.services.sign_tasks.get_sign_task_service", lambda: _StubService()
    )
    token = create_access_token(data={"sub": user.username})
    client = TestClient(app)

    res = client.post(
        "/api/accounts/health-check", headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["checked"] == 2
    assert body["invalid"] == ["a"]
    assert body["skipped"] == ["b"]
