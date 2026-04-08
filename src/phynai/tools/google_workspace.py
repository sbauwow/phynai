"""Google Workspace tools — Gmail, Calendar, Drive.

Uses Google APIs via httpx (no extra SDK dependency).
Requires a Google Cloud service account or OAuth2 credentials:

    GOOGLE_CLIENT_ID          OAuth2 client ID
    GOOGLE_CLIENT_SECRET      OAuth2 client secret
    GOOGLE_REFRESH_TOKEN      OAuth2 refresh token (offline access)

Alternatively, a pre-obtained access token:
    GOOGLE_ACCESS_TOKEN       Pre-obtained OAuth2 access token

OAuth scopes needed:
    https://www.googleapis.com/auth/gmail.modify
    https://www.googleapis.com/auth/gmail.send
    https://www.googleapis.com/auth/calendar
    https://www.googleapis.com/auth/drive.readonly
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from email.mime.text import MIMEText
from typing import Any

import httpx

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool
from phynai.tools._validators import validate_api_id, safe_path_segment

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_TOKEN_CACHE: dict[str, Any] = {}
_TOKEN_LOCK: asyncio.Lock | None = None


def _get_token_lock() -> asyncio.Lock:
    global _TOKEN_LOCK
    if _TOKEN_LOCK is None:
        _TOKEN_LOCK = asyncio.Lock()
    return _TOKEN_LOCK


# ── Auth ──────────────────────────────────────────────────────────────────────

async def _get_token() -> str:
    """Return a valid Google OAuth2 access token."""
    direct = os.environ.get("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Google Workspace credentials not configured. Set GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN in your .env."
        )

    # Serialize token refresh to prevent TOCTOU race conditions
    async with _get_token_lock():
        cached = _TOKEN_CACHE.get("token")
        if cached and _TOKEN_CACHE.get("expires_at", 0) > time.time() + 60:
            return cached  # type: ignore[return-value]

        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=data)
        resp.raise_for_status()
        result = resp.json()
        _TOKEN_CACHE["token"] = result["access_token"]
        _TOKEN_CACHE["expires_at"] = time.time() + result.get("expires_in", 3600)
        return result["access_token"]


async def _google(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(method, url, headers=headers, json=body, params=params)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


# ── Gmail: Email ──────────────────────────────────────────────────────────────

@tool(
    name="google_mail_send",
    description="Send an email via Gmail.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["email", "google"],
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body (plain text)"},
        },
        "required": ["to", "subject", "body"],
    },
    tags=["google", "gmail", "email"],
)
async def google_mail_send(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    msg = MIMEText(arguments["body"])
    msg["to"] = arguments["to"]
    msg["subject"] = arguments["subject"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    await _google("POST", f"{_GMAIL_BASE}/users/me/messages/send", body={"raw": raw})
    return ToolResult(
        tool_name="google_mail_send",
        success=True,
        output=f"Email sent to {arguments['to']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="google_mail_read",
    description="Read recent emails from Gmail.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["email", "google"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query (default: newer_than:7d)"},
            "limit": {"type": "integer", "description": "Max messages to return (default 10)"},
            "unread_only": {"type": "boolean", "description": "Only return unread messages"},
        },
    },
    tags=["google", "gmail", "email"],
)
async def google_mail_read(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    query = arguments.get("query", "newer_than:7d")
    if arguments.get("unread_only"):
        query = f"is:unread {query}"
    limit = arguments.get("limit", 10)

    # List message IDs
    data = await _google(
        "GET", f"{_GMAIL_BASE}/users/me/messages",
        params={"q": query, "maxResults": str(limit)},
    )
    message_ids = [m["id"] for m in data.get("messages", [])]

    lines = []
    for mid in message_ids:
        msg = await _google("GET", f"{_GMAIL_BASE}/users/me/messages/{mid}", params={"format": "metadata"})
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "?")
        subject = headers.get("Subject", "(no subject)")
        date = headers.get("Date", "")[:16]
        snippet = msg.get("snippet", "")[:80]
        unread = "[UNREAD] " if "UNREAD" in msg.get("labelIds", []) else ""
        lines.append(f"{unread}{date}  From: {sender}  Subject: {subject}  |  {snippet}")

    return ToolResult(
        tool_name="google_mail_read",
        success=True,
        output="\n".join(lines) if lines else "No messages found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Google Calendar ───────────────────────────────────────────────────────────

@tool(
    name="google_calendar_list",
    description="List upcoming events from Google Calendar.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["calendar", "google"],
    parameters={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "Days ahead to look (default 7)"},
            "limit": {"type": "integer", "description": "Max events (default 10)"},
            "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
        },
    },
    tags=["google", "calendar"],
)
async def google_calendar_list(arguments: dict[str, Any]) -> ToolResult:
    from datetime import datetime, timedelta, timezone
    t0 = time.monotonic()
    days = arguments.get("days", 7)
    limit = arguments.get("limit", 10)
    cal_id = validate_api_id(arguments.get("calendar_id", "primary"), "calendar_id")
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    data = await _google(
        "GET", f"{_CALENDAR_BASE}/calendars/{cal_id}/events",
        params={
            "timeMin": now.isoformat(),
            "timeMax": end.isoformat(),
            "maxResults": str(limit),
            "singleEvents": "true",
            "orderBy": "startTime",
        },
    )
    events = data.get("items", [])
    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))[:16].replace("T", " ")
        loc = e.get("location", "")
        loc_str = f"  @ {loc}" if loc else ""
        lines.append(f"{start}  {e.get('summary', '(no title)')}{loc_str}")

    return ToolResult(
        tool_name="google_calendar_list",
        success=True,
        output="\n".join(lines) if lines else "No upcoming events.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="google_calendar_create",
    description="Create an event in Google Calendar.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["calendar", "google"],
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "Start datetime ISO 8601 e.g. 2026-04-10T14:00:00"},
            "end": {"type": "string", "description": "End datetime ISO 8601"},
            "timezone": {"type": "string", "description": "IANA timezone (default UTC)"},
            "location": {"type": "string", "description": "Location string"},
            "description": {"type": "string", "description": "Event description"},
            "attendees": {"type": "string", "description": "Comma-separated attendee emails"},
            "calendar_id": {"type": "string", "description": "Calendar ID (default 'primary')"},
        },
        "required": ["summary", "start", "end"],
    },
    tags=["google", "calendar"],
)
async def google_calendar_create(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    cal_id = validate_api_id(arguments.get("calendar_id", "primary"), "calendar_id")
    tz = arguments.get("timezone", "UTC")
    event: dict[str, Any] = {
        "summary": arguments["summary"],
        "start": {"dateTime": arguments["start"], "timeZone": tz},
        "end": {"dateTime": arguments["end"], "timeZone": tz},
    }
    if arguments.get("location"):
        event["location"] = arguments["location"]
    if arguments.get("description"):
        event["description"] = arguments["description"]
    if arguments.get("attendees"):
        event["attendees"] = [
            {"email": a.strip()} for a in arguments["attendees"].split(",")
        ]
    result = await _google("POST", f"{_CALENDAR_BASE}/calendars/{cal_id}/events", body=event)
    return ToolResult(
        tool_name="google_calendar_create",
        success=True,
        output=f"Event created: {result.get('htmlLink', arguments['summary'])}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Google Drive ──────────────────────────────────────────────────────────────

@tool(
    name="google_drive_list",
    description="List files in Google Drive.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["drive", "google"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Drive search query (e.g. \"name contains 'report'\")"},
            "folder_id": {"type": "string", "description": "Folder ID to list (default: root)"},
            "limit": {"type": "integer", "description": "Max files (default 20)"},
        },
    },
    tags=["google", "drive"],
)
async def google_drive_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    limit = arguments.get("limit", 20)
    params: dict[str, str] = {
        "pageSize": str(limit),
        "fields": "files(id,name,mimeType,size,modifiedTime)",
        "orderBy": "modifiedTime desc",
    }
    q_parts = []
    if arguments.get("query"):
        q_parts.append(arguments["query"])
    if arguments.get("folder_id"):
        q_parts.append(f"'{arguments['folder_id']}' in parents")
    if q_parts:
        params["q"] = " and ".join(q_parts)

    data = await _google("GET", f"{_DRIVE_BASE}/files", params=params)
    files = data.get("files", [])
    lines = []
    for f in files:
        is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
        kind = "DIR " if is_folder else "FILE"
        size = f"{int(f.get('size', 0)):,}B" if f.get("size") else ""
        modified = f.get("modifiedTime", "")[:10]
        lines.append(f"{kind}  {f.get('name', ''):40s}  {modified}  {size}")

    return ToolResult(
        tool_name="google_drive_list",
        success=True,
        output="\n".join(lines) if lines else "No files found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="google_drive_read",
    description="Read the text content of a file from Google Drive.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["drive", "google"],
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Google Drive file ID"},
        },
        "required": ["file_id"],
    },
    tags=["google", "drive"],
)
async def google_drive_read(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    file_id = arguments["file_id"]

    # Get file metadata to check type
    meta = await _google("GET", f"{_DRIVE_BASE}/files/{file_id}", params={"fields": "mimeType,name"})
    mime = meta.get("mimeType", "")

    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Google Docs/Sheets/Slides: export as text
    if mime.startswith("application/vnd.google-apps."):
        export_mime = "text/plain"
        if "spreadsheet" in mime:
            export_mime = "text/csv"
        url = f"{_DRIVE_BASE}/files/{file_id}/export"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=headers, params={"mimeType": export_mime})
    else:
        url = f"{_DRIVE_BASE}/files/{file_id}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=headers, params={"alt": "media"})

    resp.raise_for_status()
    content = resp.text[:8000]
    return ToolResult(
        tool_name="google_drive_read",
        success=True,
        output=content,
        duration_ms=(time.monotonic() - t0) * 1000,
    )
