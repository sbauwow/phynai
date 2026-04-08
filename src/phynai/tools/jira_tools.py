"""Jira tools — issues, search, transitions, comments.

Uses the Jira REST API v3 via httpx (no extra SDK dependency).
Requires:

    JIRA_URL                  Jira instance URL (e.g. https://yourcompany.atlassian.net)
    JIRA_EMAIL                Account email for basic auth
    JIRA_API_TOKEN            API token (create at id.atlassian.com/manage-profile/security/api-tokens)

Alternatively, for Jira Data Center / Server with PAT:
    JIRA_URL                  Instance URL
    JIRA_PAT                  Personal access token (Bearer auth)
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool
from phynai.tools._validators import validate_jira_key, validate_jira_project


# ── Auth / HTTP ──────────────────────────────────────────────────────────────

def _base_url() -> str:
    url = os.environ.get("JIRA_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("JIRA_URL not configured. Set it in your .env.")
    return url


def _headers() -> dict[str, str]:
    pat = os.environ.get("JIRA_PAT", "")
    if pat:
        return {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not (email and token):
        raise RuntimeError(
            "Jira credentials not configured. Set JIRA_EMAIL and JIRA_API_TOKEN "
            "(or JIRA_PAT for Data Center) in your .env."
        )
    basic = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
    }


async def _jira(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    url = f"{_base_url()}/rest/api/3{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method, url, headers=_headers(), json=body, params=params,
        )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


def _render_adf(body: Any) -> str:
    """Extract plain text from Atlassian Document Format (ADF)."""
    if not body or not isinstance(body, dict):
        return ""
    parts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            for child in node.get("content", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)
    return "".join(parts)


# ── Search ───────────────────────────────────────────────────────────────────

@tool(
    name="jira_search",
    description="Search Jira issues using JQL (Jira Query Language).",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "jql": {"type": "string", "description": "JQL query (e.g. 'project = OPS AND status = Open')"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
        "required": ["jql"],
    },
    tags=["jira", "search"],
)
async def jira_search(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    limit = arguments.get("limit", 20)
    data = await _jira("POST", "/search", body={
        "jql": arguments["jql"],
        "maxResults": limit,
        "fields": ["summary", "status", "assignee", "priority", "issuetype", "updated"],
    })
    issues = data.get("issues", [])
    lines = []
    for issue in issues:
        key = issue["key"]
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")[:55]
        status = fields.get("status", {}).get("name", "?")
        assignee = fields.get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        priority = fields.get("priority", {}).get("name", "?")
        lines.append(f"{key:12}  {status:15}  {priority:8}  {assignee_name:20}  {summary}")
    return ToolResult(
        tool_name="jira_search",
        success=True,
        output="\n".join(lines) if lines else "No issues found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Issue CRUD ───────────────────────────────────────────────────────────────

@tool(
    name="jira_issue_get",
    description="Get details of a specific Jira issue.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Issue key (e.g. OPS-123)"},
        },
        "required": ["key"],
    },
    tags=["jira", "issues"],
)
async def jira_issue_get(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    key = validate_jira_key(arguments["key"])
    issue = await _jira("GET", f"/issue/{key}")
    fields = issue.get("fields", {})
    status = fields.get("status", {}).get("name", "?")
    priority = fields.get("priority", {}).get("name", "?")
    issue_type = fields.get("issuetype", {}).get("name", "?")
    assignee = fields.get("assignee", {})
    assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
    reporter = fields.get("reporter", {})
    reporter_name = reporter.get("displayName", "?") if reporter else "?"
    labels = ", ".join(fields.get("labels", []))
    description = _render_adf(fields.get("description"))[:500]

    lines = [
        f"{key}  {fields.get('summary', '')}",
        f"Type: {issue_type}  Status: {status}  Priority: {priority}",
        f"Assignee: {assignee_name}  Reporter: {reporter_name}",
    ]
    if labels:
        lines.append(f"Labels: {labels}")
    if description:
        lines.append(f"\n{description}")

    return ToolResult(
        tool_name="jira_issue_get",
        success=True,
        output="\n".join(lines),
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="jira_issue_create",
    description="Create a new Jira issue.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project key (e.g. OPS)"},
            "summary": {"type": "string", "description": "Issue title"},
            "description": {"type": "string", "description": "Issue description (plain text)"},
            "issue_type": {"type": "string", "description": "Issue type: Task, Bug, Story, Epic (default Task)"},
            "priority": {"type": "string", "description": "Priority: Highest, High, Medium, Low, Lowest"},
            "assignee": {"type": "string", "description": "Assignee account ID"},
            "labels": {"type": "string", "description": "Comma-separated labels"},
        },
        "required": ["project", "summary"],
    },
    tags=["jira", "issues"],
)
async def jira_issue_create(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    fields: dict[str, Any] = {
        "project": {"key": arguments["project"]},
        "summary": arguments["summary"],
        "issuetype": {"name": arguments.get("issue_type", "Task")},
    }
    if arguments.get("description"):
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": arguments["description"]}],
            }],
        }
    if arguments.get("priority"):
        fields["priority"] = {"name": arguments["priority"]}
    if arguments.get("assignee"):
        fields["assignee"] = {"accountId": arguments["assignee"]}
    if arguments.get("labels"):
        fields["labels"] = [l.strip() for l in arguments["labels"].split(",")]

    result = await _jira("POST", "/issue", body={"fields": fields})
    key = result.get("key", "?")
    url = f"{_base_url()}/browse/{key}"
    return ToolResult(
        tool_name="jira_issue_create",
        success=True,
        output=f"Issue created: {key} — {url}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="jira_issue_update",
    description="Update fields on an existing Jira issue.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Issue key (e.g. OPS-123)"},
            "summary": {"type": "string", "description": "New summary/title"},
            "description": {"type": "string", "description": "New description (plain text)"},
            "priority": {"type": "string", "description": "New priority"},
            "assignee": {"type": "string", "description": "New assignee account ID"},
            "labels": {"type": "string", "description": "Comma-separated labels (replaces existing)"},
        },
        "required": ["key"],
    },
    tags=["jira", "issues"],
)
async def jira_issue_update(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    key = validate_jira_key(arguments["key"])
    fields: dict[str, Any] = {}
    if arguments.get("summary"):
        fields["summary"] = arguments["summary"]
    if arguments.get("description"):
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": arguments["description"]}],
            }],
        }
    if arguments.get("priority"):
        fields["priority"] = {"name": arguments["priority"]}
    if arguments.get("assignee"):
        fields["assignee"] = {"accountId": arguments["assignee"]}
    if arguments.get("labels"):
        fields["labels"] = [l.strip() for l in arguments["labels"].split(",")]

    if not fields:
        return ToolResult(tool_name="jira_issue_update", success=False, output="No fields to update.")

    await _jira("PUT", f"/issue/{key}", body={"fields": fields})
    return ToolResult(
        tool_name="jira_issue_update",
        success=True,
        output=f"Updated {key}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Transitions ──────────────────────────────────────────────────────────────

@tool(
    name="jira_issue_transition",
    description="Transition a Jira issue to a new status (e.g. To Do -> In Progress -> Done).",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Issue key (e.g. OPS-123)"},
            "status": {"type": "string", "description": "Target status name (e.g. 'In Progress', 'Done')"},
        },
        "required": ["key", "status"],
    },
    tags=["jira", "transitions"],
)
async def jira_issue_transition(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    key = validate_jira_key(arguments["key"])
    target = arguments["status"].lower()

    # Get available transitions
    data = await _jira("GET", f"/issue/{key}/transitions")
    transitions = data.get("transitions", [])

    match = None
    for t in transitions:
        if t.get("name", "").lower() == target or t.get("to", {}).get("name", "").lower() == target:
            match = t
            break

    if not match:
        available = ", ".join(t.get("name", "?") for t in transitions)
        return ToolResult(
            tool_name="jira_issue_transition",
            success=False,
            output=f"No transition to '{arguments['status']}'. Available: {available}",
        )

    await _jira("POST", f"/issue/{key}/transitions", body={"transition": {"id": match["id"]}})
    return ToolResult(
        tool_name="jira_issue_transition",
        success=True,
        output=f"Transitioned {key} to {match.get('to', {}).get('name', match['name'])}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Comments ─────────────────────────────────────────────────────────────────

@tool(
    name="jira_comment_add",
    description="Add a comment to a Jira issue.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Issue key (e.g. OPS-123)"},
            "body": {"type": "string", "description": "Comment text (plain text)"},
        },
        "required": ["key", "body"],
    },
    tags=["jira", "comments"],
)
async def jira_comment_add(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    key = validate_jira_key(arguments["key"])
    adf_body = {
        "type": "doc",
        "version": 1,
        "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": arguments["body"]}],
        }],
    }
    result = await _jira("POST", f"/issue/{key}/comment", body={"body": adf_body})
    return ToolResult(
        tool_name="jira_comment_add",
        success=True,
        output=f"Comment added to {key} (id: {result.get('id', '?')})",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="jira_comments_list",
    description="List comments on a Jira issue.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["jira", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Issue key (e.g. OPS-123)"},
            "limit": {"type": "integer", "description": "Max comments (default 10)"},
        },
        "required": ["key"],
    },
    tags=["jira", "comments"],
)
async def jira_comments_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    key = validate_jira_key(arguments["key"])
    limit = arguments.get("limit", 10)
    data = await _jira("GET", f"/issue/{key}/comment", params={
        "maxResults": str(limit),
        "orderBy": "-created",
    })
    comments = data.get("comments", [])
    lines = []
    for c in comments:
        author = c.get("author", {}).get("displayName", "?")
        created = c.get("created", "")[:16].replace("T", " ")
        body_text = _render_adf(c.get("body"))[:120]
        lines.append(f"{created}  {author:20}  {body_text}")
    return ToolResult(
        tool_name="jira_comments_list",
        success=True,
        output="\n".join(lines) if lines else "No comments.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )
