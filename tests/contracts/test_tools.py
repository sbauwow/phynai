"""Tests for phynai.contracts.tools — ToolMetadata, ToolCall, ToolResult, etc."""

import uuid
from typing import Any

import pytest

from phynai.contracts import Risk, ToolCall, ToolHandler, ToolMetadata, ToolResult


# ---------------------------------------------------------------------------
# Risk enum
# ---------------------------------------------------------------------------

def test_risk_enum_values():
    assert set(Risk) == {Risk.LOW, Risk.MEDIUM, Risk.HIGH, Risk.CRITICAL}
    assert Risk.LOW.value == "LOW"
    assert Risk.MEDIUM.value == "MEDIUM"
    assert Risk.HIGH.value == "HIGH"
    assert Risk.CRITICAL.value == "CRITICAL"


def test_risk_is_str_enum():
    assert isinstance(Risk.LOW, str)


# ---------------------------------------------------------------------------
# ToolMetadata
# ---------------------------------------------------------------------------

def test_tool_metadata_creation():
    tm = ToolMetadata(
        name="bash",
        description="Run shell commands",
        risk=Risk.HIGH,
        mutates=True,
        capabilities=["execute"],
        requires_confirmation=True,
        parameters={"type": "object", "properties": {"cmd": {"type": "string"}}},
        tags=["shell", "execute"],
    )
    assert tm.name == "bash"
    assert tm.description == "Run shell commands"
    assert tm.risk == Risk.HIGH
    assert tm.mutates is True
    assert tm.capabilities == ["execute"]
    assert tm.requires_confirmation is True
    assert "cmd" in tm.parameters["properties"]
    assert tm.tags == ["shell", "execute"]


def test_tool_metadata_defaults():
    tm = ToolMetadata(
        name="read",
        description="Read a file",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["read"],
        requires_confirmation=False,
    )
    assert tm.parameters == {}
    assert tm.tags == []


def test_tool_metadata_serialization_round_trip():
    tm = ToolMetadata(
        name="write",
        description="Write a file",
        risk=Risk.MEDIUM,
        mutates=True,
        capabilities=["write"],
        requires_confirmation=True,
        parameters={"type": "object"},
        tags=["fs"],
    )
    data = tm.model_dump()
    restored = ToolMetadata.model_validate(data)
    assert restored.name == tm.name
    assert restored.risk == tm.risk
    assert restored.mutates == tm.mutates
    assert restored.tags == tm.tags


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------

def test_tool_call_creation_with_uuid_default():
    tc = ToolCall(
        tool_name="bash",
        arguments={"cmd": "ls"},
        trace_id="trace-abc",
    )
    assert tc.tool_name == "bash"
    # call_id should be a valid UUID
    uuid.UUID(tc.call_id)
    assert tc.arguments == {"cmd": "ls"}
    assert tc.trace_id == "trace-abc"


def test_tool_call_unique_ids():
    a = ToolCall(tool_name="x", arguments={}, trace_id="t")
    b = ToolCall(tool_name="x", arguments={}, trace_id="t")
    assert a.call_id != b.call_id


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

def test_tool_result_success():
    tr = ToolResult(
        tool_name="bash",
        success=True,
        output="hello world",
        duration_ms=42.5,
    )
    assert tr.tool_name == "bash"
    assert tr.success is True
    assert tr.output == "hello world"
    assert tr.error is None
    assert tr.duration_ms == 42.5
    assert tr.artifacts == []


def test_tool_result_failure():
    tr = ToolResult(
        tool_name="bash",
        success=False,
        output="",
        error="command not found",
        duration_ms=1.0,
    )
    assert tr.success is False
    assert tr.error == "command not found"


def test_tool_result_with_artifacts():
    tr = ToolResult(
        tool_name="write",
        success=True,
        output="wrote file",
        duration_ms=10.0,
        artifacts=["/tmp/out.txt"],
    )
    assert tr.artifacts == ["/tmp/out.txt"]


# ---------------------------------------------------------------------------
# ToolHandler protocol
# ---------------------------------------------------------------------------

def test_tool_handler_protocol_isinstance():
    """A class implementing __call__(arguments) -> ToolResult satisfies ToolHandler."""

    class MyHandler:
        def __call__(self, arguments: dict[str, Any]) -> ToolResult:
            return ToolResult(tool_name="test", success=True, output="ok", duration_ms=0.0)

    handler = MyHandler()
    assert isinstance(handler, ToolHandler)


def test_non_handler_fails_isinstance():
    """A plain object should not satisfy the ToolHandler protocol."""

    class NotAHandler:
        pass

    assert not isinstance(NotAHandler(), ToolHandler)
