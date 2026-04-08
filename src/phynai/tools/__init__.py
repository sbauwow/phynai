"""phynai.tools — tool decorator, built-in tools, and core registration."""

from phynai.tools.core import register_core_tools
from phynai.tools.decorator import discover_tools, register_all, tool

__all__ = ["tool", "discover_tools", "register_all", "register_core_tools"]
