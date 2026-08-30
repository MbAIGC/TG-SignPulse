"""回归测试：5.5 —— 多 worker（多进程）并发执行同一账号任务互斥。

真实 Telegram 环境不可得，这里用「两个 worker 子进程各自走 run_task_once
的锁路径执行同一账号任务」验证：
- 跨进程文件锁保证同一账号在同一时刻只有一个 worker 在执行（串行）；
- 每个 worker 执行区间不重叠；
- 各自的 worker_id（pid@hostname）可区分执行来源。

边界说明：真实 Telegram 登录/多账号并发/反向代理 WebSocket 等需在目标环境
回归（见 doc/第三轮GPT-review.md 4.3 与部署验证清单）。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.utils import account_locks


@pytest.fixture(autouse=True)
def _reset_lock_state(monkeypatch):
    yield
    account_locks._ACCOUNT_LOCKS.clear()
    account_locks._LOCK_DIR = None
    monkeypatch.delenv("ACCOUNT_LOCK_FILE", raising=False)
    monkeypatch.delenv("ACCOUNT_LOCK_TIMEOUT", raising=False)


# 子进程 worker 脚本：获取账号锁 -> 记录 start/end 时间戳到共享文件 -> 释放
_WORKER_CODE = r"""
import asyncio, json, os, sys, time
from pathlib import Path

lock_dir = sys.argv[1]
account = sys.argv[2]
out_file = sys.argv[3]
hold = float(sys.argv[4])

sys.path.insert(0, os.path.abspath("."))
from backend.utils.account_locks import get_account_lock, set_account_lock_dir

os.makedirs(lock_dir, exist_ok=True)
set_account_lock_dir(Path(lock_dir))
lock = get_account_lock(account)

async def main():
    async with lock:
        t_start = time.time()
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "event": "start", "t": t_start}) + "\n")
        await asyncio.sleep(hold)
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "event": "end", "t": time.time()}) + "\n")

asyncio.run(main())
"""


def _parse_events(out_file: Path):
    events = []
    if out_file.exists():
        for line in out_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_two_worker_processes_same_account_serialized(tmp_path):
    """两个 worker 进程并发执行同一账号任务：执行区间不重叠（跨进程互斥）。"""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(exist_ok=True)
    account_locks._LOCK_DIR = lock_dir
    out_file = tmp_path / "events.jsonl"

    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _WORKER_CODE,
                str(lock_dir),
                "acct_multiworker",
                str(out_file),
                "0.4",
            ]
        )
        for _ in range(2)
    ]
    for p in procs:
        p.wait(timeout=15)

    events = _parse_events(out_file)
    starts = sorted(
        (e for e in events if e["event"] == "start"), key=lambda e: e["t"]
    )
    ends = sorted(
        (e for e in events if e["event"] == "end"), key=lambda e: e["t"]
    )
    assert len(starts) == 2 and len(ends) == 2, f"两个 worker 都应有 start/end: {events}"
    # 两个 worker 的 pid 不同（多进程）
    assert starts[0]["pid"] != starts[1]["pid"]
    # 串行：第一个 end 必须早于第二个 start（区间不重叠）
    assert ends[0]["t"] <= starts[1]["t"] + 1e-6


def test_multi_worker_worker_id_distinguishable(tmp_path):
    """多 worker 下 worker_id（pid@hostname）可用于区分执行来源。"""
    from backend.services.tasks import _current_worker_id

    wid1 = _current_worker_id()
    assert "@" in wid1
    pid_part = wid1.split("@")[0]
    assert pid_part.isdigit() and int(pid_part) == __import__("os").getpid()
