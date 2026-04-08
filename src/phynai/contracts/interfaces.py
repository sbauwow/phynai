"""Interface protocols — Layer 5 contracts.

These define how external users and systems interact with the agent:
CLI, chat gateways (Telegram, Discord, Slack, …), and HTTP APIs.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

@runtime_checkable
class Interface(Protocol):
    """Common lifecycle for any user-facing interface."""

    async def start(self) -> None:
        """Initialise and begin accepting input."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the interface."""
        ...


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@runtime_checkable
class CLIInterface(Interface, Protocol):
    """Interactive terminal REPL."""

    async def repl(self) -> None:
        """Run the read-eval-print loop until exit."""
        ...


# ---------------------------------------------------------------------------
# Chat gateways (Telegram, Discord, Slack, …)
# ---------------------------------------------------------------------------

@runtime_checkable
class GatewayInterface(Interface, Protocol):
    """A bidirectional chat-platform gateway."""

    @property
    def platform(self) -> str:
        """Platform identifier, e.g. 'telegram', 'discord', 'slack'."""
        ...

    async def send(self, message: str, chat_id: str) -> None:
        """Send a message to a specific chat/channel."""
        ...

    def on_message(self, callback: Callable[..., Any]) -> None:
        """Register a callback for incoming messages."""
        ...

