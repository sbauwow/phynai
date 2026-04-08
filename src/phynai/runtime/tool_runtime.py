"""PhynaiToolRuntime — the concrete Layer 2 tool execution runtime."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

from phynai.contracts.events import Event, EventType, ToolEvent
from phynai.contracts.middleware import Middleware, MiddlewareContext, MiddlewarePhase
from phynai.contracts.policy import PolicyCheck, PolicyDecision
from phynai.contracts.tools import ToolCall, ToolHandler, ToolMetadata, ToolResult

from phynai.runtime.events import EventBus, ExecutionJournal
from phynai.runtime.middleware import MiddlewareChain
from phynai.runtime.policy import PolicyPipeline
from phynai.runtime.registry import ToolRegistry


class PhynaiToolRuntime:
    """Concrete implementation of the ToolRuntime protocol.

    Composes ToolRegistry, PolicyPipeline, MiddlewareChain, EventBus, and
    ExecutionJournal into a single dispatch surface that executes tool calls
    through the full pipeline: policy → middleware → handler → events.
    """

    def __init__(self) -> None:
        self._registry = ToolRegistry()
        self._policy_pipeline = PolicyPipeline()
        self._middleware_chain = MiddlewareChain()
        self._event_bus = EventBus()
        self._journal = ExecutionJournal()

    # -- Properties ----------------------------------------------------------

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def policy_pipeline(self) -> PolicyPipeline:
        return self._policy_pipeline

    @property
    def middleware_chain(self) -> MiddlewareChain:
        return self._middleware_chain

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def journal(self) -> ExecutionJournal:
        return self._journal

    # -- Registration --------------------------------------------------------

    def register(self, name: str, handler: ToolHandler, metadata: ToolMetadata) -> None:
        """Register a tool with its handler and metadata."""
        self._registry.register(name, handler, metadata)

    def unregister(self, name: str) -> None:
        """Remove a previously registered tool."""
        self._registry.unregister(name)

    # -- Delegation helpers --------------------------------------------------

    def use(self, middleware: Middleware) -> None:
        """Add middleware to the processing pipeline."""
        self._middleware_chain.use(middleware)

    def add_policy(self, policy: PolicyCheck) -> None:
        """Add a policy check to the evaluation chain."""
        self._policy_pipeline.add(policy)

    def list_tools(self) -> list[ToolMetadata]:
        """Return metadata for all registered tools."""
        return self._registry.list_tools()

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        """Return metadata for a specific tool, or None if not found."""
        return self._registry.get_metadata(name)

    # -- Dispatch ------------------------------------------------------------

    async def dispatch(self, call: ToolCall) -> ToolResult:
        """Execute a tool call through the full pipeline.

        Steps:
            1. Look up tool in registry
            2. Emit tool_requested
            3. Evaluate policies (DENY → error result)
            4. Emit tool_permitted
            5. Run PRE middleware (proceed=False → error result)
            6. Emit tool_started
            7. Invoke handler and measure duration
            8. Emit tool_completed
            9. Run POST middleware
            10. Record all events in journal
            11. Return result
        """
        pending_events: list[Event] = []

        def _tool_event(etype: EventType, **extra) -> ToolEvent:
            evt = ToolEvent(
                event_type=etype,
                source="PhynaiToolRuntime",
                trace_id=call.trace_id,
                tool_name=call.tool_name,
                call_id=call.call_id,
                payload={"tool_name": call.tool_name, **extra},
            )
            self._event_bus.emit(evt)
            pending_events.append(evt)
            return evt

        # 1. Registry lookup
        entry = self._registry.get(call.tool_name)
        if entry is None:
            _tool_event(EventType.tool_failed, error=f"Tool '{call.tool_name}' not found")
            for evt in pending_events:
                self._journal.record(evt)
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                output="",
                error=f"Tool '{call.tool_name}' is not registered",
                duration_ms=0.0,
            )

        handler, metadata = entry

        try:
            # 2. Emit tool_requested
            _tool_event(EventType.tool_requested)

            # 3. Policy evaluation
            verdict = self._policy_pipeline.evaluate(call, metadata)
            if verdict.decision == PolicyDecision.DENY:
                _tool_event(
                    EventType.tool_denied,
                    reason=verdict.reason,
                    policy=verdict.policy_name,
                )
                for evt in pending_events:
                    self._journal.record(evt)
                return ToolResult(
                    tool_name=call.tool_name,
                    call_id=call.call_id,
                    success=False,
                    output="",
                    error=f"Denied by policy '{verdict.policy_name}': {verdict.reason}",
                    duration_ms=0.0,
                )

            # 4. Emit tool_permitted
            _tool_event(EventType.tool_permitted)

            # 4b. Validate arguments against declared schema
            schema = metadata.parameters
            if schema and schema.get("required"):
                missing = [
                    f for f in schema["required"]
                    if f not in call.arguments
                ]
                if missing:
                    error_msg = f"Missing required arguments: {', '.join(missing)}"
                    logger.warning("Schema validation failed for %s: %s", call.tool_name, error_msg)
                    _tool_event(EventType.tool_failed, error=error_msg)
                    for evt in pending_events:
                        self._journal.record(evt)
                    return ToolResult(
                        tool_name=call.tool_name,
                        call_id=call.call_id,
                        success=False,
                        output="",
                        error=error_msg,
                        duration_ms=0.0,
                    )

            # 5. PRE middleware
            pre_ctx = MiddlewareContext(
                tool_call=call,
                metadata=metadata,
                phase=MiddlewarePhase.PRE,
                agent_id="default",
                session_id=call.trace_id,
            )
            pre_result = await self._middleware_chain.run_pre(pre_ctx)
            if not pre_result.proceed:
                for evt in pending_events:
                    self._journal.record(evt)
                return ToolResult(
                    tool_name=call.tool_name,
                    call_id=call.call_id,
                    success=False,
                    output="",
                    error=f"Blocked by middleware: {pre_result.reason}",
                    duration_ms=0.0,
                )

            # 6. Emit tool_started
            _tool_event(EventType.tool_started)

            # 7. Invoke handler
            start = time.perf_counter()
            result = await handler(call.arguments)
            duration_ms = (time.perf_counter() - start) * 1000.0

            # Ensure duration is recorded
            result = result.model_copy(update={"duration_ms": duration_ms})

            # 8. Emit tool_completed
            _tool_event(EventType.tool_completed, duration_ms=duration_ms)

            # 9. POST middleware
            post_ctx = MiddlewareContext(
                tool_call=call,
                metadata=metadata,
                phase=MiddlewarePhase.POST,
                agent_id="default",
                session_id=call.trace_id,
            )
            await self._middleware_chain.run_post(post_ctx)

            # 10. Record events
            for evt in pending_events:
                self._journal.record(evt)

            # 11. Return result
            return result

        except Exception as exc:
            # Emit tool_failed
            _tool_event(EventType.tool_failed, error=str(exc))

            # Run ERROR middleware
            err_ctx = MiddlewareContext(
                tool_call=call,
                metadata=metadata,
                phase=MiddlewarePhase.ERROR,
                agent_id="default",
                session_id=call.trace_id,
            )
            await self._middleware_chain.run_error(err_ctx, exc)

            # Record events
            for evt in pending_events:
                self._journal.record(evt)

            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                output="",
                error=str(exc),
                duration_ms=0.0,
            )

    def __repr__(self) -> str:
        return (
            f"PhynaiToolRuntime(tools={len(self._registry)}, "
            f"policies={len(self._policy_pipeline)}, "
            f"middleware={len(self._middleware_chain)})"
        )
