"""Tool contracts — metadata, calls, results, and handler protocol."""

from __future__ import annotations

import enum
import uuid
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Risk(str, enum.Enum):
    """Risk level associated with a tool."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolMetadata(BaseModel):
    """Declarative description of a registered tool."""

    name: str
    description: str
    risk: Risk
    mutates: bool
    capabilities: list[str]
    requires_confirmation: bool
    parameters: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for tool parameters")
    tags: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    """A request to invoke a tool."""

    tool_name: str
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    arguments: dict[str, Any]
    trace_id: str


class ToolResult(BaseModel):
    """The outcome of a tool invocation."""

    tool_name: str
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    success: bool
    output: str
    error: Optional[str] = None
    duration_ms: float
    artifacts: list[str] = Field(default_factory=list)


@runtime_checkable
class ToolHandler(Protocol):
    """Protocol for a callable that executes a tool."""

    def __call__(self, arguments: dict[str, Any]) -> ToolResult: ...
