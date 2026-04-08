"""PhynAI CLI entry point.

Wires up all layers and dispatches to the requested interface
(``run``, ``chat``, ``serve``, ``gateway``, ``version``).
Intended to be called as ``phynai`` via a console_scripts entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from phynai import __version__

# Load .env from project root (or cwd) if python-dotenv is available
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in [Path(__file__).resolve().parent.parent.parent / ".env", Path.cwd() / ".env"]:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return

_load_dotenv()

logger = logging.getLogger("phynai")


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line with structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "ts": self.formatTime(record, datefmt=None),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Propagate work_id / trace_id if injected via LoggerAdapter extra
        for key in ("work_id", "trace_id", "user_id"):
            val = record.__dict__.get(key)
            if val:
                doc[key] = val
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


# ---------------------------------------------------------------------------
# Component factory
# ---------------------------------------------------------------------------

def _build_agent(
    model_override: str | None = None,
    reasoning: str | None = None,
) -> "PhynaiAgent":  # noqa: F821
    """Create a fully-wired PhynaiAgent from environment variables.

    Parameters
    ----------
    model_override:
        If provided, overrides ``PHYNAI_MODEL`` env var.
    reasoning:
        Extended thinking budget: ``"none"``, ``"low"``, ``"medium"``, ``"high"``.
    """
    from phynai.agent import (
        PhynaiAgent,
        PhynaiClientManager,
        PhynaiContextManager,
        PhynaiCostLedger,
        PhynaiSessionStore,
    )
    from phynai.runtime import PhynaiToolRuntime

    # LLM client
    provider = os.environ.get("PHYNAI_PROVIDER", "anthropic")
    model = model_override or os.environ.get("PHYNAI_MODEL", "claude-opus-4-6")
    api_key = os.environ.get("PHYNAI_API_KEY", "")

    client = PhynaiClientManager(
        provider=provider,
        model=model,
        api_key=api_key,
        reasoning=reasoning,
    )

    # Tool runtime with core tools registered
    tools = PhynaiToolRuntime()

    from phynai.tools import register_core_tools
    register_core_tools(tools)

    # Supporting components — system prompt with tool awareness
    from phynai.prompts import build_system_prompt
    tool_names = [t.name for t in tools.list_tools()]
    system_prompt = build_system_prompt(tool_names, workdir=os.getcwd())
    context = PhynaiContextManager(system_prompt=system_prompt)
    session = PhynaiSessionStore()
    ledger = PhynaiCostLedger()

    return PhynaiAgent(
        client=client,
        tools=tools,
        context=context,
        session=session,
        ledger=ledger,
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

async def _cmd_run(args: argparse.Namespace) -> None:
    """Execute a one-shot prompt."""
    from phynai.contracts.work import WorkItem

    agent = _build_agent(
        model_override=getattr(args, "model", None),
        reasoning=getattr(args, "reasoning", None),
    )
    work = WorkItem(prompt=args.prompt, source="cli")
    result = await agent.run(work)

    if result.error:
        print(f"[error] {result.error}", file=sys.stderr)
        sys.exit(1)

    print(result.response)

    if result.cost and (result.cost.input_tokens + result.cost.output_tokens) > 0:
        c = result.cost
        parts = [f"{c.input_tokens:,}in / {c.output_tokens:,}out"]
        if c.cache_read_tokens:
            parts.append(f"{c.cache_read_tokens:,} cached")
        if c.model:
            parts.append(c.model)
        if c.estimated_cost_usd > 0:
            parts.append(f"~${c.estimated_cost_usd:.4f}")
        print(f"\n[usage] {' · '.join(parts)}", file=sys.stderr)


async def _cmd_chat(args: argparse.Namespace) -> None:
    """Start the interactive REPL."""
    from phynai.interfaces import PhynaiCLI

    agent = _build_agent(
        model_override=getattr(args, "model", None),
        reasoning=getattr(args, "reasoning", None),
    )
    cli = PhynaiCLI(agent=agent)
    try:
        await cli.start()
    except KeyboardInterrupt:
        await cli.stop()


async def _cmd_gateway(args: argparse.Namespace) -> None:
    """Start a messaging gateway."""
    agent = _build_agent(
        model_override=getattr(args, "model", None),
        reasoning=getattr(args, "reasoning", None),
    )

    if args.platform == "slack":
        from phynai.interfaces import SlackGateway
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token or not app_token:
            print("Error: SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set.", file=sys.stderr)
            sys.exit(1)
        allowed_raw = os.environ.get("SLACK_ALLOWED_USERS", "").strip()
        if not allowed_raw:
            print(
                "Error: SLACK_ALLOWED_USERS must be set to a comma-separated list of "
                "Slack user IDs (e.g. U012AB3CD,U098ZY7WX). "
                "The gateway refuses to start with an empty allowlist.",
                file=sys.stderr,
            )
            sys.exit(1)
        allowed = {uid.strip() for uid in allowed_raw.split(",") if uid.strip()}
        gw = SlackGateway(agent=agent, bot_token=bot_token, app_token=app_token, allowed_users=allowed)

    else:
        print(f"Unknown platform: {args.platform}", file=sys.stderr)
        sys.exit(1)

    try:
        await gw.start()
    except KeyboardInterrupt:
        await gw.stop()


def _cmd_setup(args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    from phynai.setup import run_setup_wizard
    section = getattr(args, "section", None)
    run_setup_wizard(section=section)


def _cmd_auth(args: argparse.Namespace) -> None:
    """Manage provider authentication."""
    from phynai.auth import login_provider, logout_provider, list_auth_status

    action = getattr(args, "auth_action", None)
    provider = getattr(args, "provider", None)

    if action == "login":
        if not provider:
            # Interactive provider selection
            from phynai.auth import PROVIDER_REGISTRY, OAUTH_CAPABLE_PROVIDERS
            providers = sorted(PROVIDER_REGISTRY.keys())
            print("Available providers:")
            for i, pid in enumerate(providers, 1):
                pconfig = PROVIDER_REGISTRY[pid]
                oauth_marker = " (OAuth)" if pid in OAUTH_CAPABLE_PROVIDERS else ""
                print(f"  {i}. {pconfig.name}{oauth_marker}")
            try:
                choice = input(f"\nProvider [1-{len(providers)}]: ").strip()
                idx = int(choice) - 1
                provider = providers[idx]
            except (ValueError, IndexError, KeyboardInterrupt, EOFError):
                return
        login_provider(provider)
    elif action == "logout":
        logout_provider(provider)
    elif action == "status":
        list_auth_status()
    else:
        list_auth_status()


def _cmd_skills(args: argparse.Namespace) -> None:
    """Manage skills: list, create, delete, show."""
    from phynai.skills.registry import SkillRegistry
    registry = SkillRegistry()

    action = getattr(args, "skills_action", "list")

    if action == "list" or action is None:
        skills = registry.scan()
        if not skills:
            print("No skills installed. Use `phynai skills create` to build one.")
            return
        print(f"{'NAME':<25} {'USE COUNT':<12} {'SOURCE':<12} DESCRIPTION")
        print("-" * 80)
        for s in sorted(skills, key=lambda x: x.use_count, reverse=True):
            print(f"{s.name:<25} {s.use_count:<12} {s.source:<12} {s.description[:40]}")

    elif action == "show":
        registry.scan()
        meta = registry.get_meta(args.name)
        if not meta:
            print(f"Skill '{args.name}' not found.")
            return
        print(meta.model_dump_json(indent=2))
        skill_py = registry._dir / args.name / "skill.py"
        if skill_py.exists():
            print("\n--- skill.py ---")
            print(skill_py.read_text())

    elif action == "create":
        asyncio.run(_create_skill(args))

    elif action == "delete":
        registry.scan()
        if registry.delete_skill(args.name):
            print(f"Skill '{args.name}' disabled.")
        else:
            print(f"Skill '{args.name}' not found.")


async def _create_skill(args: argparse.Namespace) -> None:
    from phynai.skills.registry import SkillRegistry
    from phynai.skills.builder import SkillBuilder

    client = _build_agent()._client
    registry = SkillRegistry()
    builder = SkillBuilder(client=client, registry=registry)

    name = args.name
    description = args.description or input("Description: ").strip()

    print(f"Building skill '{name}'...")
    meta = await builder.build_from_description(
        name=name,
        description=description,
        example_prompt=getattr(args, "example", ""),
    )
    print(f"Skill '{meta.name}' created at ~/.phynai/skills/{meta.name}/")


def _cmd_version(args: argparse.Namespace) -> None:
    """Print version and exit."""
    print(f"phynai {__version__}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="phynai",
        description="PhynAI Agent — operator-grade AI agent runtime.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )
    parser.add_argument(
        "-m", "--model", default=None,
        help="Override the LLM model (e.g. claude-sonnet-4-6, gpt-4o)",
    )
    parser.add_argument(
        "-r", "--reasoning",
        choices=["none", "low", "medium", "high"],
        default=None,
        help="Extended thinking budget (Anthropic models only)",
    )

    subs = parser.add_subparsers(dest="command")

    # run
    run_p = subs.add_parser("run", help="Execute a one-shot prompt")
    run_p.add_argument("prompt", help="The prompt to run")

    # chat
    subs.add_parser("chat", help="Start interactive REPL")

    # gateway
    gw_p = subs.add_parser("gateway", help="Start a messaging gateway")
    gw_p.add_argument(
        "platform",
        choices=["slack"],
        help="Platform to connect to",
    )

    # setup
    setup_p = subs.add_parser(
        "setup",
        help="Interactive setup wizard",
        description="Configure PhynAI with an interactive wizard. "
                    "Run a specific section: phynai setup provider|gateway|tools|agent",
    )
    setup_p.add_argument(
        "section", nargs="?", default=None,
        choices=["provider", "gateway", "ms365", "tools", "agent"],
        help="Run a specific section only",
    )

    # auth
    auth_p = subs.add_parser("auth", help="Manage provider authentication")
    auth_subs = auth_p.add_subparsers(dest="auth_action")
    login_p = auth_subs.add_parser("login", help="Login to a provider")
    login_p.add_argument("provider", nargs="?", help="Provider to login to")
    logout_p = auth_subs.add_parser("logout", help="Logout from a provider")
    logout_p.add_argument("provider", nargs="?", help="Provider to logout from")
    auth_subs.add_parser("status", help="Show auth status for all providers")

    # skills
    skills_p = subs.add_parser("skills", help="Manage skills")
    skills_subs = skills_p.add_subparsers(dest="skills_action")
    skills_subs.add_parser("list", help="List installed skills")
    show_p = skills_subs.add_parser("show", help="Show skill details and source")
    show_p.add_argument("name", help="Skill name")
    create_p = skills_subs.add_parser("create", help="Generate a new skill via LLM")
    create_p.add_argument("name", help="Skill name (snake_case)")
    create_p.add_argument("--description", "-d", help="What the skill does")
    create_p.add_argument("--example", "-e", help="Example prompt", default="")
    delete_p = skills_subs.add_parser("delete", help="Disable a skill")
    delete_p.add_argument("name", help="Skill name")

    # admin
    admin_p = subs.add_parser(
        "admin",
        help="Fleet deployment — provision and deploy to target machines",
        description="Admin tools for managing PhynAI across a fleet of machines. "
                    "Run: phynai admin init|validate|provision|deploy|audit",
    )
    admin_p.add_argument(
        "admin_args", nargs=argparse.REMAINDER,
        help="Subcommand and arguments (e.g. provision --role sre)",
    )

    # version
    subs.add_parser("version", help="Print version")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point — parse args and dispatch."""
    parser = _build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(level)
    logging.root.addHandler(handler)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Sync commands (no asyncio.run needed)
    # Admin — delegates to its own module
    if args.command == "admin":
        from phynai.admin import run_admin
        run_admin(getattr(args, "admin_args", []))
        return

    sync_commands = {
        "version": _cmd_version,
        "setup": _cmd_setup,
        "auth": _cmd_auth,
        "skills": _cmd_skills,
    }
    if args.command in sync_commands:
        sync_commands[args.command](args)
        return

    # Async commands
    async_commands = {
        "run": _cmd_run,
        "chat": _cmd_chat,
        "gateway": _cmd_gateway,
    }

    handler = async_commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        asyncio.run(handler(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
