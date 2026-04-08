"""Task — dependency-aware work unit for multi-agent pipelines."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


@dataclass
class Task:
    """A unit of work in a multi-agent task DAG.

    Tasks form a directed acyclic graph via ``depends_on``. A task stays
    ``blocked`` until all dependencies are ``completed``. If any dependency
    fails, the task and its transitive dependents are marked ``failed``.
    """

    title: str
    description: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    assignee: str | None = None
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.pending
    result: str = ""
    error: str = ""
    max_retries: int = 1
    retries: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_ready(self) -> bool:
        """True if the task can be executed (no unresolved deps)."""
        return self.status == TaskStatus.pending and not self.depends_on

    def block(self) -> None:
        if self.depends_on and self.status == TaskStatus.pending:
            self.status = TaskStatus.blocked

    def complete(self, result: str) -> None:
        self.status = TaskStatus.completed
        self.result = result

    def fail(self, error: str) -> None:
        self.retries += 1
        if self.retries < self.max_retries:
            self.status = TaskStatus.pending
            self.error = error
        else:
            self.status = TaskStatus.failed
            self.error = error
