"""GitHub tools — repos, issues, PRs, CI, releases.

Uses the GitHub REST API via httpx (no extra SDK dependency).
Requires a personal access token (classic or fine-grained):

    GITHUB_TOKEN              Personal access token

Token scopes needed (classic):
    repo, read:org

Fine-grained tokens: grant Repository access for the repos you need,
with permissions: Contents (read), Issues (read/write), Pull requests (read/write),
Actions (read), Metadata (read).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool
from phynai.tools._validators import validate_github_repo, validate_api_id

_API_BASE = "https://api.github.com"


# ── Auth / HTTP ──────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError(
            "GitHub credentials not configured. Set GITHUB_TOKEN in your .env."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    url = f"{_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            method, url, headers=_headers(), json=body, params=params,
        )
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}


# ── Repos ────────────────────────────────────────────────────────────────────

@tool(
    name="github_repo_search",
    description="Search GitHub repositories.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "code"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (e.g. 'language:python stars:>100')"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["query"],
    },
    tags=["github", "repo"],
)
async def github_repo_search(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    limit = arguments.get("limit", 10)
    data = await _gh("GET", "/search/repositories", params={
        "q": arguments["query"],
        "per_page": str(limit),
        "sort": "updated",
    })
    repos = data.get("items", [])
    lines = []
    for r in repos:
        stars = r.get("stargazers_count", 0)
        lines.append(f"{r['full_name']:40s}  {stars:>6} stars  {r.get('description', '')[:60]}")
    return ToolResult(
        tool_name="github_repo_search",
        success=True,
        output="\n".join(lines) if lines else "No repositories found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Issues ───────────────────────────────────────────────────────────────────

@tool(
    name="github_issues_list",
    description="List issues in a GitHub repository.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "state": {"type": "string", "description": "Filter: open, closed, all (default open)"},
            "labels": {"type": "string", "description": "Comma-separated label filter"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
        "required": ["repo"],
    },
    tags=["github", "issues"],
)
async def github_issues_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    params: dict[str, str] = {
        "state": arguments.get("state", "open"),
        "per_page": str(arguments.get("limit", 20)),
        "sort": "updated",
        "direction": "desc",
    }
    if arguments.get("labels"):
        params["labels"] = arguments["labels"]
    data = await _gh("GET", f"/repos/{repo}/issues", params=params)
    lines = []
    for issue in data:
        if issue.get("pull_request"):
            continue  # skip PRs from the issues endpoint
        labels = ", ".join(l["name"] for l in issue.get("labels", []))
        label_str = f"  [{labels}]" if labels else ""
        lines.append(f"#{issue['number']:>5}  {issue['state']:6}  {issue['title'][:60]}{label_str}")
    return ToolResult(
        tool_name="github_issues_list",
        success=True,
        output="\n".join(lines) if lines else "No issues found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="github_issue_create",
    description="Create an issue in a GitHub repository.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["github", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "title": {"type": "string", "description": "Issue title"},
            "body": {"type": "string", "description": "Issue body (markdown)"},
            "labels": {"type": "string", "description": "Comma-separated labels to apply"},
            "assignees": {"type": "string", "description": "Comma-separated GitHub usernames to assign"},
        },
        "required": ["repo", "title"],
    },
    tags=["github", "issues"],
)
async def github_issue_create(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    payload: dict[str, Any] = {
        "title": arguments["title"],
    }
    if arguments.get("body"):
        payload["body"] = arguments["body"]
    if arguments.get("labels"):
        payload["labels"] = [l.strip() for l in arguments["labels"].split(",")]
    if arguments.get("assignees"):
        payload["assignees"] = [a.strip() for a in arguments["assignees"].split(",")]
    result = await _gh("POST", f"/repos/{repo}/issues", body=payload)
    return ToolResult(
        tool_name="github_issue_create",
        success=True,
        output=f"Issue created: {result['html_url']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="github_issue_comment",
    description="Add a comment to a GitHub issue or pull request.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["github", "issues"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "number": {"type": "integer", "description": "Issue or PR number"},
            "body": {"type": "string", "description": "Comment body (markdown)"},
        },
        "required": ["repo", "number", "body"],
    },
    tags=["github", "issues"],
)
async def github_issue_comment(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    number = int(arguments["number"])
    result = await _gh(
        "POST", f"/repos/{repo}/issues/{number}/comments",
        body={"body": arguments["body"]},
    )
    return ToolResult(
        tool_name="github_issue_comment",
        success=True,
        output=f"Comment added: {result['html_url']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Pull Requests ────────────────────────────────────────────────────────────

@tool(
    name="github_pr_list",
    description="List pull requests in a GitHub repository.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "pr"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "state": {"type": "string", "description": "Filter: open, closed, all (default open)"},
            "limit": {"type": "integer", "description": "Max results (default 20)"},
        },
        "required": ["repo"],
    },
    tags=["github", "pr"],
)
async def github_pr_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    params: dict[str, str] = {
        "state": arguments.get("state", "open"),
        "per_page": str(arguments.get("limit", 20)),
        "sort": "updated",
        "direction": "desc",
    }
    data = await _gh("GET", f"/repos/{repo}/pulls", params=params)
    lines = []
    for pr in data:
        draft = " [DRAFT]" if pr.get("draft") else ""
        user = pr.get("user", {}).get("login", "?")
        lines.append(f"#{pr['number']:>5}  {pr['state']:6}  {pr['title'][:50]}{draft}  by {user}")
    return ToolResult(
        tool_name="github_pr_list",
        success=True,
        output="\n".join(lines) if lines else "No pull requests found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="github_pr_get",
    description="Get details of a specific pull request including diff stats and review status.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "pr"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "number": {"type": "integer", "description": "Pull request number"},
        },
        "required": ["repo", "number"],
    },
    tags=["github", "pr"],
)
async def github_pr_get(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    number = int(arguments["number"])
    pr = await _gh("GET", f"/repos/{repo}/pulls/{number}")
    reviews = await _gh("GET", f"/repos/{repo}/pulls/{number}/reviews")

    review_summary = {}
    for r in reviews:
        state = r.get("state", "PENDING")
        user = r.get("user", {}).get("login", "?")
        review_summary[user] = state

    lines = [
        f"#{pr['number']} {pr['title']}",
        f"State: {pr['state']}  Draft: {pr.get('draft', False)}  Mergeable: {pr.get('mergeable', '?')}",
        f"Author: {pr.get('user', {}).get('login', '?')}  Base: {pr.get('base', {}).get('ref', '?')} <- {pr.get('head', {}).get('ref', '?')}",
        f"Changed files: {pr.get('changed_files', '?')}  +{pr.get('additions', 0)} -{pr.get('deletions', 0)}",
        f"Reviews: {', '.join(f'{u}: {s}' for u, s in review_summary.items()) or 'none'}",
    ]
    if pr.get("body"):
        lines.append(f"\n{pr['body'][:500]}")

    return ToolResult(
        tool_name="github_pr_get",
        success=True,
        output="\n".join(lines),
        duration_ms=(time.monotonic() - t0) * 1000,
    )


@tool(
    name="github_pr_create",
    description="Create a pull request in a GitHub repository.",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["github", "pr"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "title": {"type": "string", "description": "PR title"},
            "body": {"type": "string", "description": "PR description (markdown)"},
            "head": {"type": "string", "description": "Branch containing changes"},
            "base": {"type": "string", "description": "Branch to merge into (default: main)"},
            "draft": {"type": "boolean", "description": "Create as draft PR (default false)"},
        },
        "required": ["repo", "title", "head"],
    },
    tags=["github", "pr"],
)
async def github_pr_create(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    payload: dict[str, Any] = {
        "title": arguments["title"],
        "head": arguments["head"],
        "base": arguments.get("base", "main"),
    }
    if arguments.get("body"):
        payload["body"] = arguments["body"]
    if arguments.get("draft"):
        payload["draft"] = True
    result = await _gh("POST", f"/repos/{repo}/pulls", body=payload)
    return ToolResult(
        tool_name="github_pr_create",
        success=True,
        output=f"PR created: {result['html_url']}",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── CI / Actions ─────────────────────────────────────────────────────────────

@tool(
    name="github_actions_status",
    description="Check GitHub Actions workflow run status for a repository.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "ci"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "branch": {"type": "string", "description": "Filter by branch (optional)"},
            "limit": {"type": "integer", "description": "Max runs to show (default 10)"},
        },
        "required": ["repo"],
    },
    tags=["github", "actions", "ci"],
)
async def github_actions_status(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    params: dict[str, str] = {
        "per_page": str(arguments.get("limit", 10)),
    }
    if arguments.get("branch"):
        params["branch"] = arguments["branch"]
    data = await _gh("GET", f"/repos/{repo}/actions/runs", params=params)
    runs = data.get("workflow_runs", [])
    lines = []
    for r in runs:
        status = r.get("conclusion") or r.get("status", "?")
        branch = r.get("head_branch", "?")
        name = r.get("name", "?")[:30]
        created = r.get("created_at", "")[:16].replace("T", " ")
        lines.append(f"{status:12}  {name:30}  {branch:20}  {created}")
    return ToolResult(
        tool_name="github_actions_status",
        success=True,
        output="\n".join(lines) if lines else "No workflow runs found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Releases ─────────────────────────────────────────────────────────────────

@tool(
    name="github_releases_list",
    description="List releases in a GitHub repository.",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["github", "releases"],
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository in owner/repo format"},
            "limit": {"type": "integer", "description": "Max releases (default 10)"},
        },
        "required": ["repo"],
    },
    tags=["github", "releases"],
)
async def github_releases_list(arguments: dict[str, Any]) -> ToolResult:
    t0 = time.monotonic()
    repo = validate_github_repo(arguments["repo"])
    data = await _gh("GET", f"/repos/{repo}/releases", params={
        "per_page": str(arguments.get("limit", 10)),
    })
    lines = []
    for r in data:
        tag = r.get("tag_name", "?")
        pre = " [pre-release]" if r.get("prerelease") else ""
        draft = " [draft]" if r.get("draft") else ""
        date = r.get("published_at", "")[:10]
        lines.append(f"{tag:20}  {date}  {r.get('name', '')[:40]}{pre}{draft}")
    return ToolResult(
        tool_name="github_releases_list",
        success=True,
        output="\n".join(lines) if lines else "No releases found.",
        duration_ms=(time.monotonic() - t0) * 1000,
    )
