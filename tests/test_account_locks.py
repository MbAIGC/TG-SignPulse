"""回归测试：backend/utils/account_locks.py 的 AccountLock（账号级跨进程锁）。

覆盖：
- 单例缓存（get_account_lock 同账号返回同一对象）
- asyncio.Lock 子类 API 兼容（async with / acquire / release / locked）
- locked() 只反映进程内占用状态（不误判跨进程占用）
- 跨协程 acquire+release 配对（login/QR 流程依赖）
- 启用文件锁后：进程内互斥 + 跨进程互斥（flock 同 fd）
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from backend.utils import account_locks
from backend.utils.account_locks import AccountLock, get_account_lock


@pytest.fixture(autouse=True)
def _reset_lock_state(monkeypatch):
    """每个测试后清理单例缓存与锁目录配置，避免跨测试污染。"""
    yield
    account_locks._ACCOUNT_LOCKS.clear()
    account_locks._LOCK_DIR = None
    monkeypatch.delenv("ACCOUNT_LOCK_FILE", raising=False)


def test_get_account_lock_singleton():
    a = get_account_lock("acct1")
    b = get_account_lock("acct1")
    assert a is b
    assert isinstance(a, AccountLock)
    assert isinstance(a, asyncio.Lock)


def test_async_with_and_locked_semantics():
    async def main():
        lock = get_account_lock("acct1")
        assert not lock.locked()
        async with lock:
            assert lock.locked()
        assert not lock.locked()

    asyncio.run(main())


def test_manual_acquire_release_paired_across_tasks():
    """login/QR 流程依赖：一个协程 acquire 存全局，另一协程 release。"""

    async def holder(released):
        lock = get_account_lock("acct1")
        await lock.acquire()
        assert lock.locked()
        released.append(lock)
        await asyncio.sleep(0.05)

    async def releaser(released):
        await asyncio.sleep(0.01)
        lock = released[0]
        assert lock.locked()
        lock.release()
        assert not lock.locked()

    async def main():
        released: list = []
        await asyncio.gather(holder(released), releaser(released))

    asyncio.run(main())


def test_concurrent_same_account_serialized():
    """并发获取同账号锁应串行（互斥），不报错。"""

    async def main():
        lock = get_account_lock("acct1")
        order: list[str] = []

        async def worker(name: str) -> None:
            async with lock:
                order.append(f"{name}_in")
                await asyncio.sleep(0.02)
                order.append(f"{name}_out")

        await asyncio.gather(worker("a"), worker("b"), worker("c"))
        # 每个 worker 的 in/out 必须配对且不交错
        assert order.count("a_in") == 1 and order.count("a_out") == 1
        for i in range(0, len(order), 2):
            assert order[i].endswith("_in")
            assert order[i + 1].endswith("_out")

    asyncio.run(main())


def _probe_lock_held(lock_file: Path, timeout: float = 5.0) -> bool:
    """子进程尝试非阻塞 flock；被占用则返回 True，空闲返回 False。"""
    code = (
        "import fcntl,os,sys\n"
        "fd=os.open(sys.argv[1], os.O_RDWR)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('free')\n"
        "except OSError:\n"
        "    print('held')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(lock_file)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip() == "held"


def test_file_lock_held_by_another_process(tmp_path):
    """启用文件锁后，跨进程互斥：另一进程持锁时本进程 flock 探测被占用。"""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir(exist_ok=True)
    account_locks._LOCK_DIR = lock_dir

    lock = AccountLock("acct_probe", lock_dir=lock_dir)

    async def main():
        # 尚未持锁：探测应为 free
        assert not _probe_lock_held(lock._lock_path)
        async with lock:
            # 本进程持锁中：外部探测应为 held
            assert _probe_lock_held(lock._lock_path)
        # 释放后：探测恢复 free
        assert not _probe_lock_held(lock._lock_path)

    asyncio.run(main())


def test_locked_only_reflects_inprocess_when_file_lock_disabled(tmp_path):
    """默认关闭文件锁时，locked() 仅反映进程内占用。"""
    lock = AccountLock("acct_inproc")
    assert lock._lock_path is None  # 默认无文件锁

    async def main():
        async with lock:
            assert lock.locked()
        assert not lock.locked()

    asyncio.run(main())
