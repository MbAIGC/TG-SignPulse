from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from backend.core.config import get_settings
from backend.utils.time import utc_now_iso
from tg_signer.utils import atomic_write_json

_SESSION_MODE_ENV = "TG_SESSION_MODE"
_SESSION_MODE_FILE = "file"
_SESSION_MODE_STRING = "string"

_GLOBAL_SEMAPHORE: asyncio.Semaphore | None = None


def get_session_mode() -> str:
    mode = os.getenv(_SESSION_MODE_ENV, _SESSION_MODE_FILE).strip().lower()
    return _SESSION_MODE_STRING if mode == _SESSION_MODE_STRING else _SESSION_MODE_FILE


def is_string_session_mode() -> bool:
    return get_session_mode() == _SESSION_MODE_STRING


def get_no_updates_flag() -> bool:
    raw = os.getenv("TG_SESSION_NO_UPDATES") or os.getenv("TG_NO_UPDATES") or ""
    raw = raw.strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_global_semaphore() -> asyncio.Semaphore:
    global _GLOBAL_SEMAPHORE
    if _GLOBAL_SEMAPHORE is None:
        limit = _resolve_concurrency_limit()
        _GLOBAL_SEMAPHORE = asyncio.Semaphore(limit)
    return _GLOBAL_SEMAPHORE


def _resolve_concurrency_limit() -> int:
    # Priority: env var > global settings > default 1
    raw = (os.getenv("TG_GLOBAL_CONCURRENCY") or "").strip()
    if raw:
        try:
            return max(int(raw), 1)
        except ValueError:
            pass
    try:
        from backend.services.config import get_config_service

        settings = get_config_service().get_global_settings()
        val = settings.get("tg_global_concurrency")
        if val is not None:
            return max(int(val), 1)
    except Exception:
        pass
    return 1


def update_global_semaphore(new_limit: int) -> None:
    """Update the global semaphore with a new concurrency limit at runtime."""
    global _GLOBAL_SEMAPHORE
    new_limit = max(new_limit, 1)
    _GLOBAL_SEMAPHORE = asyncio.Semaphore(new_limit)


_ACCOUNT_STORE_LOCK = Lock()


def _account_store_path() -> Path:
    settings = get_settings()
    session_dir = settings.resolve_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 会话目录包含敏感凭证，禁止组/其他用户访问
        os.chmod(session_dir, 0o700)
    except OSError:
        pass
    return session_dir / "accounts.json"


def _load_account_store_unlocked() -> dict:
    path = _account_store_path()
    if not path.exists():
        return {"accounts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        data["accounts"] = {}
    return data


def _load_account_store() -> dict:
    with _ACCOUNT_STORE_LOCK:
        return _load_account_store_unlocked()


def _save_account_store_unlocked(data: dict) -> None:
    path = _account_store_path()
    atomic_write_json(path, data, indent=2)


def _save_account_store(data: dict) -> None:
    with _ACCOUNT_STORE_LOCK:
        _save_account_store_unlocked(data)


def list_account_names() -> list[str]:
    data = _load_account_store()
    accounts = data.get("accounts", {})
    if not isinstance(accounts, dict):
        return []
    return sorted(accounts.keys())


def get_account_session_string(account_name: str) -> str | None:
    data = _load_account_store()
    entry = data.get("accounts", {}).get(account_name)
    if not isinstance(entry, dict):
        return None
    session_string = entry.get("session_string")
    if isinstance(session_string, str) and session_string.strip():
        return session_string.strip()
    return None


def set_account_session_string(account_name: str, session_string: str) -> None:
    with _ACCOUNT_STORE_LOCK:
        data = _load_account_store_unlocked()
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
            data["accounts"] = accounts
        entry = accounts.get(account_name)
        if not isinstance(entry, dict):
            entry = {}
        entry["session_string"] = session_string.strip()
        entry["updated_at"] = utc_now_iso()
        accounts[account_name] = entry
        _save_account_store_unlocked(data)


def delete_account_session_string(account_name: str) -> None:
    with _ACCOUNT_STORE_LOCK:
        data = _load_account_store_unlocked()
        accounts = data.get("accounts")
        if isinstance(accounts, dict) and account_name in accounts:
            accounts.pop(account_name, None)
            _save_account_store_unlocked(data)


def rename_account_entry(old_account_name: str, new_account_name: str) -> None:
    if old_account_name == new_account_name:
        return

    with _ACCOUNT_STORE_LOCK:
        data = _load_account_store_unlocked()
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
            data["accounts"] = accounts

        entry = accounts.pop(old_account_name, None)
        if entry is None:
            return
        if new_account_name in accounts:
            raise ValueError(f"account_name {new_account_name} already exists")

        if not isinstance(entry, dict):
            entry = {}
        entry["updated_at"] = utc_now_iso()
        accounts[new_account_name] = entry
        _save_account_store_unlocked(data)


def get_account_profile(account_name: str) -> dict[str, Any]:
    data = _load_account_store()
    entry = data.get("accounts", {}).get(account_name)
    if not isinstance(entry, dict):
        return {}
    return {
        "remark": entry.get("remark"),
        "proxy": entry.get("proxy"),
        "status": entry.get("status"),
        "status_message": entry.get("status_message"),
        "status_code": entry.get("status_code"),
        "status_checked_at": entry.get("status_checked_at"),
        "needs_relogin": bool(entry.get("needs_relogin", False)),
        "invalid_notified_at": entry.get("invalid_notified_at"),
    }


def get_account_proxy(account_name: str) -> str | None:
    profile = get_account_profile(account_name)
    proxy = profile.get("proxy")
    if isinstance(proxy, str) and proxy.strip():
        return proxy.strip()
    return None


def get_account_remark(account_name: str) -> str | None:
    profile = get_account_profile(account_name)
    remark = profile.get("remark")
    if isinstance(remark, str) and remark.strip():
        return remark.strip()
    return None


def set_account_profile(
    account_name: str, *, remark: str | None = None, proxy: str | None = None
) -> None:
    with _ACCOUNT_STORE_LOCK:
        data = _load_account_store_unlocked()
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
            data["accounts"] = accounts
        entry = accounts.get(account_name)
        if not isinstance(entry, dict):
            entry = {}
        if remark is not None:
            entry["remark"] = remark.strip() if isinstance(remark, str) else remark
        if proxy is not None:
            entry["proxy"] = proxy.strip() if isinstance(proxy, str) else proxy
        entry["updated_at"] = utc_now_iso()
        accounts[account_name] = entry
        _save_account_store_unlocked(data)


def get_account_status(account_name: str) -> dict[str, Any]:
    profile = get_account_profile(account_name)
    status = profile.get("status")
    return {
        "status": status if isinstance(status, str) and status else "connected",
        "message": profile.get("status_message") or "",
        "code": profile.get("status_code"),
        "checked_at": profile.get("status_checked_at"),
        "needs_relogin": bool(profile.get("needs_relogin", False)),
        "invalid_notified_at": profile.get("invalid_notified_at"),
    }


def set_account_status(
    account_name: str,
    *,
    status: str,
    message: str = "",
    code: str | None = None,
    needs_relogin: bool = False,
    invalid_notified_at: str | None = None,
) -> None:
    with _ACCOUNT_STORE_LOCK:
        data = _load_account_store_unlocked()
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
            data["accounts"] = accounts
        entry = accounts.get(account_name)
        if not isinstance(entry, dict):
            entry = {}
        entry["status"] = status
        entry["status_message"] = message or ""
        entry["status_code"] = code
        entry["status_checked_at"] = utc_now_iso()
        entry["needs_relogin"] = bool(needs_relogin)
        if invalid_notified_at is not None:
            entry["invalid_notified_at"] = invalid_notified_at
        if status != "invalid":
            entry.pop("invalid_notified_at", None)
        entry["updated_at"] = utc_now_iso()
        accounts[account_name] = entry
        _save_account_store_unlocked(data)


def _write_session_string_file(path: Path, content: str) -> None:
    """写入 session_string 文件并强制 0o600（会话凭证，禁止组/其他用户可读）。"""
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def session_string_file_path(session_dir: Path, account_name: str) -> Path:
    return session_dir / f"{account_name}.session_string"


def load_session_string_file(session_dir: Path, account_name: str) -> str | None:
    path = session_string_file_path(session_dir, account_name)
    if not path.exists():
        # Try to export session string from .session SQLite file
        return _export_session_string_from_file(session_dir, account_name)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if content and _is_legacy_broken_session_string(content):
        # 旧格式（"1" + base64，357 字符）与 kurigram 2.2.x 不兼容，
        # 删除缓存并重新从 .session 文件导出当前格式
        try:
            path.unlink()
        except OSError:
            pass
        return _export_session_string_from_file(session_dir, account_name)
    return content or None


def _is_legacy_broken_session_string(content: str) -> bool:
    """识别旧的 v1 会话字符串（'1' + base64，长度 % 4 == 1）。"""
    text = content.strip()
    if not text or text[0] != "1":
        return False
    return len(text) > 20 and len(text) % 4 == 1


def _export_session_string_from_file(
    session_dir: Path, account_name: str
) -> str | None:
    """Extract session string from .session SQLite file and cache it."""
    import base64
    import sqlite3
    import struct

    session_file = session_dir / f"{account_name}.session"
    if not session_file.exists():
        return None

    try:
        conn = sqlite3.connect(str(session_file), timeout=10, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass

        try:
            row = conn.execute(
                "SELECT dc_id, api_id, test_mode, auth_key, user_id, is_bot FROM sessions"
            ).fetchone()
        except Exception:
            conn.close()
            return None

        if not row:
            conn.close()
            return None

        dc_id, api_id, test_mode, auth_key, user_id, is_bot = row
        conn.close()

        if not auth_key or not user_id:
            return None

        # Pack into the current kurigram/pyrogram session string format
        # (no version prefix; includes api_id). The old "1"+base64 v1 format
        # cannot be decoded by kurigram 2.2.x (see _is_legacy_broken_session_string).
        packed = struct.pack(
            ">BI?256sQ?",
            dc_id,
            api_id,
            bool(test_mode),
            auth_key,
            user_id,
            bool(is_bot),
        )
        session_string = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")

        # Cache it to .session_string file for future use
        try:
            cache_path = session_string_file_path(session_dir, account_name)
            _write_session_string_file(cache_path, session_string)
        except Exception:
            pass

        return session_string
    except Exception:
        return None


def save_session_string_file(
    session_dir: Path, account_name: str, session_string: str
) -> None:
    path = session_string_file_path(session_dir, account_name)
    _write_session_string_file(path, session_string.strip())


def delete_session_string_file(session_dir: Path, account_name: str) -> None:
    path = session_string_file_path(session_dir, account_name)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
