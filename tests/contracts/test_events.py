"""Tests for phynai.contracts.events — EventType, Event, ToolEvent."""

import uuid
from datetime import datetime, timezone

import pytest

from phynai.contracts import Event, EventType, ToolEvent


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------

def test_event_type_enum_values():
    expected = {
        "tool_requested", "tool_permitted", "tool_denied",
        "tool_started", "tool_completed", "tool_failed",
        "agent_started", "agent_completed",
        "work_started", "work_completed",
    }
    assert {e.value for e in EventType} == expected


def test_event_type_is_str_enum():
    assert isinstance(EventType.tool_requested, str)
    assert EventType.tool_requested == "tool_requested"


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

def test_event_creation_with_defaults():
    e = Event(event_type=EventType.agent_started, source="test")
    assert e.event_type == EventType.agent_started
    assert isinstance(e.timestamp, datetime)
    assert e.payload == {}
    assert e.source == "test"
    # trace_id should be a valid UUID
    uuid.UUID(e.trace_id)


def test_event_auto_timestamp():
    before = datetime.now(timezone.utc)
    e = Event(event_type=EventType.work_started, source="test")
    after = datetime.now(timezone.utc)
    assert before <= e.timestamp <= after


def test_event_unique_trace_ids():
    a = Event(event_type=EventType.agent_started, source="a")
    b = Event(event_type=EventType.agent_started, source="b")
    assert a.trace_id != b.trace_id


def test_event_with_payload():
    e = Event(
        event_type=EventType.tool_completed,
        source="runtime",
        payload={"tool": "bash", "exit_code": 0},
    )
    assert e.payload["tool"] == "bash"
    assert e.payload["exit_code"] == 0


def test_event_serialization_round_trip():
    e = Event(
        event_type=EventType.tool_failed,
        source="runtime",
        payload={"error": "timeout"},
    )
    data = e.model_dump()
    restored = Event.model_validate(data)
    assert restored.event_type == e.event_type
    assert restored.source == e.source
    assert restored.trace_id == e.trace_id
    assert restored.payload == e.payload
    assert restored.timestamp == e.timestamp


# ---------------------------------------------------------------------------
# ToolEvent
# ---------------------------------------------------------------------------

def test_tool_event_extends_event():
    te = ToolEvent(
        event_type=EventType.tool_started,
        source="runtime",
        tool_name="bash",
        call_id="call-123",
    )
    assert isinstance(te, Event)
    assert te.tool_name == "bash"
    assert te.call_id == "call-123"
    assert te.duration_ms is None
    assert te.error is None


def test_tool_event_with_all_fields():
    te = ToolEvent(
        event_type=EventType.tool_completed,
        source="runtime",
        tool_name="write",
        call_id="call-456",
        duration_ms=123.4,
        error=None,
        payload={"bytes_written": 1024},
    )
    assert te.duration_ms == 123.4
    assert te.payload["bytes_written"] == 1024


def test_tool_event_serialization_round_trip():
    te = ToolEvent(
        event_type=EventType.tool_failed,
        source="runtime",
        tool_name="bash",
        call_id="call-789",
        duration_ms=50.0,
        error="segfault",
    )
    data = te.model_dump()
    restored = ToolEvent.model_validate(data)
    assert restored.tool_name == te.tool_name
    assert restored.call_id == te.call_id
    assert restored.duration_ms == te.duration_ms
    assert restored.error == te.error
