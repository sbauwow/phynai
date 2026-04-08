"""PhynAI Skills — dynamically generated and loaded tool extensions.

Skills are user-specific tool modules stored in ~/.phynai/skills/.
The agent builds new skills from usage patterns over time.

Usage:
    from phynai.skills import SkillRegistry
    registry = SkillRegistry()
    registry.load_all(tool_runtime)
"""

from phynai.skills.registry import SkillRegistry
from phynai.skills.loader import load_all_skills

__all__ = ["SkillRegistry", "load_all_skills"]
