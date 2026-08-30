from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

_ACCOUNT_LOCKS: Dict[str, "AccountLock"] = {}

# 跨进程文件锁目录；None 表示关闭文件锁（纯进程内锁，行为与旧版一致）。
# 可通过 set_account_lock_dir() 或环境变量 ACCOUNT_LOCK_FILE 开启。
_LOCK_DIR: Optional[Path] = None

# 环境变量：非空且不为 "0" 时，自动把文件锁目录解析到 workdir/locks。
_LOCK_FILE_ENV = "ACCOUNT_LOCK_FILE"

# 文件锁获取超时（秒）：超时抛出 AccountLockTimeout，避免无限阻塞线程池。
_LOCK_TIMEOUT_ENV = "ACCOUNT_LOCK_TIMEOUT"


class AccountLockTimeout(TimeoutError):
    """跨进程文件锁获取超时。"""

    def __init__(self, account_name: str, timeout: float) -> None:
        super().__init__(
            f"获取账号 {account_name!r} 的跨进程文件锁超时（{timeout:.1f}s）"
        )
        self.account_name = account_name
        self.timeout = timeout


def _get_lock_timeout() -> float:
    raw = os.getenv(_LOCK_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw)
        return value if value > 0 else 30.0
    except ValueError:
        return 30.0


def _safe_lock_key(account_name: str) -> str:
    """把账号名规整为安全文件名，防止路径注入与跨账号误锁。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", account_name)
    return safe or "account"


def set_account_lock_dir(lock_dir: Optional[Path]) -> None:
    """显式配置跨进程锁目录；传 None 关闭文件锁。"""
    global _LOCK_DIR
    _LOCK_DIR = Path(lock_dir) if lock_dir is not None else None


def get_lock_dir() -> Optional[Path]:
    """解析当前跨进程锁目录（含环境变量兜底）。"""
    if _LOCK_DIR is not None:
        return _LOCK_DIR
    env = os.getenv(_LOCK_FILE_ENV)
    if not env or env.strip() in ("", "0", "false", "False", "no", "off"):
        return None
    try:
        from backend.core.config import get_settings

        return get_settings().resolve_workdir() / "locks"
    except Exception:
        return None


def is_file_lock_enabled() -> bool:
    """当前是否启用了跨进程文件锁（供启动日志/README 说明）。"""
    return get_lock_dir() is not None


class AccountLock(asyncio.Lock):
    """账号级互斥锁：进程内 asyncio.Lock + 可选跨进程文件锁。

    语义约束（调用方依赖，勿破坏）：
    - locked() 只反映进程内占用状态，避免误释放他人持有的文件锁；
    - 支持跨协程/跨任务 acquire+release 配对（login/QR 流程）；
    - 文件锁按每账号常驻单 fd：进程内互斥靠 asyncio.Lock，
      进程间互斥靠 flock 同一 fd（不同 fd 会互锁，不能每次重建）；
    - 保持非重入（现有代码无嵌套获取路径）；
    - 文件锁获取使用非阻塞 LOCK_NB + 轮询，响应协程取消并在超时后
      抛出 AccountLockTimeout（不占用线程池，不无限阻塞）。
    """

    def __init__(self, account_name: str, lock_dir: Optional[Path] = None) -> None:
        super().__init__()
        self._account_name = account_name
        self._lock_path: Optional[Path] = None
        self._file_fd: Optional[int] = None
        if lock_dir is not None:
            self._lock_path = Path(lock_dir) / f"{_safe_lock_key(account_name)}.lock"

    def _try_acquire_file_lock(self) -> bool:
        """非阻塞 flock 尝试：成功返回 True 并持有 fd，被占用返回 False。

        每次 acquire 都新开 fd（绝不同时持有两个 fd），配合 release 释放，
        满足「每账号单 fd」约束；LOCK_NB 保证不阻塞调用线程。
        """
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX fallback
            self._file_fd = None
            return True
        fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._file_fd = fd
        return True

    async def _acquire_file_lock_with_timeout(self) -> None:
        timeout = _get_lock_timeout()
        deadline = time.monotonic() + timeout
        while not self._try_acquire_file_lock():
            if time.monotonic() >= deadline:
                raise AccountLockTimeout(self._account_name, timeout)
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                # 协程被取消：未持有任何 fd，直接上抛
                raise

    def _release_file_lock(self) -> None:
        if self._file_fd is None:
            return
        try:
            import fcntl

            try:
                fcntl.flock(self._file_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._file_fd)
        except Exception:
            pass
        finally:
            self._file_fd = None

    async def acquire(self) -> bool:
        """先取进程内锁，再取跨进程文件锁（非阻塞轮询 + 超时）。"""
        acquired = await super().acquire()
        if self._lock_path is not None:
            try:
                await self._acquire_file_lock_with_timeout()
            except BaseException:
                # 文件锁失败/超时/取消：释放已取得的进程内锁再上抛
                super().release()
                raise
        return acquired

    def release(self) -> None:
        """先释放文件锁，再释放进程内锁。"""
        if self._lock_path is not None:
            self._release_file_lock()
        super().release()

    async def __aenter__(self) -> "AccountLock":
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        self.release()


def get_account_lock(account_name: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_name)
    if lock is None:
        lock = AccountLock(account_name, lock_dir=get_lock_dir())
        _ACCOUNT_LOCKS[account_name] = lock
    return lock
