from __future__ import annotations

import json
from typing import Any, List, Optional

from sqlalchemy import Column, Integer, String, Text

from backend.core.database import Base


class TaskRunHistory(Base):
    """SignTask 运行历史统一存储（SQLite 主存储，JSON 仅兼容读取）。

    5.4 目标：面板历史记录完全统一到 SQLite 主存储。
    该表按 (task_name, account_name) 维度记录每次运行，字段与
    原 history JSON 条目对齐，保证读取方无需感知存储介质。
    """

    __tablename__ = "task_run_history"

    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String(128), nullable=False, index=True)
    account_name = Column(String(128), nullable=True, index=True)
    run_id = Column(String(32), nullable=True, index=True)
    time = Column(String(32), nullable=False, index=True)
    success = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=True)
    flow_logs = Column(Text, nullable=True)  # JSON list[str]
    flow_truncated = Column(Integer, nullable=False, default=0)
    flow_line_count = Column(Integer, nullable=False, default=0)
    last_target_message = Column(Text, nullable=True)

    @staticmethod
    def _decode_flow_logs(raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def to_entry(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "success": bool(self.success),
            "message": self.message or "",
            "account_name": self.account_name or "",
            "run_id": self.run_id or "",
            "flow_logs": self._decode_flow_logs(self.flow_logs),
            "flow_truncated": bool(self.flow_truncated),
            "flow_line_count": self.flow_line_count,
            "last_target_message": self.last_target_message or "",
        }
