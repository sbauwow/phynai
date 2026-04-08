"""Middleware contracts — context, results, and middleware protocol."""

from __future__ import annotations

import enum
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from phynai.contracts.tools import ToolCall, ToolMetadata


class MiddlewarePhase(str, enum.Enum):
    """Phase at which middleware runs."""

    PRE = "PRE"
    POST = "POST"
    ERROR = "ERROR"


class MiddlewareContext(BaseModel):
    """Context passed into a middleware invocation."""

    model_config = {"arbitrary_types_allowed": True}

    tool_call: ToolCall
    metadata: ToolMetadata
    phase: MiddlewarePhase
    agent_id: str
    session_id: str
    extra: dict[str, Any] = Field(default_factory=dict)


class MiddlewareResult(BaseModel):
    """Result returned from a middleware invocation."""

    proceed: bool = True
    modified_call: Optional[ToolCall] = None
    reason: Optional[str] = None


@runtime_checkable
class Middleware(Protocol):
    """Protocol for middleware that intercepts tool calls."""

    @property
    def phase(self) -> MiddlewarePhase: ...

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult: ...
