"""Tests for EventBus and ExecutionJournal."""

import pytest

from phynai.contracts.events import Event, EventType
from phynai.runtime.events import EventBus, ExecutionJournal


def _make_event(
    event_type: EventType = EventType.tool_requested,
    tool_name: str = "echo",
) -> Event:
    return Event(
        event_type=event_type,
        source="test",
        payload={"tool_name": tool_name},
    )


# ---- EventBus tests ----


class TestEventBusOnEmit:
    def test_on_emit_fires_callback(self):
        bus = EventBus()
        received: list[Event] = []
        bus.on(EventType.tool_requested, lambda e: received.append(e))
        evt = _make_event(EventType.tool_requested)
        bus.emit(evt)
        assert len(received) == 1
        assert received[0] is evt


class TestEventBusOff:
    def test_off_removes_callback(self):
        bus = EventBus()
        received: list[Event] = []
        cb = lambda e: received.append(e)
        bus.on(EventType.tool_started, cb)
        bus.off(EventType.tool_started, cb)
        bus.emit(_make_event(EventType.tool_started))
        assert received == []

    def test_off_unregistered_raises_value_error(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="not registered"):
            bus.off(EventType.tool_started, lambda e: None)


class TestEventBusMultipleListeners:
    def test_multiple_listeners(self):
        bus = EventBus()
        results_a: list[str] = []
        results_b: list[str] = []
        bus.on(EventType.tool_completed, lambda e: results_a.append("a"))
        bus.on(EventType.tool_completed, lambda e: results_b.append("b"))
        bus.emit(_make_event(EventType.tool_completed))
        assert results_a == ["a"]
        assert results_b == ["b"]


class TestEventBusClear:
    def test_clear_removes_all_listeners(self):
        bus = EventBus()
        received: list[Event] = []
        bus.on(EventType.tool_requested, lambda e: received.append(e))
        bus.on(EventType.tool_completed, lambda e: received.append(e))
        bus.clear()
        bus.emit(_make_event(EventType.tool_requested))
        bus.emit(_make_event(EventType.tool_completed))
        assert received == []


# ---- ExecutionJournal tests ----


class TestJournalRecordAndCount:
    def test_record_and_count(self):
        journal = ExecutionJournal()
        assert journal.count() == 0
        journal.record(_make_event())
        journal.record(_make_event())
        assert journal.count() == 2


class TestJournalQueryByEventType:
    def test_query_by_event_type(self):
        journal = ExecutionJournal()
        journal.record(_make_event(EventType.tool_requested))
        journal.record(_make_event(EventType.tool_completed))
        journal.record(_make_event(EventType.tool_requested))

        results = journal.query(event_type=EventType.tool_requested)
        assert len(results) == 2
        assert all(e.event_type == EventType.tool_requested for e in results)


class TestJournalQueryByToolName:
    def test_query_by_tool_name(self):
        journal = ExecutionJournal()
        journal.record(_make_event(tool_name="echo"))
        journal.record(_make_event(tool_name="shell"))
        journal.record(_make_event(tool_name="echo"))

        results = journal.query(tool_name="echo")
        assert len(results) == 2
        results_shell = journal.query(tool_name="shell")
        assert len(results_shell) == 1


class TestJournalToList:
    def test_to_list_serialization(self):
        journal = ExecutionJournal()
        evt = _make_event(EventType.tool_started, tool_name="calc")
        journal.record(evt)
        serialized = journal.to_list()
        assert isinstance(serialized, list)
        assert len(serialized) == 1
        d = serialized[0]
        assert d["event_type"] == "tool_started"
        assert d["source"] == "test"
        assert d["payload"]["tool_name"] == "calc"
        assert "timestamp" in d


class TestJournalClear:
    def test_clear_removes_all_events(self):
        journal = ExecutionJournal()
        journal.record(_make_event())
        journal.record(_make_event())
        assert journal.count() == 2
        journal.clear()
        assert journal.count() == 0
        assert journal.to_list() == []
