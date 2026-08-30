from backend.services.run_state_store import RunStateStore


def test_run_state_store_upserts_and_reads_by_run_id(tmp_path):
    store = RunStateStore(tmp_path)
    status = {
        "run_id": "r1",
        "account_name": "acct",
        "task_name": "task",
        "source": "api",
        "state": "running",
        "success": None,
        "error": "",
        "output": "",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": None,
        "worker_id": "worker-1",
    }
    store.save(status)
    status["state"] = "cancelled"
    status["success"] = False
    status["finished_at"] = "2026-01-01T00:00:01Z"
    store.save(status)

    loaded = store.get("r1")
    assert loaded is not None
    assert loaded["state"] == "cancelled"
    assert loaded["success"] is False
    assert store.latest_states()[0]["run_id"] == "r1"
