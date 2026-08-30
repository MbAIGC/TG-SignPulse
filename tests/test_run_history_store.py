"""回归测试：5.4 —— SignTask 运行历史统一到 SQLite 主存储。

覆盖：
- save_entry / load_entries 的 SQLite 读写；
- 历史条目字段与 JSON 兼容（_load_history_entries 优先 SQLite）；
- 超 max_entries 截断保留最新；
- delete_entry / clear 的删除与同步。
"""

import json

import pytest

from backend.services.run_history import RunHistoryStore, get_run_history_store


@pytest.fixture
def store(tmp_path):
    return RunHistoryStore(tmp_path / "workdir")


def _entry(time: str, success: bool = True, run_id: str = "r1"):
    return {
        "time": time,
        "success": success,
        "message": "ok" if success else "fail",
        "account_name": "acct",
        "run_id": run_id,
        "flow_logs": ["line1", "line2"],
        "flow_truncated": False,
        "flow_line_count": 2,
        "last_target_message": "target",
    }


def test_save_and_load_entries(store):
    store.save_entry(
        task_name="task1", account_name="acct", entry=_entry("2026-09-01T10:00:00"), max_entries=100
    )
    entries = store.load_entries(task_name="task1", account_name="acct")
    assert len(entries) == 1
    e = entries[0]
    assert e["success"] is True
    assert e["run_id"] == "r1"
    assert e["flow_logs"] == ["line1", "line2"]
    assert e["last_target_message"] == "target"


def test_save_truncates_to_max_entries(store):
    for i in range(10):
        store.save_entry(
            task_name="task1",
            account_name="acct",
            entry=_entry(f"2026-09-01T10:{i:02d}:00", run_id=f"r{i}"),
            max_entries=3,
        )
    entries = store.load_entries(task_name="task1")
    assert len(entries) == 3
    # 保留的是最新（time 最大）的 3 条
    assert entries[0]["run_id"] == "r9"


def test_load_respects_account_filter(store):
    store.save_entry(task_name="task1", account_name="acctA", entry=_entry("t1"), max_entries=100)
    store.save_entry(task_name="task1", account_name="acctB", entry=_entry("t2"), max_entries=100)
    only_a = store.load_entries(task_name="task1", account_name="acctA")
    assert len(only_a) == 1
    assert only_a[0]["account_name"] == "acctA"


def test_delete_entry(store):
    store.save_entry(task_name="task1", account_name="acct", entry=_entry("t1"), max_entries=100)
    store.save_entry(task_name="task1", account_name="acct", entry=_entry("t2"), max_entries=100)
    removed = store.delete_entry(task_name="task1", account_name="acct", time="t1")
    assert removed == 1
    remaining = store.load_entries(task_name="task1", account_name="acct")
    assert [e["time"] for e in remaining] == ["t2"]


def test_clear(store):
    store.save_entry(task_name="task1", account_name="acct", entry=_entry("t1"), max_entries=100)
    store.save_entry(task_name="task2", account_name="acct", entry=_entry("t2"), max_entries=100)
    assert store.clear() == 2
    assert store.total_entries() == 0


def test_delete_older_than(store):
    """按 time（ISO 字典序）删除早于阈值的条目。"""
    store.save_entry(
        task_name="task1", account_name="acct",
        entry=_entry("2026-01-01T00:00:00", run_id="old"), max_entries=100,
    )
    store.save_entry(
        task_name="task1", account_name="acct",
        entry=_entry("2026-01-02T00:00:00", run_id="new"), max_entries=100,
    )
    removed = store.delete_older_than(before_iso="2026-01-01T12:00:00")
    assert removed == 1
    remaining = store.load_entries(task_name="task1", account_name="acct")
    assert [e["run_id"] for e in remaining] == ["new"]


def test_delete_older_than_noop_when_none(store):
    store.save_entry(task_name="task1", account_name="acct", entry=_entry("t1"), max_entries=100)
    assert store.delete_older_than(before_iso="0001-01-01T00:00:00") == 0
    assert store.total_entries() == 1


def test_store_shared_by_workdir(tmp_path):
    """同一 workdir 复用同一 store（单例），避免多连接锁竞争。"""
    s1 = get_run_history_store(tmp_path / "workdir")
    s2 = get_run_history_store(tmp_path / "workdir")
    assert s1 is s2
    s1.close()


def test_json_compat_load_fallback(tmp_path, monkeypatch):
    """SQLite 无数据时，_load_history_entries 回退 JSON 历史文件（迁移兼容）。"""
    from backend.core.config import Settings
    from backend.services.sign_tasks import SignTaskService

    monkeypatch.setattr(
        Settings,
        "resolve_workdir",
        lambda self: tmp_path / "workdir",
        raising=False,
    )
    svc = SignTaskService()
    try:
        # 先写 JSON 历史（模拟旧版本遗留数据），再验证 SQLite 空时能读到 JSON
        history_dir = svc.run_history_dir
        history_dir.mkdir(parents=True, exist_ok=True)
        legacy = history_dir / "task1.json"
        legacy.write_text(
            json.dumps(
                [
                    {
                        "time": "2026-01-01T00:00:00",
                        "success": True,
                        "message": "legacy",
                        "account_name": "acct",
                        "run_id": "legacy1",
                        "flow_logs": ["old"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        entries = svc._load_history_entries("task1", account_name="acct")
        assert entries and entries[0]["run_id"] == "legacy1"
    finally:
        import backend.services.sign_tasks as st

        st._sign_task_service = None
