"""删除账号与在跑任务互斥：账号被占用时返回 409，正常时释放锁。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import accounts as accounts_mod
from backend.core.auth import create_access_token
from backend.core.database import Base, ensure_schema_upgrades, get_db, get_engine
from backend.core.security import hash_password
from backend.main import app
from backend.models.user import User


@pytest.fixture
def client_with_user():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    db = next(get_db())

    user = db.query(User).filter(User.username == "del_test_user").first()
    if not user:
        user = User(
            username="del_test_user",
            password_hash=hash_password("ValidPassword123!"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = create_access_token(data={"sub": user.username})
    client = TestClient(app)
    yield client, {"Authorization": f"Bearer {token}"}, user, db


def test_delete_account_returns_conflict_when_account_is_busy(
    client_with_user, monkeypatch
):
    client, headers, _user, _db = client_with_user
    never = asyncio.Event()

    class _BusyLock:
        def acquire(self):
            return never.wait()

        def release(self):
            raise AssertionError("未获取到锁时不应释放")

    monkeypatch.setattr(accounts_mod, "get_account_lock", lambda _name: _BusyLock())
    monkeypatch.setattr(accounts_mod, "_DELETE_LOCK_WAIT_SECONDS", 0.05)

    res = client.delete("/api/accounts/busy_acct", headers=headers)

    assert res.status_code == 409
    assert "正在执行任务" in res.json()["detail"]


def test_delete_account_releases_lock_after_success(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    events = []

    class _RecordingLock:
        def __init__(self):
            self.ident = id(self)

        async def acquire(self):
            events.append(("acquire", self.ident))

        def release(self):
            events.append(("release", self.ident))

    class _FakeService:
        async def delete_account(self, account_name):
            events.append(("delete", account_name))
            return True

    monkeypatch.setattr(
        accounts_mod, "get_account_lock", lambda _name: _RecordingLock()
    )
    monkeypatch.setattr(accounts_mod, "get_telegram_service", lambda: _FakeService())

    res = client.delete("/api/accounts/free_acct", headers=headers)

    assert res.status_code == 200
    kinds = [kind for kind, _ in events]
    assert kinds == ["acquire", "delete", "release"]
    lock_ids = {value for kind, value in events if kind in {"acquire", "release"}}
    assert len(lock_ids) == 1  # 获取与释放的是同一把锁
