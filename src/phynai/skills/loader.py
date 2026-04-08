"""Convenience wrapper to load all skills into a runtime at startup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phynai.runtime.tool_runtime import PhynaiToolRuntime


def load_all_skills(runtime: "PhynaiToolRuntime", skills_dir=None) -> int:
    """Load all user skills into runtime. Called during agent boot."""
    from phynai.skills.registry import SkillRegistry
    registry = SkillRegistry(skills_dir=skills_dir)
    return registry.load_into(runtime)
