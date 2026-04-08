"""Scheduler — task-to-agent assignment strategies."""

from __future__ import annotations

import logging
from collections import Counter

from phynai.multi.queue import TaskQueue
from phynai.multi.task import Task, TaskStatus
from phynai.multi.team import AgentSpec

logger = logging.getLogger(__name__)

Strategy = str  # "round-robin" | "least-busy" | "capability-match" | "dependency-first"


class Scheduler:
    """Assigns pending tasks to agents using a configurable strategy.

    Strategies:
      - round-robin:      Distribute sequentially across agents.
      - least-busy:       Assign to agent with fewest in-progress tasks.
      - capability-match: Keyword overlap between task text and agent role.
      - dependency-first: Prioritize critical-path tasks (most blocked dependents).
    """

    def __init__(self, strategy: Strategy = "dependency-first") -> None:
        self._strategy = strategy
        self._cursor = 0  # for round-robin

    def auto_assign(
        self, queue: TaskQueue, agents: list[AgentSpec],
    ) -> list[Task]:
        """Assign all unassigned pending tasks. Returns newly assigned tasks."""
        pending = [t for t in queue.pending() if t.assignee is None]
        if not pending or not agents:
            return []

        dispatch = {
            "round-robin": self._round_robin,
            "least-busy": self._least_busy,
            "capability-match": self._capability_match,
            "dependency-first": self._dependency_first,
        }

        fn = dispatch.get(self._strategy, self._dependency_first)
        assigned = fn(pending, agents, queue)
        for task in assigned:
            logger.debug("Assigned '%s' → %s", task.title, task.assignee)
        return assigned

    # -- Strategies --------------------------------------------------------

    def _round_robin(
        self, tasks: list[Task], agents: list[AgentSpec], _queue: TaskQueue,
    ) -> list[Task]:
        assigned = []
        for task in tasks:
            task.assignee = agents[self._cursor % len(agents)].name
            self._cursor += 1
            assigned.append(task)
        return assigned

    def _least_busy(
        self, tasks: list[Task], agents: list[AgentSpec], queue: TaskQueue,
    ) -> list[Task]:
        busy = Counter(
            t.assignee for t in queue.by_status(TaskStatus.in_progress)
            if t.assignee
        )
        assigned = []
        for task in tasks:
            least = min(agents, key=lambda a: busy.get(a.name, 0))
            task.assignee = least.name
            busy[least.name] += 1
            assigned.append(task)
        return assigned

    def _capability_match(
        self, tasks: list[Task], agents: list[AgentSpec], _queue: TaskQueue,
    ) -> list[Task]:
        assigned = []
        for task in tasks:
            task_words = set((task.title + " " + task.description).lower().split())
            best_agent = agents[0]
            best_score = -1
            for agent in agents:
                role_words = set(agent.role.lower().split())
                score = len(task_words & role_words)
                if score > best_score:
                    best_score = score
                    best_agent = agent
            task.assignee = best_agent.name
            assigned.append(task)
        return assigned

    def _dependency_first(
        self, tasks: list[Task], agents: list[AgentSpec], queue: TaskQueue,
    ) -> list[Task]:
        """Prioritize tasks on the critical path (most blocked dependents)."""
        # Count how many blocked tasks each pending task would unblock
        blocked = queue.by_status(TaskStatus.blocked)
        unblock_count: dict[str, int] = {}
        for task in tasks:
            count = sum(
                1 for b in blocked if task.id in b.depends_on
            )
            unblock_count[task.id] = count

        # Sort by unblock count descending (critical path first)
        prioritized = sorted(tasks, key=lambda t: unblock_count.get(t.id, 0), reverse=True)

        # Assign using least-busy among agents
        busy = Counter(
            t.assignee for t in queue.by_status(TaskStatus.in_progress)
            if t.assignee
        )
        assigned = []
        for task in prioritized:
            least = min(agents, key=lambda a: busy.get(a.name, 0))
            task.assignee = least.name
            busy[least.name] += 1
            assigned.append(task)
        return assigned
