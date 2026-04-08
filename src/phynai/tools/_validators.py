"""Input validators for tool parameters that flow into API paths.

Prevents path traversal and SSRF via LLM-controlled arguments that
get interpolated into HTTP URL paths.
"""

from __future__ import annotations

import re
from urllib.parse import quote as url_quote

# ── Validators ────────────────────────────────────────────────────────────

# GitHub: owner/repo format
_GH_REPO_RE = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")
# Jira: PROJECT-123 format
_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
# Okta/generic API IDs: alphanumeric with some punctuation
_API_ID_RE = re.compile(r"^[a-zA-Z0-9._@+=-]+$")
# Email: basic format check (used for MS365 user param)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def validate_github_repo(repo: str) -> str:
    """Validate GitHub owner/repo format."""
    if not _GH_REPO_RE.match(repo):
        raise ValueError(
            f"Invalid GitHub repo format: {repo!r} — expected 'owner/repo'"
        )
    return repo


def validate_jira_key(key: str) -> str:
    """Validate Jira issue key format (PROJECT-123)."""
    if not _JIRA_KEY_RE.match(key):
        raise ValueError(
            f"Invalid Jira issue key: {key!r} — expected format like 'PROJ-123'"
        )
    return key


def validate_jira_project(project: str) -> str:
    """Validate Jira project key (uppercase alpha + digits)."""
    if not re.match(r"^[A-Z][A-Z0-9_]+$", project):
        raise ValueError(
            f"Invalid Jira project key: {project!r} — expected uppercase like 'PROJ'"
        )
    return project


def validate_api_id(value: str, label: str = "ID") -> str:
    """Validate a generic API identifier (Okta user_id, group_id, etc)."""
    if not value or not _API_ID_RE.match(value):
        raise ValueError(
            f"Invalid {label}: {value!r} — must be alphanumeric"
        )
    return value


def validate_ms365_user(user: str) -> str:
    """Validate MS365 user — 'me', email, or UUID."""
    if user == "me":
        return user
    if _EMAIL_RE.match(user):
        return user
    if _API_ID_RE.match(user):
        return user
    raise ValueError(
        f"Invalid MS365 user: {user!r} — expected 'me', email, or user ID"
    )


def safe_path_segment(value: str, label: str = "path segment") -> str:
    """Sanitize a value for use in a URL path segment.

    Rejects path traversal attempts and URL-encodes the value.
    """
    if not value:
        raise ValueError(f"Empty {label}")
    if "/" in value or ".." in value or "\\" in value:
        raise ValueError(
            f"Invalid {label}: {value!r} — contains path traversal characters"
        )
    return url_quote(value, safe="")
