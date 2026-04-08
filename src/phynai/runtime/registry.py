"""Tool registry — per-agent tool registration and lookup."""

from __future__ import annotations

from typing import Any

from phynai.contracts.tools import ToolHandler, ToolMetadata, ToolResult


class ToolRegistry:
    """Manages registered tools and their metadata.

    Each agent instance gets its own ToolRegistry — this is NOT a singleton.
    Tools are stored as (handler, metadata) tuples keyed by name.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolHandler, ToolMetadata]] = {}

    def register(self, name: str, handler: ToolHandler, metadata: ToolMetadata) -> None:
        """Register a tool with its handler and metadata.

        Args:
            name: Unique tool name.
            handler: Callable that executes the tool.
            metadata: Declarative description of the tool.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = (handler, metadata)

    def unregister(self, name: str) -> None:
        """Remove a previously registered tool.

        Args:
            name: Name of the tool to remove.

        Raises:
            KeyError: If the tool is not registered.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered")
        del self._tools[name]

    def get(self, name: str) -> tuple[ToolHandler, ToolMetadata] | None:
        """Return the (handler, metadata) tuple for a tool, or None."""
        return self._tools.get(name)

    def get_metadata(self, name: str) -> ToolMetadata | None:
        """Return metadata for a specific tool, or None if not found."""
        entry = self._tools.get(name)
        if entry is None:
            return None
        return entry[1]

    def list_tools(self) -> list[ToolMetadata]:
        """Return metadata for all registered tools."""
        return [metadata for _, metadata in self._tools.values()]

    def has(self, name: str) -> bool:
        """Check whether a tool is registered."""
        return name in self._tools

    def clear(self) -> None:
        """Remove all registered tools."""
        self._tools.clear()

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __repr__(self) -> str:
        names = list(self._tools.keys())
        return f"ToolRegistry(tools={names})"
