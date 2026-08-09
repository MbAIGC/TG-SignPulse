"""Vendored third-party modules (kept local to avoid fragile system dependencies).

These implementations are API-compatible subsets of the upstream packages:

- ``jose``: minimal JWT (HS256) implementation, mirroring ``python-jose``.
- ``pyotp``: TOTP / provisioning-URI implementation, mirroring ``pyotp``.
"""

from backend.vendor import jose, pyotp

__all__ = ["jose", "pyotp"]
