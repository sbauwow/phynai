"""Tests for MiddlewareChain."""

import pytest

from phynai.contracts.middleware import (
    Middleware,
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
)
from phynai.contracts.tools import Risk, ToolCall, ToolMetadata
from phynai.runtime.middleware import MiddlewareChain


# --- Mock Middleware implementations ---


class TrackingPreMiddleware:
    """PRE middleware that records calls in a list and proceeds."""

    def __init__(self, label: str, tracker: list):
        self._label = label
        self._tracker = tracker

    @property
    def phase(self) -> MiddlewarePhase:
        return MiddlewarePhase.PRE

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
        self._tracker.append(f"pre:{self._label}")
        return MiddlewareResult(proceed=True)


class BlockingPreMiddleware:
    """PRE middleware that blocks execution."""

    @property
    def phase(self) -> MiddlewarePhase:
        return MiddlewarePhase.PRE

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult(proceed=False, reason="blocked by test")


class TrackingPostMiddleware:
    """POST middleware that records calls."""

    def __init__(self, label: str, tracker: list):
        self._label = label
        self._tracker = tracker

    @property
    def phase(self) -> MiddlewarePhase:
        return MiddlewarePhase.POST

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
        self._tracker.append(f"post:{self._label}")
        return MiddlewareResult(proceed=True)


class TrackingErrorMiddleware:
    """ERROR middleware that records calls."""

    def __init__(self, label: str, tracker: list):
        self._label = label
        self._tracker = tracker

    @property
    def phase(self) -> MiddlewarePhase:
        return MiddlewarePhase.ERROR

    def __call__(self, ctx: MiddlewareContext) -> MiddlewareResult:
        self._tracker.append(f"error:{self._label}")
        return MiddlewareResult(proceed=True)


def _make_context(phase: MiddlewarePhase = MiddlewarePhase.PRE) -> MiddlewareContext:
    call = ToolCall(tool_name="echo", arguments={}, trace_id="trace-1")
    meta = ToolMetadata(
        name="echo",
        description="test",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["test"],
        requires_confirmation=False,
    )
    return MiddlewareContext(
        tool_call=call,
        metadata=meta,
        phase=phase,
        agent_id="test-agent",
        session_id="session-1",
    )


@pytest.fixture
def chain() -> MiddlewareChain:
    return MiddlewareChain()


class TestEmptyChainPre:
    @pytest.mark.asyncio
    async def test_empty_chain_returns_proceed_true(self, chain: MiddlewareChain):
        ctx = _make_context(MiddlewarePhase.PRE)
        result = await chain.run_pre(ctx)
        assert result.proceed is True


class TestPreMiddlewareOrder:
    @pytest.mark.asyncio
    async def test_pre_called_in_order(self, chain: MiddlewareChain):
        tracker: list[str] = []
        chain.use(TrackingPreMiddleware("first", tracker))
        chain.use(TrackingPreMiddleware("second", tracker))
        ctx = _make_context(MiddlewarePhase.PRE)
        result = await chain.run_pre(ctx)
        assert result.proceed is True
        assert tracker == ["pre:first", "pre:second"]


class TestPreShortCircuit:
    @pytest.mark.asyncio
    async def test_pre_short_circuits_on_proceed_false(self, chain: MiddlewareChain):
        tracker: list[str] = []
        chain.use(TrackingPreMiddleware("first", tracker))
        chain.use(BlockingPreMiddleware())
        chain.use(TrackingPreMiddleware("after_block", tracker))
        ctx = _make_context(MiddlewarePhase.PRE)
        result = await chain.run_pre(ctx)
        assert result.proceed is False
        assert result.reason == "blocked by test"
        # "after_block" should NOT have been called
        assert "pre:after_block" not in tracker
        assert tracker == ["pre:first"]


class TestPostMiddleware:
    @pytest.mark.asyncio
    async def test_post_middleware_runs(self, chain: MiddlewareChain):
        tracker: list[str] = []
        chain.use(TrackingPostMiddleware("one", tracker))
        chain.use(TrackingPostMiddleware("two", tracker))
        ctx = _make_context(MiddlewarePhase.POST)
        await chain.run_post(ctx)
        assert tracker == ["post:one", "post:two"]


class TestErrorMiddleware:
    @pytest.mark.asyncio
    async def test_error_middleware_runs(self, chain: MiddlewareChain):
        tracker: list[str] = []
        chain.use(TrackingErrorMiddleware("err1", tracker))
        chain.use(TrackingErrorMiddleware("err2", tracker))
        ctx = _make_context(MiddlewarePhase.ERROR)
        await chain.run_error(ctx, RuntimeError("boom"))
        assert tracker == ["error:err1", "error:err2"]
        assert ctx.extra["error"] == "boom"
        assert ctx.extra["error_type"] == "RuntimeError"


class TestUse:
    def test_use_adds_middleware(self, chain: MiddlewareChain):
        assert len(chain) == 0
        chain.use(BlockingPreMiddleware())
        assert len(chain) == 1
        tracker: list[str] = []
        chain.use(TrackingPostMiddleware("p", tracker))
        assert len(chain) == 2
