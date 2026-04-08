"""File tools — read, write, search, and patch files."""

from __future__ import annotations

import asyncio
import difflib
import os
from pathlib import Path
from typing import Any

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool

# Maximum file sizes to prevent OOM
_MAX_READ_BYTES = 10 * 1024 * 1024   # 10 MB
_MAX_WRITE_BYTES = 50 * 1024 * 1024  # 50 MB


def _resolve_path(raw: str) -> Path:
    """Expand and resolve a path. No allowlist enforced here — policy layer owns that."""
    return Path(os.path.expanduser(raw)).resolve()


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

@tool(
    name="read_file",
    description="Read a text file with line numbers",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["filesystem"],
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "offset": {"type": "integer", "description": "Start line (1-indexed)", "default": 1},
            "limit": {"type": "integer", "description": "Max lines to read", "default": 500},
        },
        "required": ["path"],
    },
)
async def read_file_tool(arguments: dict[str, Any]) -> ToolResult:
    raw_path = arguments.get("path", "")
    if not raw_path:
        return ToolResult(tool_name="read_file", success=False, output="", error="No path provided", duration_ms=0.0)

    file_path = _resolve_path(raw_path)
    offset = max(1, int(arguments.get("offset") or 1))
    limit = min(2000, max(1, int(arguments.get("limit") or 500)))

    try:
        size = file_path.stat().st_size
        if size > _MAX_READ_BYTES:
            return ToolResult(
                tool_name="read_file", success=False, output="",
                error=f"File too large to read ({size:,} bytes > {_MAX_READ_BYTES:,} byte limit)",
                duration_ms=0.0,
            )
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = [f"{offset + i}|{line.rstrip()}" for i, line in enumerate(selected)]
        return ToolResult(tool_name="read_file", success=True, output="\n".join(numbered), duration_ms=0.0)
    except FileNotFoundError:
        return ToolResult(tool_name="read_file", success=False, output="", error=f"File not found: {file_path}", duration_ms=0.0)
    except PermissionError:
        return ToolResult(tool_name="read_file", success=False, output="", error=f"Permission denied: {file_path}", duration_ms=0.0)
    except (ValueError, OverflowError) as exc:
        return ToolResult(tool_name="read_file", success=False, output="", error=f"Invalid argument: {exc}", duration_ms=0.0)
    except OSError as exc:
        return ToolResult(tool_name="read_file", success=False, output="", error=str(exc), duration_ms=0.0)


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

@tool(
    name="write_file",
    description="Write content to a file, creating directories if needed",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["filesystem"],
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
)
async def write_file_tool(arguments: dict[str, Any]) -> ToolResult:
    raw_path = arguments.get("path", "")
    if not raw_path:
        return ToolResult(tool_name="write_file", success=False, output="", error="No path provided", duration_ms=0.0)

    file_path = _resolve_path(raw_path)
    content = arguments.get("content", "")

    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return ToolResult(
            tool_name="write_file", success=False, output="",
            error=f"Content too large (> {_MAX_WRITE_BYTES:,} byte limit)", duration_ms=0.0,
        )

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        n = file_path.write_text(content, encoding="utf-8")
        return ToolResult(tool_name="write_file", success=True, output=f"Wrote {n} chars to {file_path}", duration_ms=0.0)
    except PermissionError:
        return ToolResult(tool_name="write_file", success=False, output="", error=f"Permission denied: {file_path}", duration_ms=0.0)
    except OSError as exc:
        return ToolResult(tool_name="write_file", success=False, output="", error=str(exc), duration_ms=0.0)


# ---------------------------------------------------------------------------
# search_files  — FIXED: no shell interpolation, all args passed via argv list
# ---------------------------------------------------------------------------

@tool(
    name="search_files",
    description="Search file contents or find files by name (ripgrep-backed)",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["filesystem", "search"],
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex or glob pattern"},
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "Search mode",
                "default": "content",
            },
            "path": {"type": "string", "description": "Directory to search", "default": "."},
            "file_glob": {"type": "string", "description": "Filter files by glob (e.g. *.py)"},
            "limit": {"type": "integer", "description": "Max results", "default": 50},
        },
        "required": ["pattern"],
    },
)
async def search_files_tool(arguments: dict[str, Any]) -> ToolResult:
    pattern = str(arguments.get("pattern", ""))
    target = arguments.get("target", "content")
    search_path = _resolve_path(arguments.get("path") or ".")
    file_glob = arguments.get("file_glob")
    limit = min(500, max(1, int(arguments.get("limit") or 50)))

    if not pattern:
        return ToolResult(tool_name="search_files", success=False, output="", error="No pattern provided", duration_ms=0.0)

    try:
        if target == "files":
            # Use find with argv list — no shell interpolation
            cmd = ["find", str(search_path), "-name", pattern, "-type", "f"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            # Build ripgrep argv list — every argument passed separately, never interpolated
            rg_cmd = ["rg", "-n", "--max-count", "1000"]
            if file_glob:
                rg_cmd += ["-g", file_glob]
            rg_cmd += ["--", pattern, str(search_path)]
            proc = await asyncio.create_subprocess_exec(
                *rg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(tool_name="search_files", success=False, output="", error="Search timed out after 30s", duration_ms=30000.0)

        lines = stdout.decode("utf-8", errors="replace").splitlines()
        output = "\n".join(lines[:limit])

        if not output.strip():
            return ToolResult(tool_name="search_files", success=True, output="No matches found.", duration_ms=0.0)

        truncated = len(lines) > limit
        if truncated:
            output += f"\n... (showing {limit} of {len(lines)} matches)"

        return ToolResult(tool_name="search_files", success=True, output=output, duration_ms=0.0)

    except FileNotFoundError:
        # rg not installed — fall back to grep with argv list
        if target != "files":
            grep_cmd = ["grep", "-rn"]
            if file_glob:
                grep_cmd += [f"--include={file_glob}"]
            grep_cmd += ["--", pattern, str(search_path)]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *grep_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                lines = stdout.decode("utf-8", errors="replace").splitlines()
                output = "\n".join(lines[:limit])
                return ToolResult(tool_name="search_files", success=True, output=output or "No matches found.", duration_ms=0.0)
            except asyncio.TimeoutError:
                return ToolResult(tool_name="search_files", success=False, output="", error="Search timed out", duration_ms=30000.0)
            except OSError as exc:
                return ToolResult(tool_name="search_files", success=False, output="", error=str(exc), duration_ms=0.0)
        return ToolResult(tool_name="search_files", success=False, output="", error="find not available", duration_ms=0.0)
    except OSError as exc:
        return ToolResult(tool_name="search_files", success=False, output="", error=str(exc), duration_ms=0.0)


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------

@tool(
    name="patch",
    description="Find-and-replace edit in files",
    risk=Risk.MEDIUM,
    mutates=True,
    capabilities=["filesystem"],
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "old_string": {"type": "string", "description": "Text to find"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    },
)
async def patch_tool(arguments: dict[str, Any]) -> ToolResult:
    raw_path = arguments.get("path", "")
    if not raw_path:
        return ToolResult(tool_name="patch", success=False, output="", error="No path provided", duration_ms=0.0)

    file_path = _resolve_path(raw_path)
    old_string = arguments.get("old_string", "")
    new_string = arguments.get("new_string", "")
    replace_all = bool(arguments.get("replace_all", False))

    try:
        original = file_path.read_text(encoding="utf-8")
        count = original.count(old_string)

        if count == 0:
            return ToolResult(tool_name="patch", success=False, output="", error=f"old_string not found in {file_path}", duration_ms=0.0)
        if count > 1 and not replace_all:
            return ToolResult(
                tool_name="patch", success=False, output="",
                error=f"old_string found {count} times — set replace_all=true or be more specific",
                duration_ms=0.0,
            )

        updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
        file_path.write_text(updated, encoding="utf-8")

        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{file_path.name}",
            tofile=f"b/{file_path.name}",
        )
        return ToolResult(tool_name="patch", success=True, output="".join(diff) or "No diff (identical content)", duration_ms=0.0)

    except FileNotFoundError:
        return ToolResult(tool_name="patch", success=False, output="", error=f"File not found: {file_path}", duration_ms=0.0)
    except PermissionError:
        return ToolResult(tool_name="patch", success=False, output="", error=f"Permission denied: {file_path}", duration_ms=0.0)
    except OSError as exc:
        return ToolResult(tool_name="patch", success=False, output="", error=str(exc), duration_ms=0.0)
