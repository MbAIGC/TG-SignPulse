"""Lightweight coordination primitives for task runs.

This first migration step centralizes task-key reservation without moving the
Telegram executor. Callers can adopt it incrementally while keeping existing
status and history APIs compatible.
"""

from __future__ import annotations

import asyncio
import uuid

from backend.services.run_context import RunContext, RunSource


class RunCoordinator:
    """Serialize run reservation per account/task key."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._reserved: set[tuple[str, str]] = set()

    def _lock_for(self, key: tuple[str, str]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def try_reserve(
        self, account_name: str, task_name: str, *, source: RunSource
    ) -> RunContext | None:
        """Atomically reserve a task key until :meth:`release` is called."""
        key = (account_name, task_name)
        async with self._lock_for(key):
            if key in self._reserved:
                return None
            self._reserved.add(key)
            return RunContext(
                run_id=uuid.uuid4().hex,
                account_name=account_name,
                task_name=task_name,
                source=source,
            )

    async def release(self, context: RunContext) -> None:
        key = context.task_key
        async with self._lock_for(key):
            self._reserved.discard(key)

    def is_reserved(self, account_name: str, task_name: str) -> bool:
        return (account_name, task_name) in self._reserved
