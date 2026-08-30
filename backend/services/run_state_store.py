"""Durable SQLite state for SignTask executions.

This store is intentionally colocated with the file-backed SignTask workdir so
CLI and Web executions share one source of truth without requiring a backend
SQLAlchemy session.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any


class RunStateStore:
    """Persist one mutable state row per ``run_id``."""

    def __init__(self, workdir: Path):
        history_dir = Path(workdir) / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = history_dir / "run_history.sqlite3"
        self._local = threading.local()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY,
                account_name TEXT NOT NULL,
                task_name TEXT NOT NULL,
                source TEXT NOT NULL,
                state TEXT NOT NULL,
                success INTEGER,
                error TEXT NOT NULL DEFAULT '',
                output TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                worker_id TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_runs_task_account_started "
            "ON task_runs(task_name, account_name, started_at DESC)"
        )
        conn.commit()

    def save(self, status: dict[str, Any]) -> None:
        """Atomically insert or update the durable state for a run."""
        values = {
            "run_id": str(status.get("run_id") or ""),
            "account_name": str(status.get("account_name") or ""),
            "task_name": str(status.get("task_name") or ""),
            "source": str(status.get("source") or "manual"),
            "state": str(status.get("state") or "finished"),
            "success": status.get("success"),
            "error": str(status.get("error") or ""),
            "output": str(status.get("output") or ""),
            "started_at": str(status.get("started_at") or ""),
            "finished_at": status.get("finished_at"),
            "worker_id": status.get("worker_id"),
        }
        if not values["run_id"]:
            return
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO task_runs (
                run_id, account_name, task_name, source, state, success, error,
                output, started_at, finished_at, worker_id
            ) VALUES (
                :run_id, :account_name, :task_name, :source, :state, :success,
                :error, :output, :started_at, :finished_at, :worker_id
            ) ON CONFLICT(run_id) DO UPDATE SET
                source=excluded.source, state=excluded.state, success=excluded.success,
                error=excluded.error, output=excluded.output,
                finished_at=excluded.finished_at, worker_id=excluded.worker_id
            """,
            values,
        )
        conn.commit()

    def latest_states(self) -> list[dict[str, Any]]:
        rows = (
            self._conn()
            .execute(
                """
            SELECT * FROM task_runs
            WHERE (task_name, account_name, started_at) IN (
                SELECT task_name, account_name, MAX(started_at)
                FROM task_runs GROUP BY task_name, account_name
            )
            """
            )
            .fetchall()
        )
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if data["success"] is not None:
            data["success"] = bool(data["success"])
        return data

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = (
            self._conn()
            .execute("SELECT * FROM task_runs WHERE run_id = ?", (run_id,))
            .fetchone()
        )
        if row is None:
            return None
        return self._row_to_dict(row)
