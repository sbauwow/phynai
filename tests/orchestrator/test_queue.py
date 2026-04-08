"""Tests for WorkQueue."""

from phynai.contracts.work import WorkItem, WorkPriority
from phynai.orchestrator.queue import WorkQueue


def _item(prompt: str = "do something", priority: WorkPriority = WorkPriority.normal, item_id: str | None = None) -> WorkItem:
    kw = {"prompt": prompt, "priority": priority}
    if item_id:
        kw["id"] = item_id
    return WorkItem(**kw)


class TestWorkQueue:
    def test_push_and_pop_returns_item(self):
        q = WorkQueue()
        item = _item("hello")
        q.push(item)
        got = q.pop()
        assert got is not None
        assert got.id == item.id

    def test_priority_ordering(self):
        q = WorkQueue()
        low = _item("low", WorkPriority.low, "low")
        crit = _item("crit", WorkPriority.critical, "crit")
        q.push(low)
        q.push(crit)
        first = q.pop()
        assert first is not None
        assert first.id == "crit"

    def test_fifo_within_same_priority(self):
        q = WorkQueue()
        a = _item("a", WorkPriority.normal, "a")
        b = _item("b", WorkPriority.normal, "b")
        q.push(a)
        q.push(b)
        assert q.pop().id == "a"
        assert q.pop().id == "b"

    def test_pop_returns_none_when_empty(self):
        q = WorkQueue()
        assert q.pop() is None

    def test_lock_prevents_pop(self):
        q = WorkQueue()
        item = _item("x", item_id="x")
        q.push(item)
        q.lock("x")
        assert q.pop() is None

    def test_release_allows_pop(self):
        q = WorkQueue()
        item = _item("x", item_id="x")
        q.push(item)
        q.lock("x")
        assert q.pop() is None
        q.release("x")
        got = q.pop()
        assert got is not None
        assert got.id == "x"

    def test_size(self):
        q = WorkQueue()
        assert q.size() == 0
        q.push(_item("a"))
        q.push(_item("b"))
        assert q.size() == 2

    def test_peek_does_not_remove(self):
        q = WorkQueue()
        item = _item("peek", item_id="peek")
        q.push(item)
        peeked = q.peek()
        assert peeked is not None
        assert peeked.id == "peek"
        assert q.size() == 1
        popped = q.pop()
        assert popped is not None
        assert popped.id == "peek"
