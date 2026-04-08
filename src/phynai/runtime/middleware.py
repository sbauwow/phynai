"""Middleware chain — ordered PRE / POST / ERROR middleware execution."""

from __future__ import annotations

from phynai.contracts.middleware import (
    Middleware,
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
)


class MiddlewareChain:
    """Manages and executes an ordered list of middleware.

    Middleware is categorised by phase (PRE, POST, ERROR). Execution:
    - PRE: runs in order; short-circuits when a middleware returns proceed=False.
    - POST: runs in order; no short-circuit.
    - ERROR: runs in order; no short-circuit.
    """

    def __init__(self) -> None:
        self._middleware: list[Middleware] = []

    def use(self, middleware: Middleware) -> None:
        """Append middleware to the chain."""
        self._middleware.append(middleware)

    def remove(self, phase: MiddlewarePhase, index: int) -> None:
        """Remove a middleware by phase and positional index within that phase.

        Args:
            phase: The phase to filter by.
            index: Zero-based index among middleware of the given phase.

        Raises:
            IndexError: If the index is out of range for that phase.
        """
        phase_indices = [
            i for i, m in enumerate(self._middleware) if m.phase == phase
        ]
        if index < 0 or index >= len(phase_indices):
            raise IndexError(
                f"Index {index} out of range for phase {phase.value} "
                f"(have {len(phase_indices)} middleware)"
            )
        self._middleware.pop(phase_indices[index])

    async def run_pre(self, ctx: MiddlewareContext) -> MiddlewareResult:
        """Run all PRE-phase middleware in order.

        Short-circuits and returns immediately if any middleware sets proceed=False.
        """
        for mw in self._middleware:
            if mw.phase == MiddlewarePhase.PRE:
                result = mw(ctx)
                if not result.proceed:
                    return result
        return MiddlewareResult(proceed=True)

    async def run_post(self, ctx: MiddlewareContext) -> None:
        """Run all POST-phase middleware in order (no short-circuit)."""
        for mw in self._middleware:
            if mw.phase == MiddlewarePhase.POST:
                mw(ctx)

    async def run_error(self, ctx: MiddlewareContext, error: Exception) -> None:
        """Run all ERROR-phase middleware in order (no short-circuit).

        The error is attached to ctx.extra['error'] before middleware runs.
        """
        ctx.extra["error"] = str(error)
        ctx.extra["error_type"] = type(error).__name__
        for mw in self._middleware:
            if mw.phase == MiddlewarePhase.ERROR:
                mw(ctx)

    def __len__(self) -> int:
        return len(self._middleware)

    def __repr__(self) -> str:
        counts = {}
        for mw in self._middleware:
            counts[mw.phase.value] = counts.get(mw.phase.value, 0) + 1
        return f"MiddlewareChain({counts})"
