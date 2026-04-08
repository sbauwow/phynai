"""Tests for DirectSource and CronSource."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from phynai.contracts.work import WorkItem
from phynai.orchestrator.sources.direct import DirectSource
from phynai.orchestrator.sources.cron import CronSource


@pytest.mark.asyncio
class TestDirectSource:
    async def test_submit_and_poll(self):
        src = DirectSource()
        item = WorkItem(prompt="direct task")
        await src.submit(item)
        items = await src.poll()
        assert len(items) == 1
        assert items[0].id == item.id

    async def test_poll_returns_empty_when_nothing_submitted(self):
        src = DirectSource()
        items = await src.poll()
        assert items == []

    async def test_poll_drains_queue(self):
        src = DirectSource()
        await src.submit(WorkItem(prompt="a"))
        await src.submit(WorkItem(prompt="b"))
        items = await src.poll()
        assert len(items) == 2
        items2 = await src.poll()
        assert items2 == []


@pytest.mark.asyncio
class TestCronSource:
    async def test_add_job_and_poll_when_due(self):
        src = CronSource()
        job_id = src.add_job("every 0s", "cron task")
        # The job's next_run is _advance(schedule, now) which for "every 0s"
        # would be now + 0 seconds = now. But _advance parses "every 0s" as 0 seconds.
        # Actually let's use a trick: manually set next_run to the past
        for job in src._jobs:
            if job.id == job_id:
                job.next_run = datetime.now(timezone.utc) - timedelta(seconds=1)
        items = await src.poll()
        assert len(items) == 1
        assert items[0].prompt == "cron task"
        assert items[0].metadata["cron_job_id"] == job_id

    async def test_poll_returns_empty_when_not_yet_due(self):
        src = CronSource()
        src.add_job("every 1h", "future task")
        items = await src.poll()
        assert items == []

    async def test_remove_job(self):
        src = CronSource()
        job_id = src.add_job("every 1h", "removable")
        assert len(src._jobs) == 1
        src.remove_job(job_id)
        assert len(src._jobs) == 0

    async def test_poll_advances_next_run(self):
        src = CronSource()
        job_id = src.add_job("every 5m", "recurring")
        # Force it to be due now
        for job in src._jobs:
            if job.id == job_id:
                job.next_run = datetime.now(timezone.utc) - timedelta(seconds=1)
        await src.poll()
        # After poll, next_run should be ~5 min in the future
        for job in src._jobs:
            if job.id == job_id:
                assert job.next_run > datetime.now(timezone.utc)
