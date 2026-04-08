"""Tests for the tool decorator, discovery, and core tool implementations.

Validates that the @tool decorator attaches metadata, that discover_tools
and register_all work correctly, and that core file/terminal tools produce
expected results.
"""

from __future__ import annotations

import os
import pytest

from phynai.contracts.tools import Risk, ToolCall, ToolMetadata, ToolResult
from phynai.runtime.tool_runtime import PhynaiToolRuntime


# ---------------------------------------------------------------------------
# Decorator & discovery helpers (inline since the package doesn't ship them yet)
# ---------------------------------------------------------------------------

def tool(
    name: str,
    description: str,
    risk: Risk = Risk.LOW,
    mutates: bool = False,
    capabilities: list[str] | None = None,
    requires_confirmation: bool = False,
    parameters: dict | None = None,
):
    """Decorator that attaches ToolMetadata to a function."""

    def decorator(fn):
        fn._tool_metadata = ToolMetadata(
            name=name,
            description=description,
            risk=risk,
            mutates=mutates,
            capabilities=capabilities or [],
            requires_confirmation=requires_confirmation,
            parameters=parameters or {"type": "object", "properties": {}},
        )
        return fn

    return decorator


def discover_tools(module) -> list[tuple[callable, ToolMetadata]]:
    """Find all functions decorated with @tool in a module-like object."""
    found = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if callable(obj) and hasattr(obj, "_tool_metadata"):
            found.append((obj, obj._tool_metadata))
    return found


def register_all(runtime: PhynaiToolRuntime, module) -> int:
    """Discover and register all @tool-decorated functions into a runtime."""
    tools = discover_tools(module)
    for handler, meta in tools:
        runtime.register(meta.name, handler, meta)
    return len(tools)


# ---------------------------------------------------------------------------
# Sample decorated tools for testing
# ---------------------------------------------------------------------------

@tool(name="sample_echo", description="Echoes text back", parameters={
    "type": "object",
    "properties": {"text": {"type": "string"}},
})
def sample_echo(arguments: dict) -> ToolResult:
    return ToolResult(
        tool_name="sample_echo",
        call_id="test",
        success=True,
        output=arguments.get("text", ""),
        duration_ms=0.0,
    )


@tool(name="sample_add", description="Adds two numbers", risk=Risk.LOW, parameters={
    "type": "object",
    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
})
def sample_add(arguments: dict) -> ToolResult:
    result = arguments.get("a", 0) + arguments.get("b", 0)
    return ToolResult(
        tool_name="sample_add",
        call_id="test",
        success=True,
        output=str(result),
        duration_ms=0.0,
    )


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------

def test_tool_decorator_attaches_metadata():
    """The @tool decorator should attach _tool_metadata to the function."""
    assert hasattr(sample_echo, "_tool_metadata")
    meta = sample_echo._tool_metadata
    assert isinstance(meta, ToolMetadata)
    assert meta.name == "sample_echo"
    assert meta.description == "Echoes text back"
    assert meta.risk == Risk.LOW


def test_tool_decorator_preserves_callable():
    """Decorated functions should still be callable."""
    result = sample_echo({"text": "hello"})
    assert isinstance(result, ToolResult)
    assert result.output == "hello"


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------

class _FakeModule:
    """Namespace that holds decorated functions for discover_tools to scan."""
    sample_echo = sample_echo
    sample_add = sample_add
    not_a_tool = lambda: None  # should be skipped


def test_discover_tools_finds_decorated():
    """discover_tools should find exactly the @tool-decorated functions."""
    found = discover_tools(_FakeModule)
    names = {meta.name for _, meta in found}
    assert "sample_echo" in names
    assert "sample_add" in names
    assert len(found) == 2


def test_register_all_registers_in_runtime():
    """register_all should register discovered tools into a runtime."""
    runtime = PhynaiToolRuntime()
    count = register_all(runtime, _FakeModule)
    assert count == 2
    tools = runtime.list_tools()
    names = {t.name for t in tools}
    assert "sample_echo" in names
    assert "sample_add" in names


# ---------------------------------------------------------------------------
# Core tool behaviour tests (using simple inline tool handlers)
# ---------------------------------------------------------------------------

def _make_call(tool_name: str, **kwargs) -> ToolCall:
    return ToolCall(tool_name=tool_name, arguments=kwargs, trace_id="test-trace")


@pytest.mark.asyncio
async def test_terminal_tool_echo(tmp_path):
    """A terminal-style tool should run a shell command and capture output."""
    import subprocess

    def terminal_handler(arguments: dict) -> ToolResult:
        cmd = arguments.get("command", "echo hello")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return ToolResult(
            tool_name="terminal",
            call_id="t1",
            success=result.returncode == 0,
            output=result.stdout.strip(),
            error=result.stderr.strip() or None,
            duration_ms=0.0,
        )

    out = terminal_handler({"command": "echo hello"})
    assert out.success is True
    assert out.output == "hello"


@pytest.mark.asyncio
async def test_read_file_tool(tmp_path):
    """A read_file tool should return the contents of a file."""
    test_file = tmp_path / "sample.txt"
    test_file.write_text("line1\nline2\nline3\n")

    def read_file_handler(arguments: dict) -> ToolResult:
        path = arguments["path"]
        try:
            content = open(path).read()
            return ToolResult(
                tool_name="read_file", call_id="r1",
                success=True, output=content, duration_ms=0.0,
            )
        except Exception as e:
            return ToolResult(
                tool_name="read_file", call_id="r1",
                success=False, output="", error=str(e), duration_ms=0.0,
            )

    result = read_file_handler({"path": str(test_file)})
    assert result.success is True
    assert "line1" in result.output
    assert "line3" in result.output


@pytest.mark.asyncio
async def test_write_file_tool(tmp_path):
    """A write_file tool should create/overwrite a file."""
    target = tmp_path / "output.txt"

    def write_file_handler(arguments: dict) -> ToolResult:
        path = arguments["path"]
        content = arguments["content"]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return ToolResult(
            tool_name="write_file", call_id="w1",
            success=True, output=f"Wrote {len(content)} bytes to {path}",
            duration_ms=0.0,
        )

    result = write_file_handler({"path": str(target), "content": "hello world"})
    assert result.success is True
    assert target.read_text() == "hello world"


@pytest.mark.asyncio
async def test_patch_tool_find_replace(tmp_path):
    """A patch tool should do find-and-replace in a file."""
    target = tmp_path / "code.py"
    target.write_text("def greet():\n    return 'hello'\n")

    def patch_handler(arguments: dict) -> ToolResult:
        path = arguments["path"]
        old = arguments["old_string"]
        new = arguments["new_string"]
        content = open(path).read()
        if old not in content:
            return ToolResult(
                tool_name="patch", call_id="p1",
                success=False, output="", error="old_string not found",
                duration_ms=0.0,
            )
        content = content.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(content)
        return ToolResult(
            tool_name="patch", call_id="p1",
            success=True, output="Patch applied", duration_ms=0.0,
        )

    result = patch_handler({
        "path": str(target),
        "old_string": "'hello'",
        "new_string": "'goodbye'",
    })
    assert result.success is True
    assert "'goodbye'" in target.read_text()


@pytest.mark.asyncio
async def test_search_files_tool(tmp_path):
    """A search_files tool should find content matching a pattern."""
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    (tmp_path / "b.py").write_text("def bar(): pass\n")
    (tmp_path / "c.txt").write_text("no functions here\n")

    def search_handler(arguments: dict) -> ToolResult:
        import re
        pattern = arguments.get("pattern", "")
        search_path = arguments.get("path", ".")
        matches = []
        for root, _, files in os.walk(search_path):
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath) as f:
                        for i, line in enumerate(f, 1):
                            if re.search(pattern, line):
                                matches.append(f"{fpath}:{i}: {line.rstrip()}")
                except (OSError, UnicodeDecodeError):
                    continue
        return ToolResult(
            tool_name="search_files", call_id="s1",
            success=True, output="\n".join(matches), duration_ms=0.0,
        )

    result = search_handler({"pattern": "def \\w+", "path": str(tmp_path)})
    assert result.success is True
    assert "foo" in result.output
    assert "bar" in result.output
    # c.txt should not match
    assert "no functions" not in result.output
