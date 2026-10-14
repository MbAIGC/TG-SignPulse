"""任务启用/停用：停用只停调度与调度状态，不删除配置与历史。"""

import json

import pytest

from backend.services.sign_tasks import SignTaskService


@pytest.fixture
def service(monkeypatch, tmp_path):
    from backend.core.config import Settings

    monkeypatch.setattr(Settings, "resolve_workdir", lambda self: tmp_path / "workdir")
    svc = SignTaskService()
    return svc


def _write_task(service, account: str, task: str, **extra) -> None:
    task_dir = service.signs_dir / account / task
    task_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "_version": 4,
        "account_name": account,
        "account_names": [account],
        "sign_at": "09:00",
        "random_seconds": 0,
        "sign_interval": 1,
        "daily_times": 1,
        "chats": [{"chat_id": -100123, "actions": [{"action": 1, "text": "hi"}]}],
        "execution_mode": "fixed",
        "range_start": "",
        "range_end": "",
        "notify_on_failure": True,
        "enabled": True,
    }
    config.update(extra)
    (task_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )


def _read_task_config(service, account: str, task: str) -> dict:
    return json.loads(
        (service.signs_dir / account / task / "config.json").read_text(encoding="utf-8")
    )


def test_disable_task_stops_schedule_and_persists_flag(service, monkeypatch):
    _write_task(service, "acct", "daily")
    calls = []
    monkeypatch.setattr(
        "backend.scheduler.add_or_update_sign_task_job",
        lambda *a, **k: calls.append(("add", a, k)),
    )
    monkeypatch.setattr(
        "backend.scheduler.remove_sign_task_job",
        lambda *a, **k: calls.append(("remove", a, k)),
    )

    service.set_task_enabled("daily", False, account_name="acct")

    assert _read_task_config(service, "acct", "daily")["enabled"] is False
    assert [item[0] for item in calls] == ["remove"]
    # 配置文件仍在（停用不等于删除）
    assert (service.signs_dir / "acct" / "daily" / "config.json").exists()


def test_enable_task_restores_schedule(service, monkeypatch):
    _write_task(service, "acct", "daily", enabled=False)
    calls = []
    monkeypatch.setattr(
        "backend.scheduler.add_or_update_sign_task_job",
        lambda *a, **k: calls.append(("add", a, k)),
    )
    monkeypatch.setattr(
        "backend.scheduler.remove_sign_task_job",
        lambda *a, **k: calls.append(("remove", a, k)),
    )

    service.set_task_enabled("daily", True, account_name="acct")

    assert _read_task_config(service, "acct", "daily")["enabled"] is True
    assert [item[0] for item in calls] == ["add"]
    # 重新启用时按原计划时间注册
    assert calls[0][1][2] == "09:00"


def test_disabled_task_is_reported_as_disabled(service, monkeypatch):
    _write_task(service, "acct", "daily", enabled=False)

    task = service.get_task("daily", account_name="acct")

    assert task is not None
    assert task["enabled"] is False


def test_set_task_enabled_rejects_unknown_task(service, monkeypatch):
    with pytest.raises(ValueError):
        service.set_task_enabled("missing", False, account_name="acct")
