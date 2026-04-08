"""PhynaiCLI — interactive terminal REPL.

Implements the ``CLIInterface`` protocol from ``phynai.contracts.interfaces``.
Reads user input, creates WorkItems, runs them through the agent (or
scheduler), and prints formatted results with minimal scroll distance.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from phynai.contracts.work import WorkItem, WorkResult, WorkStatus

if TYPE_CHECKING:
    from phynai.contracts.agent import AgentCore
    from phynai.contracts.orchestrator import Scheduler

logger = logging.getLogger(__name__)

_VERSION = "0.1.0"

# ── ANSI helpers ──────────────────────────────────────────────────────────

_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_WHITE = "\033[97m"
_GRAY = "\033[90m"
_CLEAR_LINE = "\033[2K\r"
_UP_LINE = "\033[1A"


def _cols() -> int:
    """Terminal width, fallback 80."""
    return shutil.get_terminal_size((80, 24)).columns


def _rule(char: str = "─", color: str = _GRAY) -> str:
    """Horizontal rule spanning terminal width."""
    return f"{color}{char * _cols()}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


# ── Banner ────────────────────────────────────────────────────────────────

_BANNER = f"""{_CYAN}{_BOLD}
 ╭─────────────────────────────────╮
 │  ▄▀▀▄ █  █ █  █ █▀▀▄ ▄▀▀▄ █   │
 │  █▀▀  █▀▀█ ▀▄▄█ █  █ █▀▀█ █   │
 │  ▀    ▀  ▀    ▀ ▀  ▀ ▀  ▀ ▀   │
 ╰─────────────────────────────────╯{_RESET}
{_DIM}  agent v{_VERSION} · /help for commands{_RESET}
"""


# ── Spinner ───────────────────────────────────────────────────────────────

class _Spinner:
    """Animated inline spinner that shows tool activity."""

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._message: str = "thinking"
        self._tool_name: str = ""

    async def _animate(self) -> None:
        frames = itertools.cycle(self._FRAMES)
        start = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - start
                frame = next(frames)
                label = self._tool_name or self._message
                line = f"{_CLEAR_LINE}{_CYAN}{frame}{_RESET} {_DIM}{label}{_RESET} {_GRAY}{elapsed:.0f}s{_RESET}"
                sys.stderr.write(line)
                sys.stderr.flush()
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            sys.stderr.write(_CLEAR_LINE)
            sys.stderr.flush()

    def start(self, message: str = "thinking") -> None:
        self._message = message
        self._tool_name = ""
        self._task = asyncio.get_event_loop().create_task(self._animate())

    def set_tool(self, name: str) -> None:
        self._tool_name = f"→ {name}"

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None


# ── CLI ───────────────────────────────────────────────────────────────────

class PhynaiCLI:
    """Interactive terminal REPL for PhynAI.

    Parameters
    ----------
    agent:
        Agent core conforming to :class:`AgentCore`.
    scheduler:
        Optional scheduler.  When provided, work is submitted through
        it rather than calling ``agent.run()`` directly.
    """

    def __init__(
        self,
        agent: AgentCore,
        scheduler: Scheduler | None = None,
    ) -> None:
        self._agent = agent
        self._scheduler = scheduler
        self._session_id: str = str(uuid.uuid4())
        self._history: list[tuple[str, str]] = []  # (prompt, response)
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._running: bool = False
        self._spinner = _Spinner()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Print banner and enter the REPL loop."""
        self._running = True
        self._print_banner()
        self._print_model_info()
        await self.repl()

    async def stop(self) -> None:
        """Signal the REPL to exit."""
        self._running = False
        self._spinner.stop()

    # ── REPL ──────────────────────────────────────────────────────────

    async def repl(self) -> None:
        """Run the read-eval-print loop until the user quits."""
        while self._running:
            try:
                prompt_str = f"{_BOLD}{_GREEN}phynai{_RESET}{_DIM}›{_RESET} "
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(prompt_str)
                )
            except (EOFError, KeyboardInterrupt):
                print(f"\n{_dim('Goodbye.')}")
                self._running = False
                break

            text = user_input.strip()
            if not text:
                continue

            # ── Slash commands ────────────────────────────────────────
            if text.startswith("/"):
                handled = self._handle_command(text)
                if handled:
                    continue

            # ── Normal prompt ─────────────────────────────────────────
            work = WorkItem(
                prompt=text,
                session_id=self._session_id,
                source="cli",
            )

            # Hook spinner into tool dispatch for live feedback
            if hasattr(self._agent, '_on_tool_start'):
                self._agent._on_tool_start = lambda name: self._spinner.set_tool(name)

            self._spinner.start("thinking")
            try:
                result = await self._execute(work)
                self._spinner.stop()
                self._print_result(result)
                self._history.append((text, result.response))
                # Track running totals
                if result.cost:
                    self._total_cost_usd += result.cost.estimated_cost_usd
                    self._total_input_tokens += result.cost.input_tokens
                    self._total_output_tokens += result.cost.output_tokens
            except Exception as exc:
                self._spinner.stop()
                logger.exception("Error running work item")
                print(f"{_RED}✗{_RESET} {exc}")

    # ── Command dispatch ──────────────────────────────────────────────

    def _handle_command(self, text: str) -> bool:
        """Handle a slash command.  Returns True if handled."""
        cmd = text.split()[0].lower()
        if cmd in ("/quit", "/exit"):
            print(_dim("Goodbye."))
            self._running = False
            return True
        if cmd == "/help":
            self._show_help()
            return True
        if cmd == "/history":
            self._show_history()
            return True
        if cmd == "/cost":
            self._show_cost()
            return True
        if cmd == "/session":
            print(f"{_dim('session')} {_GRAY}{self._session_id}{_RESET}")
            return True
        if cmd == "/clear":
            print("\033[2J\033[H", end="")  # clear screen
            return True
        # Unknown command — treat as a prompt
        return False

    # ── Execution ─────────────────────────────────────────────────────

    async def _execute(self, work: WorkItem) -> WorkResult:
        """Submit work to scheduler or run directly on agent."""
        if self._scheduler is not None:
            return await self._agent.run(work)
        return await self._agent.run(work)

    # ── Output formatting ─────────────────────────────────────────────

    def _print_result(self, result: WorkResult) -> None:
        """Print result compactly right below the prompt."""
        if result.status == WorkStatus.failed:
            print(f"{_RED}✗{_RESET} {result.error or 'Unknown error'}")
            return

        # Main response — directly below input, no extra newline
        response = result.response.rstrip()
        if response:
            print(response)

        # Artifacts — compact
        if result.artifacts:
            for art in result.artifacts:
                desc = art.description or art.type
                loc = art.path or art.url or ""
                print(f"  {_DIM}↳{_RESET} {desc}  {_GRAY}{loc}{_RESET}")

        # Cost — single dim line, only if nonzero
        if result.cost and (result.cost.input_tokens + result.cost.output_tokens) > 0:
            c = result.cost
            parts = [f"{c.input_tokens:,}in/{c.output_tokens:,}out"]
            if c.cache_read_tokens:
                parts.append(f"{c.cache_read_tokens:,}cached")
            if c.estimated_cost_usd > 0:
                parts.append(f"${c.estimated_cost_usd:.4f}")
            print(f"{_GRAY}  {' · '.join(parts)}{_RESET}")

    # ── Slash command handlers ────────────────────────────────────────

    def _print_banner(self) -> None:
        """Print the PhynAI banner."""
        print(_BANNER)

    def _print_model_info(self) -> None:
        """Print the active model and reasoning level below the banner."""
        model = getattr(self._agent._client, "model", "unknown")
        reasoning = getattr(self._agent._client, "_reasoning", None)
        parts = [f"{_DIM}  model{_RESET} {model}"]
        if reasoning and reasoning != "none":
            parts.append(f"{_DIM}reasoning{_RESET} {reasoning}")
        print(" · ".join(parts))
        print()

    def _show_history(self) -> None:
        """Display conversation history compactly."""
        if not self._history:
            print(_dim("  No history yet."))
            return
        for i, (prompt, response) in enumerate(self._history, 1):
            short = response[:72].replace("\n", " ")
            print(f"  {_GRAY}{i}.{_RESET} {_BOLD}{prompt}{_RESET}")
            print(f"     {_DIM}{short}{'…' if len(response) > 72 else ''}{_RESET}")

    def _show_cost(self) -> None:
        """Show accumulated cost summary."""
        turns = len(self._history)
        cost = f"${self._total_cost_usd:.4f}" if self._total_cost_usd > 0 else "—"
        total = self._total_input_tokens + self._total_output_tokens
        model = getattr(self._agent._client, "model", "?")
        reasoning = getattr(self._agent._client, "_reasoning", None)
        print(f"  {_DIM}model{_RESET}     {model}" + (f"  {_DIM}reasoning={reasoning}{_RESET}" if reasoning else ""))
        print(f"  {_DIM}turns{_RESET}     {turns}")
        print(f"  {_DIM}tokens{_RESET}    {total:,}  ({self._total_input_tokens:,}in / {self._total_output_tokens:,}out)")
        print(f"  {_DIM}cost{_RESET}      {cost}")
        print(f"  {_DIM}session{_RESET}   {_GRAY}{self._session_id}{_RESET}")

    @staticmethod
    def _show_help() -> None:
        """Print available REPL commands."""
        cmds = [
            ("/help", "Show this help"),
            ("/history", "Conversation history"),
            ("/cost", "Token & cost summary"),
            ("/session", "Current session ID"),
            ("/clear", "Clear screen"),
            ("/quit", "Exit"),
        ]
        for cmd, desc in cmds:
            print(f"  {_BOLD}{cmd:<12}{_RESET}{_DIM}{desc}{_RESET}")

    # ── Legacy format method (kept for tests) ─────────────────────────

    @staticmethod
    def _format_result(result: WorkResult) -> str:
        """Format a WorkResult as plain text (used by tests)."""
        lines: list[str] = []
        if result.status == WorkStatus.failed:
            lines.append(f"[failed] {result.error or 'Unknown error'}")
        else:
            lines.append(result.response)
        if result.artifacts:
            for art in result.artifacts:
                desc = art.description or art.type
                loc = art.path or art.url or ""
                lines.append(f"  ↳ {desc}  {loc}")
        if result.cost and (result.cost.input_tokens + result.cost.output_tokens) > 0:
            c = result.cost
            parts = [f"{c.input_tokens:,}in/{c.output_tokens:,}out"]
            if c.cache_read_tokens:
                parts.append(f"{c.cache_read_tokens:,}cached")
            if c.estimated_cost_usd > 0:
                parts.append(f"${c.estimated_cost_usd:.4f}")
            lines.append(f"  {' · '.join(parts)}")
        return "\n".join(lines)
