"""回归测试：第三档 9/10 —— run_id 唯一性 + 任务状态机持久化。

覆盖：
- start_task_run 每次生成唯一 run_id（history 条目含 run_id）
- _set_run_status 会把状态原子写盘，重启后可恢复
- 重启后 running 态被标记为 cancelled（进程中断）
- get_task_run_status 内存优先，磁盘回退一致
"""

import json

import pytest

from backend.services.sign_tasks import SignTaskService


@pytest.fixture
def sign_task_service(tmp_path, monkeypatch):
    """用临时 workdir 构造 SignTaskService，避免污染全局单例与真实数据目录。"""
    from backend.core.config import Settings

    monkeypatch.setattr(
        Settings,
        "resolve_workdir",
        lambda self: tmp_path / "workdir",
        raising=False,
    )
    svc = SignTaskService()
    yield svc
    # 清除单例，避免污染后续用例
    import backend.services.sign_tasks as st

    st._sign_task_service = None


def test_run_id_unique_and_persisted_in_history(sign_task_service):
    """每次运行生成唯一 run_id，并写入 history 条目。"""
    svc = sign_task_service
    account = "acct_runid"
    task = "task_runid"

    # 直调 run_task_with_logs 不会走 start_task_run 时，_save_run_info 也应生成 run_id
    # 这里直接验证 _save_run_info 生成的 history 条目带 run_id
    svc._save_run_info(
        task,
        success=True,
        message="ok",
        account_name=account,
        flow_logs=["line1"],
    )
    entries = svc._load_history_entries(task, account_name=account)
    assert entries, "history 应有条目"
    first = entries[0]
    assert first.get("run_id"), "history 条目必须带 run_id"
    assert len(first["run_id"]) == 32  # uuid4.hex

    # 两次写入产生不同 run_id
    svc._save_run_info(task, success=True, message="ok2", account_name=account)
    entries = svc._load_history_entries(task, account_name=account)
    assert entries[0]["run_id"] != entries[1]["run_id"]


def test_run_status_persisted_and_recovered_after_restart(sign_task_service, tmp_path):
    """状态机持久化：_set_run_status 写盘；新建服务实例（模拟重启）可恢复。"""
    svc = sign_task_service
    account = "acct_persist"
    task = "task_persist"

    run_id = "a" * 32
    svc._set_run_status(
        account,
        task,
        run_id=run_id,
        state="finished",
        success=True,
        error="",
        output="done",
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:00:01",
    )

    # 状态文件已落盘
    status_file = svc._run_status_file_path(account, task)
    assert status_file.exists(), "状态文件应已写盘"
    on_disk = json.loads(status_file.read_text(encoding="utf-8"))
    assert on_disk["run_id"] == run_id
    assert on_disk["state"] == "finished"

    # 模拟重启：构造全新实例（仍指向同一 workdir），从磁盘恢复
    import backend.services.sign_tasks as st

    st._sign_task_service = None
    # 重新构造：monkeypatch 的 resolve_workdir 仍生效（同一 tmp_path）
    svc2 = SignTaskService()
    assert svc2._run_statuses, "重启后应从磁盘恢复 run status"
    restored = svc2.get_task_run_status(account, task, run_id=run_id)
    assert restored["state"] == "finished"
    assert restored["success"] is True
    assert restored["run_id"] == run_id


def test_interrupted_running_marked_cancelled_after_restart(sign_task_service):
    """重启前 state=running 的进程中断态，恢复后应标记为 cancelled。"""
    svc = sign_task_service
    account = "acct_interrupt"
    task = "task_interrupt"

    run_id = "b" * 32
    svc._set_run_status(
        account,
        task,
        run_id=run_id,
        state="running",
        success=None,
        error="",
        output="",
    )
    status_file = svc._run_status_file_path(account, task)
    assert status_file.exists()

    # 模拟重启
    import backend.services.sign_tasks as st

    st._sign_task_service = None
    svc2 = SignTaskService()
    restored = svc2.get_task_run_status(account, task, run_id=run_id)
    assert restored["state"] == "cancelled"
    assert restored["success"] is False
    assert "重启" in restored["error"] or "中断" in restored["error"]


def test_get_run_status_idle_when_no_record(sign_task_service):
    """无任何运行记录时返回 idle。"""
    svc = sign_task_service
    status = svc.get_task_run_status("acct_none", "task_none")
    assert status["state"] == "idle"
    assert status["run_id"] == ""
