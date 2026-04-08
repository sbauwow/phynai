"""@tool decorator for clean tool registration."""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from phynai.contracts.tools import Risk, ToolHandler, ToolMetadata, ToolResult


def tool(
    name: str,
    description: str,
    risk: Risk = Risk.LOW,
    mutates: bool = False,
    capabilities: list[str] | None = None,
    requires_confirmation: bool = False,
    parameters: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Callable:
    """Decorator that marks an async function as a tool handler.

    Stores ToolMetadata on the function as ``._tool_metadata`` and the
    original callable as ``._tool_handler`` so discovery helpers can
    find and register them automatically.
    """

    metadata = ToolMetadata(
        name=name,
        description=description,
        risk=risk,
        mutates=mutates,
        capabilities=capabilities or [],
        requires_confirmation=requires_confirmation,
        parameters=parameters or {},
        tags=tags or [],
    )

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(arguments: dict[str, Any]) -> ToolResult:
            return await fn(arguments)

        wrapper._tool_metadata = metadata  # type: ignore[attr-defined]
        wrapper._tool_handler = fn  # type: ignore[attr-defined]
        return wrapper

    return decorator


def discover_tools(module: Any) -> list[tuple[ToolHandler, ToolMetadata]]:
    """Scan a module for @tool-decorated functions.

    Returns a list of (handler, metadata) tuples ready for registration.
    """
    tools: list[tuple[ToolHandler, ToolMetadata]] = []
    for _name, obj in inspect.getmembers(module, callable):
        meta: ToolMetadata | None = getattr(obj, "_tool_metadata", None)
        if meta is not None:
            tools.append((obj, meta))
    return tools


def register_all(runtime: Any, module: Any) -> None:
    """Discover all @tool-decorated functions in *module* and register them
    with the given :class:`PhynaiToolRuntime`."""
    for handler, meta in discover_tools(module):
        runtime.register(meta.name, handler, meta)
