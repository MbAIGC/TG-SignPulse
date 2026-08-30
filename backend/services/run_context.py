"""统一任务运行的不可变上下文。

RunContext 是 API、Scheduler 与数据库任务适配层之间的共同契约；
具体的 Telegram 执行仍由 SignTaskService 负责，以便逐步迁移旧入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.utils.time import utc_now_iso

RunSource = Literal["api", "scheduler", "db_task", "manual"]


@dataclass(frozen=True, slots=True)
class RunContext:
    """一条任务运行的稳定标识及来源信息。"""

    run_id: str
    account_name: str
    task_name: str
    source: RunSource = "manual"
    started_at: str = field(default_factory=utc_now_iso)

    @property
    def task_key(self) -> tuple[str, str]:
        return (self.account_name, self.task_name)
