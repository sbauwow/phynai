"""Tests for PhynaiScheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from phynai.contracts.work import WorkItem, WorkPriority, WorkResult, WorkStatus
from phynai.orchestrator.graph import PhynaiDependencyGraph
from phynai.orchestrator.scheduler import PhynaiScheduler
from phynai.orchestrator.sources.direct import DirectSource


# ---------------------------------------------------------------------------
# Mock agent core
# ---------------------------------------------------------------------------

class MockAgentCore:
    """Returns a fixed WorkResult for any WorkItem."""

    def __init__(self, status: WorkStatus = WorkStatus.completed, response: str = "done"):
        self._status = status
        self._response = response
        self.calls: list[WorkItem] = []

    async def run(self, work_item: WorkItem) -> WorkResult:
        self.calls.append(work_item)
        return WorkResult(
            work_id=work_item.id,
            status=self._status,
            response=self._response,
            completed_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Mock sink
# ---------------------------------------------------------------------------

class MockSink:
    """Collects delivered WorkResults."""

    def __init__(self):
        self.results: list[WorkResult] = []

    async def deliver(self, result: WorkResult) -> None:
        self.results.append(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPhynaiScheduler:
    async def test_submit_and_run_once(self):
        agent = MockAgentCore()
        sched = PhynaiScheduler(agent=agent)
        item = WorkItem(prompt="test task")
        await sched.submit(item)

        result = await sched.run_once()
        assert result is not None
        assert result.work_id == item.id
        assert result.status == WorkStatus.completed
        assert len(agent.calls) == 1

    async def test_run_once_returns_none_when_no_work(self):
        agent = MockAgentCore()
        sched = PhynaiScheduler(agent=agent)
        result = await sched.run_once()
        assert result is None
        assert len(agent.calls) == 0

    async def test_multiple_sources_are_polled(self):
        agent = MockAgentCore()
        sched = PhynaiScheduler(agent=agent)

        src1 = DirectSource()
        src2 = DirectSource()
        sched.add_source(src1)
        sched.add_source(src2)

        await src1.submit(WorkItem(prompt="from src1", id="s1"))
        await src2.submit(WorkItem(prompt="from src2", id="s2"))

        r1 = await sched.run_once()
        r2 = await sched.run_once()
        assert r1 is not None
        assert r2 is not None
        processed_ids = {r1.work_id, r2.work_id}
        assert processed_ids == {"s1", "s2"}

    async def test_dependency_graph_blocks_ineligible(self):
        agent = MockAgentCore()
        graph = PhynaiDependencyGraph()
        sched = PhynaiScheduler(agent=agent, graph=graph)

        # b depends on a
        item_a = WorkItem(prompt="task a", id="a")
        item_b = WorkItem(prompt="task b", id="b")
        graph.add_edge("b", "a")  # b depends on a

        await sched.submit(item_b)
        await sched.submit(item_a)

        # First run should pick a (b is blocked)
        r1 = await sched.run_once()
        assert r1 is not None
        assert r1.work_id == "a"

        # Now b should be eligible (a was marked complete)
        r2 = await sched.run_once()
        assert r2 is not None
        assert r2.work_id == "b"

    async def test_sinks_receive_results(self):
        agent = MockAgentCore()
        sched = PhynaiScheduler(agent=agent)
        sink = MockSink()
        sched.add_sink(sink)

        item = WorkItem(prompt="sink test")
        await sched.submit(item)
        await sched.run_once()

        assert len(sink.results) == 1
        assert sink.results[0].work_id == item.id
        assert sink.results[0].status == WorkStatus.completed

    async def test_queue_size_property(self):
        agent = MockAgentCore()
        sched = PhynaiScheduler(agent=agent)
        assert sched.queue_size == 0
        await sched.submit(WorkItem(prompt="a"))
        await sched.submit(WorkItem(prompt="b"))
        assert sched.queue_size == 2
