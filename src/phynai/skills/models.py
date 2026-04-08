"""Skill data models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class SkillMeta(BaseModel):
    """Metadata stored in skill.json alongside the skill module."""

    name: str                          # Unique skill identifier (also the tool name)
    description: str                   # What this skill does
    version: str = "1.0.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    use_count: int = 0                 # Incremented on each invocation
    source: str = "generated"         # "generated" | "builtin" | "imported"
    tags: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
    enabled: bool = True


class SkillUsageEvent(BaseModel):
    """Recorded each time a skill is invoked."""

    skill_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    duration_ms: float = 0.0
