"""PhynaiGateway — messaging platform adapters.

Provides an abstract base class for bidirectional chat-platform gateways
and a Slack implementation using slack-bolt async Socket Mode.

Implements the ``GatewayInterface`` protocol from
``phynai.contracts.interfaces``.
"""

from __future__ import annotations

import abc
import asyncio
import collections
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from phynai.contracts.work import WorkItem, WorkResult, WorkStatus

if TYPE_CHECKING:
    from phynai.contracts.agent import AgentCore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PhynaiGateway(abc.ABC):
    """Abstract base for messaging-platform gateways.

    Subclasses implement ``start``, ``stop``, ``send``, and the
    ``platform`` property.  Common logic for creating WorkItems,
    running them, and dispatching results lives here.

    Parameters
    ----------
    agent:
        Agent core conforming to :class:`AgentCore`.
    scheduler:
        Optional scheduler for work submission.
    """

    # Sliding-window rate limit: max N requests per window (seconds) per user
    _RATE_LIMIT_MAX: int = 5
    _RATE_LIMIT_WINDOW: float = 10.0  # 5 requests per 10 seconds per user

    def __init__(self, agent: AgentCore) -> None:
        self._agent = agent
        self._on_message_callback: Callable[..., Any] | None = None
        # user_id -> deque of timestamps within the current window
        self._rate_buckets: dict[str, collections.deque[float]] = {}

    # -------------------------------------------------------------------
    # Abstract interface
    # -------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def platform(self) -> str:
        """Platform identifier (e.g. ``'telegram'``, ``'discord'``)."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialise and begin accepting messages."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the gateway."""

    @abc.abstractmethod
    async def send(self, message: str, chat_id: str) -> None:
        """Send a message to a specific chat/channel."""

    # -------------------------------------------------------------------
    # Callback registration
    # -------------------------------------------------------------------

    def on_message(self, callback: Callable[..., Any]) -> None:
        """Register a callback for incoming messages."""
        self._on_message_callback = callback

    def _check_rate_limit(self, user_id: str) -> bool:
        """Return True if the request is allowed; False if rate-limited."""
        now = time.monotonic()
        window_start = now - self._RATE_LIMIT_WINDOW
        bucket = self._rate_buckets.setdefault(user_id, collections.deque())
        # Drop timestamps outside the window
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= self._RATE_LIMIT_MAX:
            return False
        bucket.append(now)
        return True

    # -------------------------------------------------------------------
    # Common logic
    # -------------------------------------------------------------------

    async def _handle_incoming(self, text: str, chat_id: str, user_id: str = "") -> None:
        """Process an incoming message: create WorkItem, run, reply."""
        work = WorkItem(
            prompt=text,
            source=self.platform,
            user_id=user_id,
            metadata={"chat_id": chat_id},
        )

        try:
            result = await self._agent.run(work)
            reply = result.response if result.status != WorkStatus.failed else (
                f"Error: {result.error or 'Unknown error'}"
            )
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.exception("Error processing message from %s: %s", chat_id, exc)
            reply = "Sorry, something went wrong. Please try again later."

        await self.send(reply, chat_id)

        if self._on_message_callback is not None:
            try:
                self._on_message_callback(text, chat_id, reply)
            except (RuntimeError, ValueError, TypeError) as exc:
                logger.exception("on_message callback failed: %s", exc)


# ---------------------------------------------------------------------------
# Slack implementation (Socket Mode — no public URL required)
# ---------------------------------------------------------------------------

class SlackGateway(PhynaiGateway):
    """Slack gateway using Bolt async + Socket Mode.

    Requires:
        SLACK_BOT_TOKEN   (xoxb-...)  — OAuth token with chat:write, im:history,
                                        app_mentions:read, channels:history scopes
        SLACK_APP_TOKEN   (xapp-...)  — App-level token with connections:write scope

    Parameters
    ----------
    agent:
        Agent core.
    bot_token:
        xoxb- Slack bot OAuth token.
    app_token:
        xapp- Slack app-level token for Socket Mode.
    allowed_users:
        Set of Slack user IDs to accept messages from.
        MUST be non-empty — the gateway will refuse to start with an empty allowlist.
    """

    def __init__(
        self,
        agent: AgentCore,
        bot_token: str = "",
        app_token: str = "",
        allowed_users: set[str] | None = None,
    ) -> None:
        super().__init__(agent)
        self._bot_token = bot_token
        self._app_token = app_token
        self._allowed_users: set[str] = allowed_users or set()
        self._handler: Any | None = None
        self._bolt_app: Any | None = None

    @property
    def platform(self) -> str:
        return "slack"

    async def start(self) -> None:
        """Start Slack Socket Mode gateway."""
        try:
            from slack_bolt.app.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            raise RuntimeError(
                "slack-bolt is required for the Slack gateway.\n"
                "Install it: pip install slack-bolt"
            )

        if not self._bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required")
        if not self._app_token:
            raise ValueError("SLACK_APP_TOKEN is required")
        if not self._allowed_users:
            raise ValueError(
                "SLACK_ALLOWED_USERS must be set — set it to a comma-separated list of "
                "Slack user IDs (e.g. U012AB3CD). The gateway will not start with an "
                "empty allowlist to prevent unauthorized access."
            )

        self._bolt_app = AsyncApp(token=self._bot_token)
        self._register_handlers(self._bolt_app)

        self._handler = AsyncSocketModeHandler(self._bolt_app, self._app_token)
        logger.info("Slack gateway starting in Socket Mode")
        print("PhynAI Slack gateway running (Socket Mode)")
        await self._handler.start_async()

    async def stop(self) -> None:
        """Disconnect Socket Mode handler."""
        if self._handler is not None:
            try:
                await self._handler.close_async()
            except Exception:
                pass
        logger.info("Slack gateway stopped")

    async def send(self, message: str, chat_id: str) -> None:
        """Send a message to a Slack channel or DM."""
        if self._bolt_app is None:
            raise RuntimeError("Gateway not started")
        # Chunk long messages — Slack limit is 40,000 chars
        chunks = [message[i:i + 3900] for i in range(0, len(message), 3900)]
        for chunk in chunks:
            await self._bolt_app.client.chat_postMessage(
                channel=chat_id,
                text=chunk,
            )

    def _register_handlers(self, app: Any) -> None:
        """Register Bolt event handlers for mentions and DMs."""

        @app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            user = event.get("user", "")
            if user not in self._allowed_users:
                await say("Sorry, you are not authorized to use this bot.")
                return
            if not self._check_rate_limit(user):
                await say("You are sending too many requests. Please wait a moment.")
                return
            # Strip the bot mention prefix (<@BOTID> ...) from the text
            text = event.get("text", "")
            channel = event.get("channel", "")
            clean = _strip_mention(text)
            if clean:
                await self._handle_incoming(clean, channel, user_id=user)

        @app.event("message")
        async def handle_dm(message: dict, say: Any) -> None:
            # Only respond to DMs (channel_type == "im") to avoid double-handling mentions
            if message.get("channel_type") != "im":
                return
            if message.get("subtype"):
                return  # ignore edits, deletions, bot messages
            user = message.get("user", "")
            if user not in self._allowed_users:
                await say("Sorry, you are not authorized to use this bot.")
                return
            if not self._check_rate_limit(user):
                await say("You are sending too many requests. Please wait a moment.")
                return
            text = message.get("text", "")
            channel = message.get("channel", "")
            if text:
                await self._handle_incoming(text, channel, user_id=user)


def _strip_mention(text: str) -> str:
    """Remove leading <@USERID> mention from Slack message text."""
    import re
    return re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()
