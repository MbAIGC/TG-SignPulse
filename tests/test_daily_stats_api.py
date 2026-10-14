"""30 天状态条接口：鉴权、参数范围与返回结构。"""

import pytest
from fastapi.testclient import TestClient

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
    user = db.query(User).filter(User.username == "stats_user").first()
    if not user:
        user = User(
            username="stats_user",
            password_hash=hash_password("ValidPassword123!"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(data={"sub": user.username})
    yield TestClient(app), {"Authorization": f"Bearer {token}"}, user, db


class _StubSignService:
    def __init__(self):
        self.calls = []

    def get_daily_stats(self, task_name="", account_name="", days=30):
        self.calls.append(
            {"task_name": task_name, "account_name": account_name, "days": days}
        )
        return {
            "days": [{"date": "2026-10-14", "success": 1, "failed": 0}],
            "success": 1,
            "failed": 0,
            "total": 1,
            "success_rate": 1.0,
        }


def test_stats_endpoints_require_auth(client_with_user):
    client, _headers, _user, _db = client_with_user

    assert client.get("/api/sign-tasks/stats/overall").status_code in (401, 403)
    assert client.get("/api/sign-tasks/daily/stats").status_code in (401, 403)


def test_overall_stats_endpoint(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    stub = _StubSignService()
    monkeypatch.setattr(
        "backend.api.routes.sign_tasks_v2.get_sign_task_service", lambda: stub
    )

    res = client.get("/api/sign-tasks/stats/overall?days=7", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["success_rate"] == 1.0
    assert body["days"][0]["date"] == "2026-10-14"
    assert stub.calls == [{"task_name": "", "account_name": "", "days": 7}]


def test_task_stats_endpoint_passes_account_and_days(client_with_user, monkeypatch):
    client, headers, _user, _db = client_with_user
    stub = _StubSignService()
    monkeypatch.setattr(
        "backend.api.routes.sign_tasks_v2.get_sign_task_service", lambda: stub
    )

    res = client.get(
        "/api/sign-tasks/daily/stats?days=30&account_name=acct", headers=headers
    )

    assert res.status_code == 200
    assert stub.calls == [{"task_name": "daily", "account_name": "acct", "days": 30}]


def test_overall_stats_rejects_out_of_range_days(client_with_user):
    client, headers, _user, _db = client_with_user

    res = client.get("/api/sign-tasks/stats/overall?days=365", headers=headers)

    assert res.status_code == 422
