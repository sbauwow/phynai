"""Agent-core protocols — Layer 3 interfaces.

These protocols define what an agent, its LLM client, context manager,
session store, and cost ledger must look like. Implementations live
elsewhere; this module is pure contract.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from phynai.contracts.work import CostRecord, WorkItem, WorkResult


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

@runtime_checkable
class AgentCore(Protocol):
    """The main agent execution loop."""

    async def run(self, work_item: WorkItem) -> WorkResult:
        """Execute a WorkItem and return its result."""
        ...


# ---------------------------------------------------------------------------
# LLM client abstraction
# ---------------------------------------------------------------------------

@runtime_checkable
class ClientManager(Protocol):
    """Manages LLM provider connections and completions."""

    async def create_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AsyncIterator[Any] | dict[str, Any]:
        """Send a completion request. Returns a stream or a single response."""
        ...

    def list_models(self) -> list[str]:
        """Return available model identifiers."""
        ...


# ---------------------------------------------------------------------------
# Context / prompt management
# ---------------------------------------------------------------------------

@runtime_checkable
class ContextManager(Protocol):
    """Builds, compresses, and enriches the message context for the LLM."""

    def build_system_prompt(self, work_item: WorkItem) -> str:
        """Generate the system prompt for a given work item."""
        ...

    def compress(
        self, messages: list[dict[str, Any]], target_tokens: int
    ) -> list[dict[str, Any]]:
        """Compress a message history to fit within a token budget."""
        ...

    # inject_memory() — reserved for future long-term memory integration


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

@runtime_checkable
class SessionStore(Protocol):
    """Persists and retrieves conversation sessions."""

    async def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        """Persist a session's messages and metadata."""
        ...

    async def load(
        self, session_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """Load a session. Returns None if not found."""
        ...

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions, newest first."""
        ...

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search sessions by content or metadata."""
        ...


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

@runtime_checkable
class CostLedger(Protocol):
    """Accumulates and queries cost records."""

    def record(self, work_id: str, cost: CostRecord) -> None:
        """Record a cost entry for a work item."""
        ...

    def total(self, session_id: str | None = None) -> CostRecord:
        """Aggregate cost. Optionally scoped to a session."""
        ...

    def by_model(self) -> dict[str, CostRecord]:
        """Return aggregated costs keyed by model name."""
        ...
