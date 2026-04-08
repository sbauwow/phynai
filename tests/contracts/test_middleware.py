"""Tests for phynai.contracts.middleware — phases, context, results, protocol."""

import pytest

from phynai.contracts import (
    Middleware,
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
    Risk,
    ToolCall,
    ToolMetadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call() -> ToolCall:
    return ToolCall(tool_name="bash", arguments={"cmd": "ls"}, trace_id="trace-1")


def _make_tool_metadata() -> ToolMetadata:
    return ToolMetadata(
        name="bash",
        description="shell",
        risk=Risk.HIGH,
        mutates=True,
        capabilities=["execute"],
        requires_confirmation=True,
    )


# ---------------------------------------------------------------------------
# MiddlewarePhase enum
# ---------------------------------------------------------------------------

def test_middleware_phase_enum_values():
    assert set(MiddlewarePhase) == {MiddlewarePhase.PRE, MiddlewarePhase.POST, MiddlewarePhase.ERROR}
    assert MiddlewarePhase.PRE.value == "PRE"
    assert MiddlewarePhase.POST.value == "POST"
    assert MiddlewarePhase.ERROR.value == "ERROR"


# ---------------------------------------------------------------------------
# MiddlewareContext
# ---------------------------------------------------------------------------

def test_middleware_context_creation():
    ctx = MiddlewareContext(
        tool_call=_make_tool_call(),
        metadata=_make_tool_metadata(),
        phase=MiddlewarePhase.PRE,
        agent_id="agent-1",
        session_id="session-1",
    )
    assert ctx.tool_call.tool_name == "bash"
    assert ctx.metadata.name == "bash"
    assert ctx.phase == MiddlewarePhase.PRE
    assert ctx.agent_id == "agent-1"
    assert ctx.session_id == "session-1"
    assert ctx.extra == {}


def test_middleware_context_with_extra():
    ctx = MiddlewareContext(
        tool_call=_make_tool_call(),
        metadata=_make_tool_metadata(),
        phase=MiddlewarePhase.POST,
        agent_id="a",
        session_id="s",
        extra={"key": "value"},
    )
    assert ctx.extra == {"key": "value"}


# ---------------------------------------------------------------------------
# MiddlewareResult
# ---------------------------------------------------------------------------

def test_middleware_result_defaults():
    r = MiddlewareResult()
    assert r.proceed is True
    assert r.modified_call is None
    assert r.reason is None


def test_middleware_result_deny():
    r = MiddlewareResult(proceed=False, reason="blocked by policy")
    assert r.proceed is False
    assert r.reason == "blocked by policy"


def test_middleware_result_with_modified_call():
    modified = ToolCall(tool_name="safe_bash", arguments={"cmd": "echo hi"}, trace_id="t")
    r = MiddlewareResult(proceed=True, modified_call=modified)
    assert r.modified_call is not None
    assert r.modified_call.tool_name == "safe_bash"


# ---------------------------------------------------------------------------
# Middleware protocol
# ---------------------------------------------------------------------------

def test_middleware_protocol_isinstance():
    """A class with phase property and __call__ satisfies Middleware protocol."""

    class LoggingMiddleware:
        @property
        def phase(self) -> MiddlewarePhase:
            return MiddlewarePhase.PRE

        def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
            return MiddlewareResult()

    mw = LoggingMiddleware()
    assert isinstance(mw, Middleware)


def test_non_middleware_fails_isinstance():

    class NotMiddleware:
        pass

    assert not isinstance(NotMiddleware(), Middleware)
