"""Job 数据模型与任务类型。"""
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class TaskType(str, Enum):
    DAILY_REVIEW = "daily_review"        # 每日复习清单（跑 agent）
    WEEKLY_SUMMARY = "weekly_summary"    # 已下线（家长周报功能砍除），保留枚举仅为兼容存量 job 数据
    SPACED_REVIEW = "spaced_review"      # 间隔复习（针对薄弱点，跑 agent）
    PUSH_REMINDER = "push_reminder"      # 纯文本提醒（不跑 LLM，直接推）
    CUSTOM_PROMPT = "custom_prompt"      # 自定义提示（跑 agent）


@dataclass
class Job:
    id: str
    task_type: TaskType
    cron: str                      # 5 段 cron：分 时 日 月 周
    prompt: str                    # push_reminder: 推送文本；其余: 给 agent 的提示
    channel: Optional[str] = None  # 推送渠道，None 时 infer
    chat_id: Optional[str] = None  # 推送目标，None 时 infer
    enabled: bool = True
    one_shot: bool = False         # 一次性任务，触发后自动禁用
    name: str = ""
    created_at: float = field(default_factory=time.time)
    last_run_at: Optional[float] = None
    last_status: str = ""          # ok | error | skipped
    run_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["task_type"] = TaskType(d.get("task_type", "custom_prompt"))
        return cls(**d)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]
