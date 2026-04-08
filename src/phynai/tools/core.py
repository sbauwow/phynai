"""Core registration — register all built-in tools with a runtime.

Supports admin-deployed policy files (``~/.phynai/policy.yaml``).  When
present, only tools in the ``allow`` list are loaded.  The ``deny`` list
takes precedence over ``allow``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phynai.tools.decorator import register_all

if TYPE_CHECKING:
    from phynai.runtime.tool_runtime import PhynaiToolRuntime

logger = logging.getLogger(__name__)

# Map from tool-group name (used in policy.yaml) to the module import info.
# Format: (module_path, env_guard) — env_guard is a callable returning bool.
_TOOL_MODULES: dict[str, tuple[str, Any]] = {
    "terminal":   ("phynai.tools.terminal",          lambda: True),
    "file_tools": ("phynai.tools.file_tools",        lambda: True),
    "web_tools":  ("phynai.tools.web_tools",         lambda: True),
    "ms365":      ("phynai.tools.ms365",             lambda: bool(
        os.environ.get("MICROSOFT_TENANT_ID") or os.environ.get("MICROSOFT_ACCESS_TOKEN")
    )),
    "google":     ("phynai.tools.google_workspace",  lambda: bool(
        os.environ.get("GOOGLE_REFRESH_TOKEN") or os.environ.get("GOOGLE_ACCESS_TOKEN")
    )),
    "github":     ("phynai.tools.github_tools",      lambda: bool(os.environ.get("GITHUB_TOKEN"))),
    "jira":       ("phynai.tools.jira_tools",        lambda: bool(
        os.environ.get("JIRA_URL") and (os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_PAT"))
    )),
    "okta":       ("phynai.tools.okta_tools",        lambda: bool(
        os.environ.get("OKTA_ORG_URL") and os.environ.get("OKTA_API_TOKEN")
    )),
}


def _load_policy() -> dict[str, Any] | None:
    """Load policy.yaml from ~/.phynai/ if it exists."""
    policy_path = Path.home() / ".phynai" / "policy.yaml"
    if not policy_path.is_file():
        return None
    try:
        import yaml
        with open(policy_path) as f:
            policy = yaml.safe_load(f) or {}
        logger.info("Loaded deployment policy from %s", policy_path)
        return policy
    except ImportError:
        # FAIL CLOSED: policy exists but can't be parsed → refuse to start
        logger.error(
            "policy.yaml found at %s but PyYAML is not installed. "
            "Refusing to start without policy enforcement. "
            "Install PyYAML: uv pip install pyyaml", policy_path,
        )
        raise SystemExit(
            "FATAL: policy.yaml exists but PyYAML is missing — "
            "cannot enforce policy. Install pyyaml or remove policy.yaml."
        )
    except Exception as exc:
        # FAIL CLOSED: corrupted or unreadable policy → refuse to start
        logger.error("Failed to load policy.yaml at %s: %s", policy_path, exc)
        raise SystemExit(
            f"FATAL: policy.yaml exists but cannot be loaded: {exc} — "
            "cannot enforce policy. Fix the file or remove it."
        )


def _should_load_tool(
    tool_group: str,
    policy: dict[str, Any] | None,
    env_guard: Any,
) -> bool:
    """Decide whether a tool group should be loaded.

    Rules:
      1. If policy exists, deny list takes precedence over allow.
      2. If policy has an allow list, only listed tools are loaded.
      3. If no policy, fall back to credential-based guards.
    """
    if policy is not None:
        tools_policy = policy.get("tools", {})
        deny_list = tools_policy.get("deny", [])
        allow_list = tools_policy.get("allow", [])

        if tool_group in deny_list:
            return False
        if allow_list and tool_group not in allow_list:
            return False
        # Policy allows it — still check env credentials
        return env_guard()

    # No policy — use credential-based guards only
    return env_guard()


def register_core_tools(runtime: PhynaiToolRuntime) -> None:
    """Import and register all built-in tool modules, then load user skills.

    When ``~/.phynai/policy.yaml`` is present (admin-managed deployment),
    only tools in the policy's ``allow`` list are loaded.  The ``deny``
    list always takes precedence.
    """
    import importlib

    policy = _load_policy()

    if policy:
        managed = policy.get("credential_source") == "bundle"
        if managed:
            # Also load .env from ~/.phynai/ for bundle-deployed creds
            env_path = Path.home() / ".phynai" / ".env"
            if env_path.is_file():
                _load_env_file(env_path)

    for group_name, (module_path, env_guard) in _TOOL_MODULES.items():
        if _should_load_tool(group_name, policy, env_guard):
            try:
                mod = importlib.import_module(module_path)
                register_all(runtime, mod)
                logger.debug("Loaded tool group: %s", group_name)
            except Exception as exc:
                logger.warning("Failed to load tool group %s: %s", group_name, exc)
        else:
            logger.debug("Skipped tool group: %s (policy or credentials)", group_name)

    # Load user skills from ~/.phynai/skills/
    from phynai.skills.loader import load_all_skills
    load_all_skills(runtime)


def _load_env_file(path: Path) -> None:
    """Minimal .env loader — sets vars that aren't already set in os.environ."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
