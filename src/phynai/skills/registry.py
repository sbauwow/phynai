"""SkillRegistry — manages the on-disk skill library at ~/.phynai/skills/."""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phynai.skills.models import SkillMeta

logger = logging.getLogger("phynai.skills.registry")

_DEFAULT_SKILLS_DIR = Path.home() / ".phynai" / "skills"


class SkillRegistry:
    """Manages skill discovery, loading, metadata, and persistence.

    Skills live at ~/.phynai/skills/<skill_name>/
        skill.py      — the tool implementation (@tool-decorated function)
        skill.json    — SkillMeta serialized to JSON
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._dir = Path(skills_dir or os.environ.get("PHYNAI_SKILLS_DIR", _DEFAULT_SKILLS_DIR))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, SkillMeta] = {}

    # ── Discovery ─────────────────────────────────────────────────────────────

    def scan(self) -> list[SkillMeta]:
        """Scan skills dir and return metadata for all enabled skills."""
        found = []
        for skill_dir in sorted(self._dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            meta_path = skill_dir / "skill.json"
            module_path = skill_dir / "skill.py"
            if not meta_path.exists() or not module_path.exists():
                continue
            try:
                meta = SkillMeta.model_validate(json.loads(meta_path.read_text()))
                if meta.enabled:
                    self._meta[meta.name] = meta
                    found.append(meta)
            except Exception as exc:
                logger.warning("Skipping malformed skill at %s: %s", skill_dir, exc)
        return found

    def get_meta(self, name: str) -> SkillMeta | None:
        return self._meta.get(name)

    def list_skills(self) -> list[SkillMeta]:
        return list(self._meta.values())

    # ── Loading into tool runtime ─────────────────────────────────────────────

    def load_into(self, runtime: Any) -> int:
        """Load all enabled skills into a PhynaiToolRuntime. Returns count loaded."""
        from phynai.tools.decorator import discover_tools

        loaded = 0
        for meta in self.scan():
            module_path = self._dir / meta.name / "skill.py"
            try:
                module = _load_module_from_path(meta.name, module_path)
                for handler, tool_meta in discover_tools(module):
                    runtime.register(tool_meta.name, handler, tool_meta)
                    loaded += 1
                    logger.debug("Loaded skill: %s", tool_meta.name)
            except Exception as exc:
                logger.warning("Failed to load skill '%s': %s", meta.name, exc)

        if loaded:
            logger.info("Loaded %d skill(s) from %s", loaded, self._dir)
        return loaded

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_meta(self, meta: SkillMeta) -> None:
        skill_dir = self._dir / meta.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.json").write_text(
            meta.model_dump_json(indent=2), encoding="utf-8"
        )
        self._meta[meta.name] = meta

    def save_skill(self, name: str, code: str, meta: SkillMeta) -> Path:
        """Write skill.py + skill.json to disk. Returns the skill dir.

        Security:
          - Path traversal protection: resolved dir must be inside skills dir.
          - AST lint: rejects code with unsafe patterns (eval, exec, subprocess, etc).
        """
        # Path traversal protection
        skill_dir = (self._dir / name).resolve()
        if not str(skill_dir).startswith(str(self._dir.resolve())):
            raise ValueError(
                f"Invalid skill name {name!r}: path traversal detected"
            )

        # AST lint as a gate — reject unsafe code, don't just warn
        warnings = _lint_skill_source(code, name)
        if warnings:
            raise ValueError(
                f"Skill '{name}' rejected by safety lint:\n"
                + "\n".join(f"  - {w}" for w in warnings)
            )

        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.py").write_text(code, encoding="utf-8")
        self.save_meta(meta)
        logger.info("Saved skill '%s' to %s", name, skill_dir)
        return skill_dir

    def delete_skill(self, name: str) -> bool:
        """Disable a skill by setting enabled=False (non-destructive)."""
        meta = self._meta.get(name)
        if not meta:
            return False
        meta.enabled = False
        meta.updated_at = datetime.now(timezone.utc)
        self.save_meta(meta)
        logger.info("Disabled skill '%s'", name)
        return True

    def increment_use_count(self, name: str) -> None:
        meta = self._meta.get(name)
        if meta:
            meta.use_count += 1
            meta.updated_at = datetime.now(timezone.utc)
            self.save_meta(meta)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def top_skills(self, n: int = 10) -> list[SkillMeta]:
        return sorted(self._meta.values(), key=lambda m: m.use_count, reverse=True)[:n]


# ── Safety lint for skills ────────────────────────────────────────────────

# AST node names and function calls that indicate potentially unsafe code
_UNSAFE_CALLS = frozenset({
    "os.system", "os.popen", "os.exec", "os.execl", "os.execle",
    "os.execlp", "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.spawn", "os.spawnl", "os.spawnle",
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_call", "subprocess.check_output",
    "shutil.rmtree",
})
_UNSAFE_NAMES = frozenset({"__import__", "eval", "exec", "compile"})


def _lint_skill_source(source: str, name: str) -> list[str]:
    """AST-lint a skill source file for potentially unsafe patterns.

    Returns a list of warning strings. Does NOT block loading — just warns.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [f"Skill '{name}' has a syntax error"]

    for node in ast.walk(tree):
        # Check for calls to unsafe functions
        if isinstance(node, ast.Call):
            func = node.func
            # Direct name: eval(...), exec(...)
            if isinstance(func, ast.Name) and func.id in _UNSAFE_NAMES:
                warnings.append(
                    f"Skill '{name}' uses {func.id}() at line {node.lineno}"
                )
            # Attribute: os.system(...), subprocess.run(...)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                full = f"{func.value.id}.{func.attr}"
                if full in _UNSAFE_CALLS:
                    warnings.append(
                        f"Skill '{name}' uses {full}() at line {node.lineno}"
                    )
        # Check for __import__ as a name reference
        elif isinstance(node, ast.Name) and node.id == "__import__":
            warnings.append(
                f"Skill '{name}' references __import__ at line {node.lineno}"
            )
    return warnings


def _load_module_from_path(name: str, path: Path) -> Any:
    """Dynamically import a Python file as a module.

    Runs an AST safety lint before loading. Unsafe patterns BLOCK loading.
    """
    source = path.read_text(encoding="utf-8")
    warnings = _lint_skill_source(source, name)
    if warnings:
        for w in warnings:
            logger.error("SKILL SAFETY: %s", w)
        raise ImportError(
            f"Skill '{name}' blocked by safety lint: {'; '.join(warnings)}"
        )

    module_name = f"phynai.skills._loaded.{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module
