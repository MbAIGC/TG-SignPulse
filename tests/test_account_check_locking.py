import asyncio

import pytest

from backend.services.sign_tasks import SignTaskService


@pytest.mark.asyncio
async def test_sign_task_account_check_does_not_reenter_account_lock(
    monkeypatch, tmp_path
):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    service._account_locks["acct"] = asyncio.Lock()
    calls = []

    async def fake_check(account_name, task_name, **kwargs):
        calls.append(kwargs.get("lock_already_held", False))
        return "skip execution"

    monkeypatch.setattr(service, "_check_account_before_task", fake_check)
    monkeypatch.setattr(service, "get_task", lambda *args, **kwargs: {"name": "task"})

    result = await asyncio.wait_for(
        service.run_task_with_logs("acct", "task"), timeout=1
    )

    assert calls == [False]
    assert result["success"] is False


@pytest.mark.asyncio
async def test_db_task_account_check_marks_existing_lock(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    calls = []

    async def fake_check(account_name, task_name, **kwargs):
        calls.append(kwargs.get("lock_already_held", False))
        return "skip execution"

    monkeypatch.setattr(service, "_check_account_before_task", fake_check)
    monkeypatch.setattr(service, "get_task", lambda *args, **kwargs: {"name": "task"})

    result = await service.run_task_with_logs("acct", "task", lock_already_held=True)

    assert calls == [True]
    assert result["success"] is False
