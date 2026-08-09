from __future__ import annotations

import bcrypt

_BCRYPT_MAX_BYTES = 72


def _truncate_password(password: str) -> bytes:
    """bcrypt only uses the first 72 bytes; truncate to match passlib behavior."""
    return str(password or "").encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """哈希密码"""
    return bcrypt.hashpw(
        _truncate_password(password),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(
            _truncate_password(plain_password),
            str(hashed_password or "").encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False
