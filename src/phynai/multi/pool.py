"""AgentPool — semaphore-controlled concurrent agent execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from phynai.multi.task import Task

logger = logging.getLogger(__name__)


class AgentPool:
    """Manages concurrent agent execution with a semaphore cap.

    Each ``run()`` call acquires a semaphore slot, executes the agent
    function, and releases the slot when done.
    """

    def __init__(self, max_concurrency: int = 5) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active: dict[str, asyncio.Task] = {}

    async def run(
        self,
        task: Task,
        agent_fn: Callable[[Task], Awaitable[str]],
    ) -> str:
        """Execute an agent function for a task, respecting concurrency limits.

        Returns the agent's output string. Raises on failure.
        """
        async with self._semaphore:
            logger.debug("Pool: starting '%s' (assignee=%s)", task.title, task.assignee)
            try:
                result = await agent_fn(task)
                return result
            except Exception:
                logger.exception("Pool: agent failed on '%s'", task.title)
                raise

    async def run_batch(
        self,
        tasks: list[Task],
        agent_fn: Callable[[Task], Awaitable[str]],
    ) -> dict[str, str | Exception]:
        """Execute multiple tasks concurrently. Returns {task_id: result_or_exception}."""
        async def _run_one(t: Task) -> tuple[str, str | Exception]:
            try:
                result = await self.run(t, agent_fn)
                return t.id, result
            except Exception as e:
                return t.id, e

        coros = [_run_one(t) for t in tasks]
        pairs = await asyncio.gather(*coros)
        return dict(pairs)
