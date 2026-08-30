import asyncio

from backend.services.run_history import RunHistoryStore
from backend.services.sign_tasks import SignTaskService


def test_history_retention_is_isolated_by_account(tmp_path):
    store = RunHistoryStore(tmp_path / "workdir")
    for account in ("acct-a", "acct-b"):
        for index in range(2):
            store.save_entry(
                task_name="same-task",
                account_name=account,
                entry={
                    "time": f"2026-01-01T00:0{index}:00",
                    "run_id": f"{account}-{index}",
                },
                max_entries=1,
            )

    assert [
        e["run_id"]
        for e in store.load_entries(task_name="same-task", account_name="acct-a")
    ] == ["acct-a-1"]
    assert [
        e["run_id"]
        for e in store.load_entries(task_name="same-task", account_name="acct-b")
    ] == ["acct-b-1"]


def test_flow_log_limits_are_applied(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    monkeypatch.setenv("SIGN_TASK_HISTORY_MAX_FLOW_LINES", "20")
    monkeypatch.setenv("SIGN_TASK_HISTORY_MAX_LINE_CHARS", "80")
    service = SignTaskService()
    lines = ["x" * 81] + ["ok"] * 20

    normalized, truncated, count = service._normalize_flow_logs(lines)
    assert normalized[0] == "x" * 80
    assert len(normalized) == 20
    assert truncated is True
    assert count == 21


def test_history_delete_by_run_id_is_precise(tmp_path):
    store = RunHistoryStore(tmp_path / "workdir")
    for run_id in ("r1", "r2"):
        store.save_entry(
            task_name="task",
            account_name="acct",
            entry={"time": "same-time", "run_id": run_id},
            max_entries=10,
        )

    assert store.delete_entry(task_name="task", account_name="acct", run_id="r1") == 1
    assert [item["run_id"] for item in store.load_entries(task_name="task")] == ["r2"]


def test_json_history_is_imported_idempotently(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    history_file = service._history_file_path("task", "acct")
    history_file.write_text(
        '[{"time":"2026-01-01T00:00:00","success":true,"message":"old"}]',
        encoding="utf-8",
    )

    first = service._load_history_entries("task", account_name="acct")
    second = service._load_history_entries("task", account_name="acct")

    assert len(first) == len(second) == 1
    assert service._run_history_store.total_entries() == 1
    assert first[0]["run_id"].startswith("legacy-")


def test_concurrent_start_returns_same_run(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    monkeypatch.setattr(service, "get_task", lambda *args, **kwargs: {"name": "task"})
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(kwargs.get("run_id"))
        await asyncio.sleep(0.02)
        return {"success": True, "output": "", "error": ""}

    monkeypatch.setattr(service, "run_task_with_logs", fake_run)

    async def scenario():
        statuses = await asyncio.gather(
            service.start_task_run("acct", "task"),
            service.start_task_run("acct", "task"),
        )
        await asyncio.sleep(0.05)
        return statuses

    statuses = asyncio.run(scenario())
    assert statuses[0]["run_id"] == statuses[1]["run_id"]
    assert len(calls) == 1
