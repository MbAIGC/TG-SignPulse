import json

from backend.services.sign_tasks import SignTaskService


def test_json_history_merges_when_sqlite_already_has_entries(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    service._run_history_store.save_entry(
        task_name="task",
        account_name="acct",
        entry={"time": "2026-01-02T00:00:00+00:00", "run_id": "new"},
        max_entries=10,
    )
    service._history_file_path("task", "acct").write_text(
        json.dumps(
            [
                {
                    "time": "2026-01-01T00:00:00+00:00",
                    "run_id": "old",
                    "success": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    entries = service._load_history_entries("task", account_name="acct")

    assert [entry["run_id"] for entry in entries] == ["new", "old"]
    assert service._run_history_store.total_entries() == 2


def test_history_service_returns_run_id_for_precise_lookup(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    service = SignTaskService()
    service._run_history_store.save_entry(
        task_name="task",
        account_name="acct",
        entry={"time": "same-time", "run_id": "first"},
        max_entries=10,
    )
    service._run_history_store.save_entry(
        task_name="task",
        account_name="acct",
        entry={"time": "same-time", "run_id": "second"},
        max_entries=10,
    )
    monkeypatch.setattr(
        service,
        "list_tasks",
        lambda **kwargs: [{"name": "task", "account_name": "acct"}],
    )

    history = service.get_filtered_history_logs(account_name="acct")
    detail = service.get_history_log_detail("acct", "task", run_id="first")

    assert {entry["run_id"] for entry in history} == {"first", "second"}
    assert detail is not None
    assert detail["run_id"] == "first"
    assert service.delete_history_log("acct", "task", run_id="first")
    assert [
        entry["run_id"] for entry in service._load_history_entries("task", "acct")
    ] == ["second"]
