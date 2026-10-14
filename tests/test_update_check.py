"""版本与更新检查：只读、带缓存、禁用开关、失败可见。"""

import pytest
from fastapi.testclient import TestClient

from backend.core.auth import create_access_token
from backend.core.database import Base, ensure_schema_upgrades, get_db, get_engine
from backend.core.security import hash_password
from backend.main import app
from backend.models.user import User
from backend.services import update_check


@pytest.fixture
def client_with_user():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    db = next(get_db())
    user = db.query(User).filter(User.username == "update_user").first()
    if not user:
        user = User(
            username="update_user",
            password_hash=hash_password("ValidPassword123!"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(data={"sub": user.username})
    yield TestClient(app), {"Authorization": f"Bearer {token}"}, user, db


@pytest.fixture(autouse=True)
def clear_update_cache():
    update_check._cache["checked_at"] = 0.0
    update_check._cache["payload"] = None
    yield
    update_check._cache["checked_at"] = 0.0
    update_check._cache["payload"] = None


def test_version_endpoint_requires_auth_and_returns_build_info(
    client_with_user, monkeypatch
):
    client, headers, _user, _db = client_with_user
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    monkeypatch.setenv("BUILD_DATE", "2026-10-14T00:00:00Z")

    assert client.get("/api/update/version").status_code in (401, 403)

    res = client.get("/api/update/version", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == update_check.app_version
    assert body["build_sha"] == "abc1234"


def test_update_status_detects_newer_version(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user

    def fake_release(_repo):
        return {
            "tag": "v99.0.0",
            "html_url": "https://example.com/release",
            "published_at": "2026-10-01T00:00:00Z",
            "name": "v99.0.0",
            "body": "notes",
        }

    monkeypatch.setattr(update_check, "_fetch_latest_release", fake_release)

    res = client.get("/api/update/status?refresh=1", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["latest"] == "99.0.0"
    assert body["update_available"] is True
    assert body["release_url"] == "https://example.com/release"
    assert body["error"] == ""


def test_update_status_uses_cache_until_refresh(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    calls = {"count": 0}

    def fake_release(_repo):
        calls["count"] += 1
        return {
            "tag": "v0.0.1",
            "html_url": "",
            "published_at": "",
            "name": "",
            "body": "",
        }

    monkeypatch.setattr(update_check, "_fetch_latest_release", fake_release)

    client.get("/api/update/status", headers=headers)
    client.get("/api/update/status", headers=headers)
    assert calls["count"] == 1

    client.get("/api/update/status?refresh=1", headers=headers)
    assert calls["count"] == 2


def test_update_status_can_be_disabled(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    monkeypatch.setenv("APP_UPDATE_CHECK", "0")
    called = {"count": 0}

    def fake_release(_repo):
        called["count"] += 1
        return {
            "tag": "v1.0.0",
            "html_url": "",
            "published_at": "",
            "name": "",
            "body": "",
        }

    monkeypatch.setattr(update_check, "_fetch_latest_release", fake_release)

    res = client.get("/api/update/status?refresh=1", headers=headers)

    assert res.status_code == 200
    assert res.json()["disabled"] is True
    assert called["count"] == 0


def test_update_status_reports_failure_without_raising(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user

    def boom(_repo):
        raise RuntimeError("网络不可达")

    monkeypatch.setattr(update_check, "_fetch_latest_release", boom)

    res = client.get("/api/update/status?refresh=1", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["update_available"] is False
    assert "网络不可达" in body["error"]
