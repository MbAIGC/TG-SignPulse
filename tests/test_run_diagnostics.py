import asyncio
import os
import socket

import pytest

from backend.services.sign_tasks import SignTaskService


def test_recorded_worker_alive_for_current_process():
    status = {"worker_id": f"{os.getpid()}@{socket.gethostname()}"}
    assert SignTaskService._is_recorded_worker_alive(status) is True


def test_recorded_worker_missing_is_not_alive():
    status = {"worker_id": "99999999@other-host"}
    assert SignTaskService._is_recorded_worker_alive(status) is False


@pytest.mark.asyncio
async def test_start_task_runner_receives_reserved_execution_flag(
    monkeypatch, tmp_path
):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    monkeypatch.setattr(service, "get_task", lambda *args, **kwargs: {"name": "task"})
    received = {}

    async def fake_run(*args, **kwargs):
        received.update(kwargs)
        return {"success": True, "output": "done", "error": ""}

    monkeypatch.setattr(service, "run_task_with_logs", fake_run)
    await service.start_task_run("acct", "task")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert received["allow_active_task"] is True
    assert received["run_id"]
