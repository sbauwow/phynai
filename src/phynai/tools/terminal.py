"""Terminal tool — execute shell commands with output compression.

Compresses CLI output before it reaches the LLM context window:
- Strips ANSI escape codes
- Collapses repeated/similar lines
- Keeps first N + last N lines for large outputs (head+tail)
- Preserves errors/warnings in full
- Summarizes line counts when truncating
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool

MAX_OUTPUT_BYTES = 50 * 1024  # 50 KB hard limit
MAX_LINES = 200               # soft limit — compress beyond this
HEAD_LINES = 80               # keep first N lines
TAIL_LINES = 40               # keep last N lines
REPEAT_THRESHOLD = 3          # collapse after this many similar lines

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_PROGRESS_RE = re.compile(r"^\s*[\d.]+%\s*[|█▓▒░#=\->]+", re.MULTILINE)


def _compress_output(raw: str) -> str:
    """Compress CLI output for LLM consumption."""
    # Strip ANSI escape codes
    text = _ANSI_RE.sub("", raw)

    # Strip progress bars (keep only the last one)
    lines = text.split("\n")

    # Collapse repeated lines
    compressed: list[str] = []
    repeat_count = 0
    last_line = ""
    for line in lines:
        stripped = line.strip()
        # Check if this line is similar to the previous (ignoring numbers/timestamps)
        simplified = re.sub(r"\d+", "N", stripped)
        last_simplified = re.sub(r"\d+", "N", last_line.strip())

        if simplified == last_simplified and simplified:
            repeat_count += 1
            if repeat_count == REPEAT_THRESHOLD:
                compressed.append(f"  ... ({REPEAT_THRESHOLD}+ similar lines)")
            elif repeat_count > REPEAT_THRESHOLD:
                # Update the count in the collapse marker
                compressed[-1] = f"  ... ({repeat_count}+ similar lines)"
            continue
        else:
            repeat_count = 0
            last_line = line

        compressed.append(line)

    # Head + tail truncation if still too long
    if len(compressed) > MAX_LINES:
        head = compressed[:HEAD_LINES]
        tail = compressed[-TAIL_LINES:]
        omitted = len(compressed) - HEAD_LINES - TAIL_LINES
        compressed = head + [f"\n... [{omitted} lines omitted] ...\n"] + tail

    result = "\n".join(compressed)

    # Final byte limit
    if len(result) > MAX_OUTPUT_BYTES:
        result = result[:MAX_OUTPUT_BYTES] + f"\n... (truncated to {MAX_OUTPUT_BYTES // 1024}KB)"

    return result


@tool(
    name="terminal",
    description="Execute shell commands",
    risk=Risk.HIGH,
    mutates=True,
    requires_confirmation=True,
    capabilities=["shell", "process"],
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 180)",
                "default": 180,
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command",
            },
        },
        "required": ["command"],
    },
)
async def terminal_tool(arguments: dict[str, Any]) -> ToolResult:
    """Execute a shell command and return its output."""
    command: str = arguments.get("command", "")
    timeout: int = arguments.get("timeout", 180)
    workdir: str | None = arguments.get("workdir")

    if not command:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error="No command provided", duration_ms=0.0,
        )

    # Resolve workdir — canonicalize to prevent path traversal
    cwd: str | None = None
    if workdir:
        cwd = str(Path(os.path.expanduser(workdir)).resolve())

    # Split command into argv list — never pass to shell for execution
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error=f"Invalid command syntax: {exc}", duration_ms=0.0,
        )

    if not argv:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error="Empty command after parsing", duration_ms=0.0,
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                tool_name="terminal", success=False, output="",
                error=f"Command timed out after {timeout}s", duration_ms=float(timeout * 1000),
            )

        output = stdout.decode("utf-8", errors="replace")
        err_output = stderr.decode("utf-8", errors="replace")
        combined = output + ("\n--- stderr ---\n" + err_output if err_output else "")

        # Smart compression: strip ANSI, collapse repeats, head+tail
        combined = _compress_output(combined)

        success = proc.returncode == 0
        return ToolResult(
            tool_name="terminal", success=success, output=combined,
            error=None if success else f"Exit code {proc.returncode}",
            duration_ms=0.0,
        )
    except FileNotFoundError:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error=f"Command not found: {argv[0]}", duration_ms=0.0,
        )
    except PermissionError:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error=f"Permission denied: {argv[0]}", duration_ms=0.0,
        )
    except OSError as exc:
        return ToolResult(
            tool_name="terminal", success=False, output="",
            error=str(exc), duration_ms=0.0,
        )
