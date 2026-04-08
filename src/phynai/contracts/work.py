"""Work contracts — the central data vocabulary of PhynAI.

Every layer speaks in WorkItems and WorkResults. This module defines the
canonical shapes for work requests, results, cost tracking, and artifacts.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkStatus(str, enum.Enum):
    """Lifecycle status of a WorkItem."""

    pending = "pending"
    assigned = "assigned"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    delegated = "delegated"
    cancelled = "cancelled"


class WorkPriority(str, enum.Enum):
    """Priority level for scheduling and queue ordering."""

    critical = "critical"
    high = "high"
    normal = "normal"
    low = "low"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class WorkConstraints(BaseModel):
    """Guardrails that bound how a WorkItem may be executed."""

    max_iterations: int = 50
    max_tokens: int | None = None
    allowed_tools: list[str] | None = None
    denied_tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300


class WorkItem(BaseModel):
    """A unit of work submitted to the agent system.

    This is THE central contract. Everything — CLI commands, API requests,
    Paperclip tasks, cron jobs — gets normalised into a WorkItem before the
    agent touches it.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: WorkConstraints = Field(default_factory=WorkConstraints)
    priority: WorkPriority = WorkPriority.normal
    parent_id: str | None = None  # subagent lineage
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "direct"  # e.g. 'slack', 'cron', 'cli', 'api'
    user_id: str = ""  # identity of the requesting user (e.g. Slack UID, email)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    """A concrete output produced during work execution."""

    type: str  # e.g. 'file', 'pr', 'message'
    path: str | None = None
    url: str | None = None
    description: str = ""


class CostRecord(BaseModel):
    """Token usage and cost tracking for a single LLM interaction or aggregate."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""
    provider: str = ""
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float | None = None
    status: str = "estimated"  # one of: estimated, actual, included, unknown


class WorkResult(BaseModel):
    """The outcome of processing a WorkItem."""

    work_id: str
    status: WorkStatus
    response: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    cost: CostRecord = Field(default_factory=CostRecord)
    events: list[Any] = Field(default_factory=list)
    blocked_on: str | None = None
    error: str | None = None
    completed_at: datetime | None = None
