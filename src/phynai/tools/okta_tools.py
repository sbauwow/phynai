"""Okta tools — users, groups, apps, MFA, logs.

Uses the Okta Management API via httpx (no extra SDK dependency).
Requires:

    OKTA_ORG_URL              Okta org URL (e.g. https://yourcompany.okta.com)
    OKTA_API_TOKEN            API token (Security > API > Tokens in Okta admin)

Token requires Super Admin or appropriate admin role for the operations needed.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool
from phynai.tools._validators import validate_api_id


# ── Auth / HTTP ──────────────────────────────────────────────────────────────

def _base_url() -> str:
    url = os.environ.get("OKTA_ORG_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("OKTA_ORG_URL not configured. Set it in your .env.")
    return url


def _headers() -> dict[str, str]:
    token = os.environ.get("OKTA_API_TOKEN", "")
    if not token:
        raise RuntimeError("OKTA_API_TOKEN not configured. Set it in your .env.")
    return {
        "Authorization": f"SSWS {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _okta(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    url = f"{_base_url()}/api/v1{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method, url, headers=_headers(), json=body, params=params,
        )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


# ── Users ────────────────────────────────────────────────────────────────────

@tool(
    name="okta_user_search",
    description="Search Okta users by name, email, or login.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term (matches name, email, login)"},
            "filter": {"type": "string", "description": "Okta filter expression (e.g. 'status eq \"ACTIVE\"')"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
    },
    tags=["okta", "users", "identity"],
)
async def okta_user_search(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    params: dict[str, str] = {"limit": str(arguments.get("limit", 20))}
    if arguments.get("query"):
        params["q"] = arguments["query"]
    if arguments.get("filter"):
        params["filter"] = arguments["filter"]
    data = await _okta("GET", "/users", params=params)
    lines = []
    for u in data:
        profile = u.get("profile", {})
        status = u.get("status", "?")
        email = profile.get("email", "")
        name = f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip()
        login = profile.get("login", "")
        lines.append(f"{status:12}  {name:25}  {email:35}  {login}")
    return ToolResult(
        tool_name="okta_user_search",
        success=True,
        output="\n".join(lines) if lines else "No users found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_user_get",
    description="Get detailed information about an Okta user.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "User ID or login (email)"},
        },
        "required": ["user_id"],
    },
    tags=["okta", "users", "identity"],
)
async def okta_user_get(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    user = await _okta("GET", f"/users/{arguments['user_id']}")
    profile = user.get("profile", {})
    status = user.get("status", "?")
    created = user.get("created", "")[:10]
    last_login = user.get("lastLogin", "never")
    if last_login != "never":
        last_login = last_login[:16].replace("T", " ")

    lines = [
        f"{profile.get('firstName', '')} {profile.get('lastName', '')}",
        f"Login: {profile.get('login', '?')}",
        f"Email: {profile.get('email', '?')}",
        f"Status: {status}",
        f"Created: {created}",
        f"Last login: {last_login}",
        f"ID: {user.get('id', '?')}",
    ]
    if profile.get("department"):
        lines.append(f"Department: {profile['department']}")
    if profile.get("title"):
        lines.append(f"Title: {profile['title']}")
    if profile.get("manager"):
        lines.append(f"Manager: {profile['manager']}")
    if profile.get("mobilePhone"):
        lines.append(f"Mobile: {profile['mobilePhone']}")

    return ToolResult(
        tool_name="okta_user_get",
        success=True,
        output="\n".join(lines),
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_user_lifecycle",
    description="Perform a lifecycle action on an Okta user: activate, deactivate, suspend, unsuspend, unlock, reset_password.",
    risk=Risk.HIGH,
    mutates=True,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "User ID or login (email)"},
            "action": {"type": "string", "description": "Action: activate, deactivate, suspend, unsuspend, unlock, reset_password"},
        },
        "required": ["user_id", "action"],
    },
    tags=["okta", "users", "identity"],
)
async def okta_user_lifecycle(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    user_id = validate_api_id(arguments["user_id"], "user_id")
    action = arguments["action"]
    valid_actions = {"activate", "deactivate", "suspend", "unsuspend", "unlock", "reset_password"}
    if action not in valid_actions:
        return ToolResult(
            tool_name="okta_user_lifecycle",
            success=False,
            output=f"Invalid action '{action}'. Valid: {', '.join(sorted(valid_actions))}",
        )

    # Map to Okta API paths
    path_map = {
        "activate": "activate",
        "deactivate": "deactivate",
        "suspend": "suspend",
        "unsuspend": "unsuspend",
        "unlock": "unlock",
        "reset_password": "reset_password",
    }
    params: dict[str, str] = {}
    if action == "activate":
        params["sendEmail"] = "true"
    if action == "reset_password":
        params["sendEmail"] = "true"

    await _okta("POST", f"/users/{user_id}/lifecycle/{path_map[action]}", params=params)
    return ToolResult(
        tool_name="okta_user_lifecycle",
        success=True,
        output=f"User {user_id}: {action} completed",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Groups ───────────────────────────────────────────────────────────────────

@tool(
    name="okta_groups_list",
    description="List Okta groups, optionally filtered by name.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search by group name"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
    },
    tags=["okta", "groups", "identity"],
)
async def okta_groups_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    params: dict[str, str] = {"limit": str(arguments.get("limit", 20))}
    if arguments.get("query"):
        params["q"] = arguments["query"]
    data = await _okta("GET", "/groups", params=params)
    lines = []
    for g in data:
        profile = g.get("profile", {})
        name = profile.get("name", "?")
        desc = profile.get("description", "")[:50]
        gtype = g.get("type", "?")
        lines.append(f"{gtype:15}  {name:30}  {desc}")
    return ToolResult(
        tool_name="okta_groups_list",
        success=True,
        output="\n".join(lines) if lines else "No groups found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_group_members",
    description="List members of an Okta group.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "Group ID"},
            "limit": {"type": "integer", "description": "Max results (default 50)"},
        },
        "required": ["group_id"],
    },
    tags=["okta", "groups", "identity"],
)
async def okta_group_members(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    group_id = validate_api_id(arguments["group_id"], "group_id")
    params: dict[str, str] = {"limit": str(arguments.get("limit", 50))}
    data = await _okta("GET", f"/groups/{group_id}/users", params=params)
    lines = []
    for u in data:
        profile = u.get("profile", {})
        name = f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip()
        email = profile.get("email", "")
        status = u.get("status", "?")
        lines.append(f"{status:12}  {name:25}  {email}")
    return ToolResult(
        tool_name="okta_group_members",
        success=True,
        output="\n".join(lines) if lines else "No members found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_group_add_user",
    description="Add a user to an Okta group.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "Group ID"},
            "user_id": {"type": "string", "description": "User ID"},
        },
        "required": ["group_id", "user_id"],
    },
    tags=["okta", "groups", "identity"],
)
async def okta_group_add_user(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    await _okta("PUT", f"/groups/{arguments['group_id']}/users/{arguments['user_id']}")
    return ToolResult(
        tool_name="okta_group_add_user",
        success=True,
        output=f"User {arguments['user_id']} added to group {arguments['group_id']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_group_remove_user",
    description="Remove a user from an Okta group.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "Group ID"},
            "user_id": {"type": "string", "description": "User ID"},
        },
        "required": ["group_id", "user_id"],
    },
    tags=["okta", "groups", "identity"],
)
async def okta_group_remove_user(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    await _okta("DELETE", f"/groups/{arguments['group_id']}/users/{arguments['user_id']}")
    return ToolResult(
        tool_name="okta_group_remove_user",
        success=True,
        output=f"User {arguments['user_id']} removed from group {arguments['group_id']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Apps ─────────────────────────────────────────────────────────────────────

@tool(
    name="okta_apps_list",
    description="List applications configured in Okta.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search by app name"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
    },
    tags=["okta", "apps", "identity"],
)
async def okta_apps_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    params: dict[str, str] = {"limit": str(arguments.get("limit", 20))}
    if arguments.get("query"):
        params["q"] = arguments["query"]
    data = await _okta("GET", "/apps", params=params)
    lines = []
    for app in data:
        name = app.get("label", app.get("name", "?"))
        status = app.get("status", "?")
        sign_on = app.get("signOnMode", "?")
        lines.append(f"{status:10}  {name:35}  {sign_on}")
    return ToolResult(
        tool_name="okta_apps_list",
        success=True,
        output="\n".join(lines) if lines else "No applications found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="okta_app_users",
    description="List users assigned to an Okta application.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity"],
    parameters={
        "type": "object",
        "properties": {
            "app_id": {"type": "string", "description": "Application ID"},
            "limit": {"type": "integer", "description": "Max results (default 50)"},
        },
        "required": ["app_id"],
    },
    tags=["okta", "apps", "identity"],
)
async def okta_app_users(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    params: dict[str, str] = {"limit": str(arguments.get("limit", 50))}
    data = await _okta("GET", f"/apps/{arguments['app_id']}/users", params=params)
    lines = []
    for u in data:
        profile = u.get("profile", {})
        email = profile.get("email", u.get("credentials", {}).get("userName", "?"))
        status = u.get("status", "?")
        scope = u.get("scope", "?")
        lines.append(f"{status:12}  {email:35}  scope: {scope}")
    return ToolResult(
        tool_name="okta_app_users",
        success=True,
        output="\n".join(lines) if lines else "No users assigned.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── MFA / Factors ────────────────────────────────────────────────────────────

@tool(
    name="okta_user_factors",
    description="List MFA factors enrolled for an Okta user.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "identity", "mfa"],
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "User ID or login (email)"},
        },
        "required": ["user_id"],
    },
    tags=["okta", "mfa", "identity"],
)
async def okta_user_factors(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    data = await _okta("GET", f"/users/{arguments['user_id']}/factors")
    lines = []
    for f in data:
        factor_type = f.get("factorType", "?")
        provider = f.get("provider", "?")
        status = f.get("status", "?")
        profile = f.get("profile", {})
        detail = ""
        if profile.get("phoneNumber"):
            detail = f"  ({profile['phoneNumber']})"
        elif profile.get("email"):
            detail = f"  ({profile['email']})"
        elif profile.get("credentialId"):
            detail = f"  ({profile['credentialId']})"
        lines.append(f"{status:10}  {factor_type:20}  {provider:15}{detail}")
    return ToolResult(
        tool_name="okta_user_factors",
        success=True,
        output="\n".join(lines) if lines else "No MFA factors enrolled.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── System Log ───────────────────────────────────────────────────────────────

@tool(
    name="okta_logs",
    description="Query the Okta system log for recent events.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["okta", "audit"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keyword (e.g. user email, event type)"},
            "filter": {"type": "string", "description": "Okta log filter (e.g. 'eventType eq \"user.session.start\"')"},
            "since": {"type": "string", "description": "Start time ISO 8601 (default: last 24h)"},
            "limit": {"type": "integer", "description": "Max events (default 20)"},
        },
    },
    tags=["okta", "logs", "audit"],
)
async def okta_logs(arguments: dict[str, Any]) -> ToolResult:
    from datetime import datetime, timedelta, timezone
    t0 = time.monotonic()
    params: dict[str, str] = {"limit": str(arguments.get("limit", 20))}
    if arguments.get("query"):
        params["q"] = arguments["query"]
    if arguments.get("filter"):
        params["filter"] = arguments["filter"]
    since = arguments.get("since")
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    params["since"] = since

    data = await _okta("GET", "/logs", params=params)
    lines = []
    for event in data:
        ts = event.get("published", "")[:19].replace("T", " ")
        event_type = event.get("eventType", "?")
        actor = event.get("actor", {}).get("displayName", event.get("actor", {}).get("alternateId", "?"))
        outcome = event.get("outcome", {}).get("result", "?")
        target_list = event.get("target", [])
        target = target_list[0].get("displayName", "") if target_list else ""
        target_str = f"  -> {target}" if target else ""
        lines.append(f"{ts}  {outcome:8}  {actor:25}  {event_type:40}{target_str}")
    return ToolResult(
        tool_name="okta_logs",
        success=True,
        output="\n".join(lines) if lines else "No events found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )
