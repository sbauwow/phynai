"""Microsoft 365 tools — Outlook, Teams, Calendar, OneDrive, SharePoint.

Uses the Microsoft Graph API via httpx (no extra SDK dependency).
Requires a service-principal / client-credentials app registration in Azure AD:

    MICROSOFT_TENANT_ID       Azure AD tenant ID
    MICROSOFT_CLIENT_ID       App (client) ID
    MICROSOFT_CLIENT_SECRET   Client secret

Alternatively, delegated auth via a user access token:
    MICROSOFT_ACCESS_TOKEN    Pre-obtained OAuth2 access token

Scopes needed (application permissions):
    Mail.ReadWrite, Mail.Send
    Calendars.ReadWrite
    Files.ReadWrite.All
    ChannelMessage.Send, Chat.ReadWrite
    Sites.Read.All
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool
from phynai.tools._validators import validate_ms365_user, safe_path_segment

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_CACHE: dict[str, Any] = {}  # simple in-process cache
_TOKEN_LOCK: asyncio.Lock | None = None  # lazy init to avoid event loop issues


def _get_token_lock() -> asyncio.Lock:
    """Get or create the token refresh lock (lazy to avoid event loop issues)."""
    global _TOKEN_LOCK
    if _TOKEN_LOCK is None:
        _TOKEN_LOCK = asyncio.Lock()
    return _TOKEN_LOCK


# ── Auth ──────────────────────────────────────────────────────────────────────

async def _get_token() -> str:
    """Return a valid Graph API access token (client credentials or env override)."""
    # Direct token override
    direct = os.environ.get("MICROSOFT_ACCESS_TOKEN", "")
    if direct:
        return direct

    tenant = os.environ.get("MICROSOFT_TENANT_ID", "")
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "")
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "")

    if not (tenant and client_id and client_secret):
        raise RuntimeError(
            "Microsoft 365 credentials not configured. Set MICROSOFT_TENANT_ID, "
            "MICROSOFT_CLIENT_ID, and MICROSOFT_CLIENT_SECRET in your .env."
        )

    # Serialize token refresh to prevent TOCTOU race conditions
    async with _get_token_lock():
        # Check cache (inside lock to prevent duplicate refreshes)
        cached = _TOKEN_CACHE.get("token")
        if cached and _TOKEN_CACHE.get("expires_at", 0) > time.time() + 60:
            return cached  # type: ignore[return-value]

        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data=data)
        resp.raise_for_status()
        result = resp.json()
        _TOKEN_CACHE["token"] = result["access_token"]
        _TOKEN_CACHE["expires_at"] = time.time() + result.get("expires_in", 3600)
        return result["access_token"]


async def _graph(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{_GRAPH_BASE}{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method, url, headers=headers,
            json=body, params=params,
        )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


# ── Outlook: Email ─────────────────────────────────────────────────────────────

@tool(
    name="ms_mail_send",
    description="Send an email via Microsoft Outlook / Exchange.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["email", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body (plain text or HTML)"},
            "html": {"type": "boolean", "description": "True if body is HTML (default false)"},
            "user": {"type": "string", "description": "Sender UPN or 'me' (default 'me')"},
        },
        "required": ["to", "subject", "body"],
    },
    tags=["ms365", "outlook", "email"],
)
async def ms_mail_send(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    recipients = [
        {"emailAddress": {"address": a.strip()}}
        for a in arguments["to"].split(",")
    ]
    content_type = "HTML" if arguments.get("html") else "Text"
    payload = {
        "message": {
            "subject": arguments["subject"],
            "body": {"contentType": content_type, "content": arguments["body"]},
            "toRecipients": recipients,
        },
        "saveToSentItems": True,
    }
    await _graph("POST", f"/users/{user}/sendMail", body=payload)
    return ToolResult(
        tool_name="ms_mail_send",
        success=True,
        output=f"Email sent to {arguments['to']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="ms_mail_read",
    description="Read recent emails from a Microsoft Outlook mailbox.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["email", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "user": {"type": "string", "description": "UPN or 'me' (default 'me')"},
            "folder": {"type": "string", "description": "Folder name: inbox, sentitems, drafts (default inbox)"},
            "limit": {"type": "integer", "description": "Max messages to return (default 10)"},
            "unread_only": {"type": "boolean", "description": "Only return unread messages"},
        },
    },
    tags=["ms365", "outlook", "email"],
)
async def ms_mail_read(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    folder = arguments.get("folder", "inbox")
    limit = arguments.get("limit", 10)
    params: dict[str, str] = {
        "$top": str(limit),
        "$select": "subject,from,receivedDateTime,isRead,bodyPreview",
        "$orderby": "receivedDateTime desc",
    }
    if arguments.get("unread_only"):
        params["$filter"] = "isRead eq false"
    data = await _graph("GET", f"/users/{user}/mailFolders/{folder}/messages", params=params)
    messages = data.get("value", [])
    lines = []
    for m in messages:
        sender = m.get("from", {}).get("emailAddress", {}).get("address", "?")
        read_flag = "" if m.get("isRead") else "[UNREAD] "
        lines.append(
            f"{read_flag}{m.get('receivedDateTime', '')[:10]}  "
            f"From: {sender}  Subject: {m.get('subject', '')}  |  "
            f"{m.get('bodyPreview', '')[:80]}"
        )
    output = "\n".join(lines) if lines else "No messages found."
    return ToolResult(
        tool_name="ms_mail_read",
        success=True,
        output=output,
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Calendar ──────────────────────────────────────────────────────────────────

@tool(
    name="ms_calendar_list",
    description="List upcoming calendar events from Microsoft 365 Calendar.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["calendar", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "user": {"type": "string", "description": "UPN or 'me'"},
            "days": {"type": "integer", "description": "Days ahead to look (default 7)"},
            "limit": {"type": "integer", "description": "Max events (default 10)"},
        },
    },
    tags=["ms365", "calendar"],
)
async def ms_calendar_list(arguments: dict[str, Any]) -> ToolResult:
    import time
    from datetime import datetime, timedelta, timezone
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    days = arguments.get("days", 7)
    limit = arguments.get("limit", 10)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end.isoformat(),
        "$top": str(limit),
        "$select": "subject,start,end,location,organizer",
        "$orderby": "start/dateTime",
    }
    data = await _graph("GET", f"/users/{user}/calendarView", params=params)
    events = data.get("value", [])
    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
        loc = e.get("location", {}).get("displayName", "")
        loc_str = f"  @ {loc}" if loc else ""
        lines.append(f"{start}  {e.get('subject', '')}{loc_str}")
    output = "\n".join(lines) if lines else "No upcoming events."
    return ToolResult(
        tool_name="ms_calendar_list",
        success=True,
        output=output,
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="ms_calendar_create",
    description="Create a calendar event in Microsoft 365 Calendar.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["calendar", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "Start datetime ISO 8601 e.g. 2026-04-10T14:00:00"},
            "end": {"type": "string", "description": "End datetime ISO 8601"},
            "timezone": {"type": "string", "description": "IANA timezone (default UTC)"},
            "location": {"type": "string", "description": "Location string"},
            "body": {"type": "string", "description": "Event description"},
            "attendees": {"type": "string", "description": "Comma-separated attendee emails"},
            "user": {"type": "string", "description": "UPN or 'me'"},
        },
        "required": ["subject", "start", "end"],
    },
    tags=["ms365", "calendar"],
)
async def ms_calendar_create(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    tz = arguments.get("timezone", "UTC")
    event: dict[str, Any] = {
        "subject": arguments["subject"],
        "start": {"dateTime": arguments["start"], "timeZone": tz},
        "end": {"dateTime": arguments["end"], "timeZone": tz},
    }
    if arguments.get("location"):
        event["location"] = {"displayName": arguments["location"]}
    if arguments.get("body"):
        event["body"] = {"contentType": "Text", "content": arguments["body"]}
    if arguments.get("attendees"):
        event["attendees"] = [
            {"emailAddress": {"address": a.strip()}, "type": "required"}
            for a in arguments["attendees"].split(",")
        ]
    result = await _graph("POST", f"/users/{user}/events", body=event)
    return ToolResult(
        tool_name="ms_calendar_create",
        success=True,
        output=f"Event created: {result.get('webLink', arguments['subject'])}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Teams ─────────────────────────────────────────────────────────────────────

@tool(
    name="ms_teams_send",
    description="Send a message to a Microsoft Teams channel or chat.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["teams", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Teams group/team ID"},
            "channel_id": {"type": "string", "description": "Channel ID within the team"},
            "message": {"type": "string", "description": "Message text (markdown supported)"},
        },
        "required": ["team_id", "channel_id", "message"],
    },
    tags=["ms365", "teams"],
)
async def ms_teams_send(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    payload = {
        "body": {"contentType": "text", "content": arguments["message"]}
    }
    path = f"/teams/{arguments['team_id']}/channels/{arguments['channel_id']}/messages"
    result = await _graph("POST", path, body=payload)
    return ToolResult(
        tool_name="ms_teams_send",
        success=True,
        output=f"Message sent: {result.get('id', 'ok')}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="ms_teams_list_channels",
    description="List channels in a Microsoft Teams team.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["teams", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "team_id": {"type": "string", "description": "Teams group/team ID"},
        },
        "required": ["team_id"],
    },
    tags=["ms365", "teams"],
)
async def ms_teams_list_channels(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    data = await _graph("GET", f"/teams/{arguments['team_id']}/channels")
    channels = data.get("value", [])
    lines = [f"{c.get('id')}  {c.get('displayName')}" for c in channels]
    return ToolResult(
        tool_name="ms_teams_list_channels",
        success=True,
        output="\n".join(lines) if lines else "No channels found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── OneDrive ──────────────────────────────────────────────────────────────────

@tool(
    name="ms_onedrive_list",
    description="List files and folders in a OneDrive directory.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["onedrive", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder path (default: root)"},
            "user": {"type": "string", "description": "UPN or 'me'"},
        },
    },
    tags=["ms365", "onedrive"],
)
async def ms_onedrive_list(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    path = arguments.get("path", "")
    if path:
        endpoint = f"/users/{user}/drive/root:/{path}:/children"
    else:
        endpoint = f"/users/{user}/drive/root/children"
    data = await _graph("GET", endpoint, params={"$select": "name,size,lastModifiedDateTime,folder,file"})
    items = data.get("value", [])
    lines = []
    for item in items:
        kind = "DIR " if "folder" in item else "FILE"
        size = f"{item.get('size', 0):,}B" if "file" in item else ""
        lines.append(f"{kind}  {item.get('name'):40s}  {item.get('lastModifiedDateTime', '')[:10]}  {size}")
    return ToolResult(
        tool_name="ms_onedrive_list",
        success=True,
        output="\n".join(lines) if lines else "Empty folder.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="ms_onedrive_read",
    description="Read the text content of a file from OneDrive.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["onedrive", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path in OneDrive e.g. Documents/report.txt"},
            "user": {"type": "string", "description": "UPN or 'me'"},
        },
        "required": ["path"],
    },
    tags=["ms365", "onedrive"],
)
async def ms_onedrive_read(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    user = validate_ms365_user(arguments.get("user", "me"))
    path = arguments["path"]
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_GRAPH_BASE}/users/{user}/drive/root:/{path}:/content"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    content = resp.text[:8000]
    return ToolResult(
        tool_name="ms_onedrive_read",
        success=True,
        output=content,
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── SharePoint ────────────────────────────────────────────────────────────────

@tool(
    name="ms_sharepoint_search",
    description="Search for content across Microsoft SharePoint sites.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["sharepoint", "ms365"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query string"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["query"],
    },
    tags=["ms365", "sharepoint"],
)
async def ms_sharepoint_search(arguments: dict[str, Any]) -> ToolResult:
    import time
    t0 = time.monotonic()
    limit = arguments.get("limit", 10)
    payload = {
        "requests": [{
            "entityTypes": ["driveItem", "listItem", "site"],
            "query": {"queryString": arguments["query"]},
            "from": 0,
            "size": limit,
        }]
    }
    data = await _graph("POST", "/search/query", body=payload)
    hits = (
        data.get("value", [{}])[0]
        .get("hitsContainers", [{}])[0]
        .get("hits", [])
    )
    lines = []
    for h in hits:
        resource = h.get("resource", {})
        name = resource.get("name") or resource.get("displayName", "?")
        url = resource.get("webUrl", "")
        lines.append(f"{name}  {url}")
    return ToolResult(
        tool_name="ms_sharepoint_search",
        success=True,
        output="\n".join(lines) if lines else "No results found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )
