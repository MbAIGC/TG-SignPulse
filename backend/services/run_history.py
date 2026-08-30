"""SignTask 运行历史统一存储（SQLite 主存储 + JSON 兼容读取）。

5.4 目标：面板历史记录完全统一到 SQLite 主存储。

设计：
- SQLite 主存储：`<workdir>/history/run_history.sqlite3`，表 `run_history`。
  sign_tasks 是纯文件服务（无 backend SQLAlchemy 依赖，测试/CLI 均可直调），
  因此这里用独立 SQLite 文件并自带 schema 迁移，避免与 backend 的 db.sqlite 耦合。
- JSON 兼容：写入时仍同步写 history/*.json（供旧版本/离线检查），但读取优先 SQLite，
  SQLite 无数据时回退 JSON（首次迁移前的老数据）。
- 前端接口形状不变，无需前端改动。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = 1


class RunHistoryStore:
    """基于 workdir 的 SQLite 运行历史存储。"""

    def __init__(self, workdir: Path):
        self.history_dir = Path(workdir) / "history"
        self.db_path = self.history_dir / "run_history.sqlite3"
        self._local = threading.local()
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---- schema ----
    def _ensure_schema(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        migrations = {1: self._migrate_to_v1}
        while version < SCHEMA_VERSION:
            migrations[version + 1](conn)
            version += 1
            conn.execute(f"PRAGMA user_version={version}")
        conn.commit()

    @staticmethod
    def _migrate_to_v1(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                account_name TEXT,
                run_id TEXT,
                time TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                flow_logs TEXT,
                flow_truncated INTEGER NOT NULL DEFAULT 0,
                flow_line_count INTEGER NOT NULL DEFAULT 0,
                last_target_message TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_history_task_time "
            "ON run_history(task_name, time)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_history_task_account "
            "ON run_history(task_name, account_name)"
        )

    # ---- 写入 ----
    def save_entry(
        self,
        *,
        task_name: str,
        account_name: str,
        entry: Dict[str, Any],
        max_entries: int,
    ) -> None:
        conn = self._conn()
        # Keep insertion and retention in one transaction so concurrent writers
        # cannot observe or delete a partially committed history update.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO run_history
                    (task_name, account_name, run_id, time, success, message,
                     flow_logs, flow_truncated, flow_line_count, last_target_message)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_name,
                    account_name or None,
                    entry.get("run_id") or "",
                    entry.get("time") or "",
                    1 if entry.get("success") else 0,
                    entry.get("message") or "",
                    _json_dumps(entry.get("flow_logs") or []),
                    1 if entry.get("flow_truncated") else 0,
                    int(entry.get("flow_line_count") or 0),
                    entry.get("last_target_message") or "",
                ),
            )

            # Retain the newest entries independently for each account.
            rows = conn.execute(
                "SELECT id FROM run_history WHERE task_name=? AND account_name IS ? "
                "ORDER BY id DESC",
                (task_name, account_name or None),
            ).fetchall()
            if max_entries >= 0 and len(rows) > max_entries:
                remove_ids = [r[0] for r in rows[max_entries:]]
                placeholders = ",".join("?" * len(remove_ids))
                conn.execute(
                    f"DELETE FROM run_history WHERE id IN ({placeholders})",
                    remove_ids,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def import_entries(
        self,
        *,
        task_name: str,
        account_name: str,
        entries: List[Dict[str, Any]],
        max_entries: int,
    ) -> int:
        """Idempotently import compatible JSON entries into SQLite."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        imported = 0
        try:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT run_id FROM run_history WHERE task_name=? AND account_name IS ?",
                    (task_name, account_name or None),
                )
                if row[0]
            }
            for entry in entries:
                run_id = str(entry.get("run_id") or "") or _legacy_entry_id(entry)
                if run_id in existing:
                    continue
                conn.execute(
                    """
                    INSERT INTO run_history
                        (task_name, account_name, run_id, time, success, message,
                         flow_logs, flow_truncated, flow_line_count, last_target_message)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_name,
                        account_name or None,
                        run_id,
                        entry.get("time") or "",
                        1 if entry.get("success") else 0,
                        entry.get("message") or "",
                        _json_dumps(entry.get("flow_logs") or []),
                        1 if entry.get("flow_truncated") else 0,
                        int(entry.get("flow_line_count") or 0),
                        entry.get("last_target_message") or "",
                    ),
                )
                existing.add(run_id)
                imported += 1
            rows = conn.execute(
                "SELECT id FROM run_history WHERE task_name=? AND account_name IS ? "
                "ORDER BY id DESC",
                (task_name, account_name or None),
            ).fetchall()
            if len(rows) > max_entries:
                remove_ids = [row[0] for row in rows[max_entries:]]
                placeholders = ",".join("?" * len(remove_ids))
                conn.execute(
                    f"DELETE FROM run_history WHERE id IN ({placeholders})", remove_ids
                )
            conn.commit()
            return imported
        except Exception:
            conn.rollback()
            raise

    # ---- 读取 ----
    def load_entries(
        self, *, task_name: str, account_name: str = ""
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        if account_name:
            rows = conn.execute(
                "SELECT * FROM run_history WHERE task_name=? AND account_name=? "
                "ORDER BY time DESC",
                (task_name, account_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM run_history WHERE task_name=? ORDER BY time DESC",
                (task_name,),
            ).fetchall()
        cols = [
            d[0] for d in conn.execute("SELECT * FROM run_history LIMIT 0").description
        ]
        return [_row_to_entry(row, cols) for row in rows]

    def load_all_entries(self) -> List[Dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM run_history ORDER BY time DESC").fetchall()
        cols = [
            d[0] for d in conn.execute("SELECT * FROM run_history LIMIT 0").description
        ]
        return [_row_to_entry(row, cols) for row in rows]

    # ---- 删除 ----
    def delete_entry(
        self,
        *,
        task_name: str = "",
        account_name: str = "",
        time: str = "",
        run_id: str = "",
    ) -> int:
        conn = self._conn()
        conditions: List[str] = []
        params: List[Any] = []
        if task_name:
            conditions.append("task_name=?")
            params.append(task_name)
        if account_name:
            conditions.append("account_name=?")
            params.append(account_name)
        if run_id:
            conditions.append("run_id=?")
            params.append(run_id)
        elif time:
            conditions.append("time=?")
            params.append(time)
        if not conditions:
            return 0
        cur = conn.execute(
            f"DELETE FROM run_history WHERE {' AND '.join(conditions)}", params
        )
        conn.commit()
        return cur.rowcount

    def clear(self, *, task_name: str = "") -> int:
        conn = self._conn()
        if task_name:
            cur = conn.execute(
                "DELETE FROM run_history WHERE task_name=?", (task_name,)
            )
        else:
            cur = conn.execute("DELETE FROM run_history")
        conn.commit()
        return cur.rowcount

    def delete_older_than(self, *, before_iso: str) -> int:
        """删除 time < before_iso 的历史条目（ISO 字符串字典序比较）。"""
        conn = self._conn()
        cur = conn.execute("DELETE FROM run_history WHERE time < ?", (before_iso,))
        conn.commit()
        return cur.rowcount

    def total_entries(self) -> int:
        conn = self._conn()
        return conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]


def _legacy_entry_id(entry: Dict[str, Any]) -> str:
    payload = json.dumps(
        entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "legacy-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _row_to_entry(row, cols: List[str]) -> Dict[str, Any]:
    data = dict(zip(cols, row, strict=True))
    flow_logs: Any = []
    try:
        parsed = json.loads(data.get("flow_logs") or "[]")
        flow_logs = parsed if isinstance(parsed, list) else []
    except Exception:
        flow_logs = []
    return {
        "time": data.get("time") or "",
        "success": bool(data.get("success")),
        "message": data.get("message") or "",
        "account_name": data.get("account_name") or "",
        "run_id": data.get("run_id") or "",
        "flow_logs": flow_logs,
        "flow_truncated": bool(data.get("flow_truncated")),
        "flow_line_count": int(data.get("flow_line_count") or 0),
        "last_target_message": data.get("last_target_message") or "",
    }


_store_cache: Dict[str, RunHistoryStore] = {}


def get_run_history_store(workdir: Path) -> RunHistoryStore:
    key = str(Path(workdir).resolve())
    store = _store_cache.get(key)
    if store is None:
        store = RunHistoryStore(workdir)
        _store_cache[key] = store
    return store
