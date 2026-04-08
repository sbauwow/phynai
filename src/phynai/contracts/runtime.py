"""ToolRuntime protocol — the central dispatch surface."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from phynai.contracts.middleware import Middleware
from phynai.contracts.policy import PolicyCheck
from phynai.contracts.tools import ToolCall, ToolHandler, ToolMetadata, ToolResult


@runtime_checkable
class ToolRuntime(Protocol):
    """Protocol for the tool execution runtime.

    Implementations manage tool registration, middleware pipelines,
    policy checks, and dispatching calls to handlers.
    """

    def register(self, name: str, handler: ToolHandler, metadata: ToolMetadata) -> None:
        """Register a tool with its handler and metadata."""
        ...

    def unregister(self, name: str) -> None:
        """Remove a previously registered tool."""
        ...

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Execute a tool call through the full pipeline."""
        ...

    def use(self, middleware: Middleware) -> None:
        """Add middleware to the processing pipeline."""
        ...

    def add_policy(self, policy: PolicyCheck) -> None:
        """Add a policy check to the evaluation chain."""
        ...

    def list_tools(self) -> list[ToolMetadata]:
        """Return metadata for all registered tools."""
        ...

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        """Return metadata for a specific tool, or None if not found."""
        ...
