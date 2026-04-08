"""TaskQueue — topological dependency resolution for task DAGs."""

from __future__ import annotations

import logging
from typing import Callable

from phynai.multi.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskQueue:
    """Manages a set of tasks with dependency tracking.

    When a task completes, its dependents are automatically unblocked.
    When a task fails, transitive dependents are cascade-failed.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._on_complete: list[Callable[[Task], None]] = []

    # -- Loading -----------------------------------------------------------

    def add(self, task: Task) -> None:
        """Add a task to the queue."""
        self._tasks[task.id] = task
        if task.depends_on:
            task.block()

    def add_all(self, tasks: list[Task]) -> None:
        for t in tasks:
            self.add(t)

    # -- Queries -----------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def pending(self) -> list[Task]:
        """Return tasks that are ready to execute (pending, no unresolved deps)."""
        return [t for t in self._tasks.values() if t.is_ready()]

    def is_done(self) -> bool:
        """True if no tasks are pending, blocked, or in progress."""
        active = {TaskStatus.pending, TaskStatus.in_progress, TaskStatus.blocked}
        return not any(t.status in active for t in self._tasks.values())

    # -- State transitions -------------------------------------------------

    def start(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.in_progress

    def complete(self, task_id: str, result: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.complete(result)
        self._unblock_dependents(task_id)
        for cb in self._on_complete:
            cb(task)

    def fail(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.fail(error)
        if task.status == TaskStatus.failed:
            self._cascade_fail(task_id)

    # -- Dependency management ---------------------------------------------

    def _unblock_dependents(self, completed_id: str) -> None:
        """Promote blocked tasks whose dependencies are now satisfied."""
        for task in self._tasks.values():
            if task.status != TaskStatus.blocked:
                continue
            if completed_id in task.depends_on:
                task.depends_on.remove(completed_id)
                if not task.depends_on:
                    task.status = TaskStatus.pending
                    logger.debug("Unblocked task: %s", task.title)

    def _cascade_fail(self, failed_id: str) -> None:
        """Mark all transitive dependents of a failed task as failed."""
        to_fail: set[str] = set()
        frontier = [failed_id]
        while frontier:
            fid = frontier.pop()
            for task in self._tasks.values():
                if task.id in to_fail:
                    continue
                if fid in task.depends_on:
                    to_fail.add(task.id)
                    frontier.append(task.id)

        for tid in to_fail:
            task = self._tasks[tid]
            task.status = TaskStatus.failed
            task.error = f"Dependency '{failed_id}' failed"
            logger.debug("Cascade-failed task: %s", task.title)

    # -- Hooks -------------------------------------------------------------

    def on_complete(self, callback: Callable[[Task], None]) -> None:
        self._on_complete.append(callback)
