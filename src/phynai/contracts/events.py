"""Typed runtime events for the PhynAI agent system."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, enum.Enum):
    """Categories of events emitted during agent and tool execution."""

    tool_requested = "tool_requested"
    tool_permitted = "tool_permitted"
    tool_denied = "tool_denied"
    tool_started = "tool_started"
    tool_completed = "tool_completed"
    tool_failed = "tool_failed"
    agent_started = "agent_started"
    agent_completed = "agent_completed"
    work_started = "work_started"
    work_completed = "work_completed"


class Event(BaseModel):
    """Base event emitted by the runtime."""

    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ToolEvent(Event):
    """Event specific to tool invocations."""

    tool_name: str
    call_id: str
    duration_ms: Optional[float] = None
    error: Optional[str] = None
