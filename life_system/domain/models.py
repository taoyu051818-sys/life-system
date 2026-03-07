from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    OPEN = "open"
    SNOOZED = "snoozed"
    DONE = "done"
    ABANDONED = "abandoned"


@dataclass(slots=True)
class Task:
    id: int
    title: str
    status: TaskStatus
    priority: int = 3
    due_at: Optional[str] = None
    snooze_until: Optional[str] = None
