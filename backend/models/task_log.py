from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.core.database import Base
from backend.utils.time import utc_now_naive


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    run_id = Column(String(32), nullable=True, index=True)
    # 执行进程标识（pid:hostname），便于多 worker 部署排查并发执行来源
    worker_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    log_path = Column(String(255), nullable=True)
    output = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utc_now_naive, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="logs")
