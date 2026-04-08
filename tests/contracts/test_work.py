"""Tests for phynai.contracts.work — WorkItem, WorkResult, enums, etc."""

import json
import uuid
from datetime import datetime, timezone

import pytest

from phynai.contracts import (
    Artifact,
    CostRecord,
    WorkConstraints,
    WorkItem,
    WorkPriority,
    WorkResult,
    WorkStatus,
)


# ---------------------------------------------------------------------------
# WorkStatus enum
# ---------------------------------------------------------------------------

def test_work_status_enum_values():
    expected = {"pending", "assigned", "running", "completed", "failed", "blocked", "delegated", "cancelled"}
    assert {s.value for s in WorkStatus} == expected


def test_work_status_is_str_enum():
    assert isinstance(WorkStatus.pending, str)
    assert WorkStatus.pending == "pending"


# ---------------------------------------------------------------------------
# WorkPriority enum
# ---------------------------------------------------------------------------

def test_work_priority_enum_values():
    expected = {"critical", "high", "normal", "low"}
    assert {p.value for p in WorkPriority} == expected


def test_work_priority_ordering():
    """Priority members are defined in descending importance order."""
    members = list(WorkPriority)
    assert members == [WorkPriority.critical, WorkPriority.high, WorkPriority.normal, WorkPriority.low]


# ---------------------------------------------------------------------------
# WorkConstraints
# ---------------------------------------------------------------------------

def test_work_constraints_defaults():
    c = WorkConstraints()
    assert c.max_iterations == 50
    assert c.max_tokens is None
    assert c.allowed_tools is None
    assert c.denied_tools == []
    assert c.timeout_seconds == 300


def test_work_constraints_custom():
    c = WorkConstraints(max_iterations=10, max_tokens=4096, allowed_tools=["bash"], denied_tools=["rm"], timeout_seconds=60)
    assert c.max_iterations == 10
    assert c.max_tokens == 4096
    assert c.allowed_tools == ["bash"]
    assert c.denied_tools == ["rm"]
    assert c.timeout_seconds == 60


# ---------------------------------------------------------------------------
# WorkItem
# ---------------------------------------------------------------------------

def test_work_item_defaults():
    wi = WorkItem(prompt="do something")
    # id is a valid uuid4
    uuid.UUID(wi.id)
    assert wi.prompt == "do something"
    assert wi.context == {}
    assert isinstance(wi.constraints, WorkConstraints)
    assert wi.priority == WorkPriority.normal
    assert wi.parent_id is None
    uuid.UUID(wi.session_id)
    assert wi.source == "direct"
    assert wi.metadata == {}
    assert isinstance(wi.created_at, datetime)


def test_work_item_with_full_params():
    now = datetime.now(timezone.utc)
    wi = WorkItem(
        id="custom-id",
        prompt="full params",
        context={"key": "value"},
        constraints=WorkConstraints(max_iterations=5),
        priority=WorkPriority.high,
        parent_id="parent-123",
        session_id="session-456",
        source="api",
        metadata={"tag": "test"},
        created_at=now,
    )
    assert wi.id == "custom-id"
    assert wi.prompt == "full params"
    assert wi.context == {"key": "value"}
    assert wi.constraints.max_iterations == 5
    assert wi.priority == WorkPriority.high
    assert wi.parent_id == "parent-123"
    assert wi.session_id == "session-456"
    assert wi.source == "api"
    assert wi.metadata == {"tag": "test"}
    assert wi.created_at == now


def test_work_item_unique_ids():
    a = WorkItem(prompt="a")
    b = WorkItem(prompt="b")
    assert a.id != b.id
    assert a.session_id != b.session_id


def test_work_item_json_schema():
    schema = WorkItem.model_json_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
    # Validate it's valid JSON by round-tripping
    dumped = json.dumps(schema)
    loaded = json.loads(dumped)
    assert loaded == schema


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def test_artifact_creation():
    a = Artifact(type="file", path="/tmp/out.txt", description="output file")
    assert a.type == "file"
    assert a.path == "/tmp/out.txt"
    assert a.url is None
    assert a.description == "output file"


def test_artifact_defaults():
    a = Artifact(type="pr")
    assert a.path is None
    assert a.url is None
    assert a.description == ""


# ---------------------------------------------------------------------------
# CostRecord
# ---------------------------------------------------------------------------

def test_cost_record_defaults():
    c = CostRecord()
    assert c.input_tokens == 0
    assert c.output_tokens == 0
    assert c.cache_read_tokens == 0
    assert c.cache_write_tokens == 0
    assert c.model == ""
    assert c.provider == ""
    assert c.estimated_cost_usd == 0.0
    assert c.actual_cost_usd is None
    assert c.status == "estimated"


def test_cost_record_arithmetic():
    """Two CostRecords can be summed manually by field."""
    a = CostRecord(input_tokens=100, output_tokens=50, estimated_cost_usd=0.01)
    b = CostRecord(input_tokens=200, output_tokens=80, estimated_cost_usd=0.02)
    total = CostRecord(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        estimated_cost_usd=a.estimated_cost_usd + b.estimated_cost_usd,
    )
    assert total.input_tokens == 300
    assert total.output_tokens == 130
    assert abs(total.estimated_cost_usd - 0.03) < 1e-9


# ---------------------------------------------------------------------------
# WorkResult
# ---------------------------------------------------------------------------

def test_work_result_defaults():
    r = WorkResult(work_id="w1", status=WorkStatus.completed)
    assert r.work_id == "w1"
    assert r.status == WorkStatus.completed
    assert r.response == ""
    assert r.artifacts == []
    assert isinstance(r.cost, CostRecord)
    assert r.events == []
    assert r.blocked_on is None
    assert r.error is None
    assert r.completed_at is None


def test_work_result_serialization_round_trip():
    r = WorkResult(
        work_id="w2",
        status=WorkStatus.failed,
        response="something went wrong",
        artifacts=[Artifact(type="log", path="/tmp/log.txt")],
        cost=CostRecord(input_tokens=500, output_tokens=200, estimated_cost_usd=0.05),
        error="timeout",
        completed_at=datetime.now(timezone.utc),
    )
    data = r.model_dump()
    restored = WorkResult.model_validate(data)
    assert restored.work_id == r.work_id
    assert restored.status == r.status
    assert restored.response == r.response
    assert len(restored.artifacts) == 1
    assert restored.artifacts[0].type == "log"
    assert restored.cost.input_tokens == 500
    assert restored.error == "timeout"
    assert restored.completed_at == r.completed_at
