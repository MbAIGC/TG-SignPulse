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


@pytest.mark.asyncio
async def test_run_status_init_failure_releases_active_state(
    sign_task_service, monkeypatch
):
    svc = sign_task_service

    def fail_status(*args, **kwargs):
        raise OSError("status storage unavailable")

    monkeypatch.setattr(svc, "_set_run_status", fail_status)

    with pytest.raises(OSError, match="status storage unavailable"):
        await svc.run_task_with_logs("acct_init", "task_init")

    assert svc.is_task_running("task_init", account_name="acct_init") is False
    assert ("acct_init", "task_init") not in svc._active_logs


@pytest.mark.asyncio
async def test_cancel_task_run_persists_cancelled_status(
    sign_task_service, monkeypatch
):
    svc = sign_task_service
    monkeypatch.setattr(svc, "get_task", lambda *args, **kwargs: {"name": "task"})

    async def wait_for_cancel(*args, **kwargs):
        import asyncio

        await asyncio.sleep(60)
        return {"success": True, "error": "", "output": ""}

    monkeypatch.setattr(svc, "run_task_with_logs", wait_for_cancel)
    started = await svc.start_task_run("acct_cancel", "task")
    cancelled = await svc.cancel_task_run(
        "acct_cancel", "task", run_id=started["run_id"]
    )

    assert cancelled["state"] == "cancelled"
    assert cancelled["success"] is False
    persisted = svc._run_state_store.get(started["run_id"])
    assert persisted is not None
    assert persisted["state"] == "cancelled"


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
    # Simulate a real restart: the original process no longer exists.
    stored = svc._run_state_store.get(run_id)
    assert stored is not None
    stored["worker_id"] = "99999999@missing-worker"
    svc._run_state_store.save(stored)

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


def test_cleanup_old_logs_removes_stale_sqlite_entries(sign_task_service, tmp_path):
    """_cleanup_old_logs 应同时清理 SQLite 主存储中超过 3 天的条目。"""
    svc = sign_task_service
    store = svc._run_history_store

    # 直接写入 SQLite：一条旧、一条新（跳过 _save_run_info 的时间生成）
    store.save_entry(
        task_name="task_clean",
        account_name="acct_clean",
        entry={
            "time": "2020-01-01T00:00:00",
            "success": True,
            "message": "old",
            "account_name": "acct_clean",
            "run_id": "o" * 32,
        },
        max_entries=100,
    )
    store.save_entry(
        task_name="task_clean",
        account_name="acct_clean",
        entry={
            "time": "2999-01-01T00:00:00",
            "success": True,
            "message": "new",
            "account_name": "acct_clean",
            "run_id": "n" * 32,
        },
        max_entries=100,
    )

    svc._cleanup_old_logs()

    remaining = store.load_entries(task_name="task_clean", account_name="acct_clean")
    assert [e["run_id"] for e in remaining] == ["n" * 32]


# 子进程脚本：在指定 workdir 写入 running 状态后直接退出（模拟运行中被中断）。
_WORKER_WRITE_RUNNING = r"""
import os, sys
from pathlib import Path

workdir, account, task, run_id = sys.argv[1:5]
sys.path.insert(0, os.path.abspath("."))

from backend.core.config import Settings
Settings.resolve_workdir = lambda self: Path(workdir)

from backend.services.sign_tasks import SignTaskService
svc = SignTaskService()
svc._set_run_status(
    account, task,
    run_id=run_id,
    state="running",
    success=None,
    error="",
    output="",
)
# 不写终态，直接退出（进程中断）
"""


def test_cross_process_interrupted_running_marked_cancelled_after_restart(
    tmp_path, monkeypatch
):
    """真跨进程：子进程写 running 后中断，父进程重启实例应恢复为 cancelled。"""
    import subprocess
    import sys as _sys

    from backend.core.config import Settings

    workdir = tmp_path / "workdir"
    account = "acct_crossproc"
    task = "task_crossproc"
    run_id = "c" * 32

    # 子进程写入 running 状态
    result = subprocess.run(
        [
            _sys.executable,
            "-c",
            _WORKER_WRITE_RUNNING,
            str(workdir),
            account,
            task,
            run_id,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr}"

    # 状态文件已由子进程落盘
    status_file = workdir / "history" / "_run_status" / f"{account}__{task}.json"
    assert status_file.exists(), "子进程应已写盘 running 状态"

    # 父进程模拟重启：同一 workdir 新建实例，从磁盘恢复
    monkeypatch.setattr(
        Settings,
        "resolve_workdir",
        lambda self: workdir,
        raising=False,
    )
    import backend.services.sign_tasks as st

    st._sign_task_service = None
    svc = SignTaskService()
    restored = svc.get_task_run_status(account, task, run_id=run_id)
    assert restored["state"] == "cancelled"
    assert restored["success"] is False
    assert "重启" in restored["error"] or "中断" in restored["error"]
    # 说明：_load_persisted_run_statuses 将磁盘 running 解释为 cancelled 只改内存；
    # 磁盘保留最近运行态（running），下次启动仍会被正确解释为中断。
