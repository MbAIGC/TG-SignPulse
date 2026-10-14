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


def test_delete_older_than_prunes_finished_runs(tmp_path):
    """回归：task_runs 只增不删会让启动聚合越来越慢，必须支持按时间清理。"""
    store = RunStateStore(tmp_path)

    def _status(run_id: str, started_at: str) -> dict:
        return {
            "run_id": run_id,
            "account_name": "acct",
            "task_name": "task",
            "source": "api",
            "state": "finished",
            "success": True,
            "started_at": started_at,
        }

    store.save(_status("old-1", "2026-01-01T00:00:00+00:00"))
    store.save(_status("old-2", "2026-01-02T00:00:00+00:00"))
    store.save(_status("new-1", "2026-02-01T00:00:00+00:00"))

    removed = store.delete_older_than("2026-01-15T00:00:00+00:00")

    assert removed == 2
    assert store.get("old-1") is None
    assert store.get("old-2") is None
    assert store.get("new-1") is not None
    # 清理后仍能正常聚合最新状态
    assert {item["run_id"] for item in store.latest_states()} == {"new-1"}
