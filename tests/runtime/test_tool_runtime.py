"""Tests for PhynaiToolRuntime."""

from typing import Any

import pytest

from phynai.contracts.events import EventType
from phynai.contracts.middleware import (
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
)
from phynai.contracts.policy import PolicyDecision, PolicyVerdict
from phynai.contracts.tools import Risk, ToolCall, ToolMetadata, ToolResult
from phynai.runtime.tool_runtime import PhynaiToolRuntime


# --- Helpers ---


def _make_metadata(name: str = "echo") -> ToolMetadata:
    return ToolMetadata(
        name=name,
        description=f"A {name} tool",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["test"],
        requires_confirmation=False,
    )


def _make_call(tool_name: str = "echo", **kwargs) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        arguments=kwargs,
        trace_id="trace-1",
    )


def _echo_handler(arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(
        tool_name="echo",
        call_id="test",
        success=True,
        output=str(arguments),
        duration_ms=0.0,
    )


def _failing_handler(arguments: dict[str, Any]) -> ToolResult:
    raise RuntimeError("handler exploded")


# --- Mock policy ---


class DenyAllPolicy:
    @property
    def name(self) -> str:
        return "deny_all"

    def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
        return PolicyVerdict(
            decision=PolicyDecision.DENY,
            reason="no way",
            policy_name=self.name,
        )


# --- Mock middleware ---


class BlockingPreMiddleware:
    @property
    def phase(self) -> MiddlewarePhase:
        return MiddlewarePhase.PRE

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult(proceed=False, reason="middleware blocked")


@pytest.fixture
def runtime() -> PhynaiToolRuntime:
    return PhynaiToolRuntime()


class TestRegisterAndDispatch:
    @pytest.mark.asyncio
    async def test_register_and_dispatch(self, runtime: PhynaiToolRuntime):
        runtime.register("echo", _echo_handler, _make_metadata("echo"))
        call = _make_call("echo", msg="hello")
        result = await runtime.dispatch(call)
        assert result.success is True
        assert "hello" in result.output


class TestDispatchUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, runtime: PhynaiToolRuntime):
        call = _make_call("nonexistent")
        result = await runtime.dispatch(call)
        assert result.success is False
        assert "not registered" in result.error


class TestDispatchWithDenyPolicy:
    @pytest.mark.asyncio
    async def test_deny_policy_blocks_without_calling_handler(
        self, runtime: PhynaiToolRuntime
    ):
        call_count = 0

        def counting_handler(arguments: dict) -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                tool_name="echo",
                call_id="x",
                success=True,
                output="ok",
                duration_ms=0.0,
            )

        runtime.register("echo", counting_handler, _make_metadata("echo"))
        runtime.add_policy(DenyAllPolicy())
        call = _make_call("echo")
        result = await runtime.dispatch(call)
        assert result.success is False
        assert "Denied by policy" in result.error
        assert call_count == 0


class TestDispatchRecordsEvents:
    @pytest.mark.asyncio
    async def test_events_recorded_in_journal(self, runtime: PhynaiToolRuntime):
        runtime.register("echo", _echo_handler, _make_metadata("echo"))
        call = _make_call("echo")
        await runtime.dispatch(call)
        assert runtime.journal.count() > 0
        # Should have tool_requested, tool_permitted, tool_started, tool_completed
        types = {e.event_type for e in runtime.journal.query()}
        assert EventType.tool_requested in types
        assert EventType.tool_completed in types


class TestDispatchWithPreMiddlewareBlocking:
    @pytest.mark.asyncio
    async def test_pre_middleware_blocks(self, runtime: PhynaiToolRuntime):
        runtime.register("echo", _echo_handler, _make_metadata("echo"))
        runtime.use(BlockingPreMiddleware())
        call = _make_call("echo")
        result = await runtime.dispatch(call)
        assert result.success is False
        assert "middleware" in result.error.lower()


class TestDispatchMeasuresDuration:
    @pytest.mark.asyncio
    async def test_duration_is_set(self, runtime: PhynaiToolRuntime):
        runtime.register("echo", _echo_handler, _make_metadata("echo"))
        call = _make_call("echo")
        result = await runtime.dispatch(call)
        assert result.success is True
        assert result.duration_ms >= 0.0


class TestDispatchHandlerException:
    @pytest.mark.asyncio
    async def test_exception_returns_failed_result(self, runtime: PhynaiToolRuntime):
        runtime.register("boom", _failing_handler, _make_metadata("boom"))
        call = _make_call("boom")
        result = await runtime.dispatch(call)
        assert result.success is False
        assert "handler exploded" in result.error
        # Journal should record tool_failed
        failed = runtime.journal.query(event_type=EventType.tool_failed)
        assert len(failed) >= 1
