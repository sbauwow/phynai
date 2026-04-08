"""Tests for PhynaiCostLedger — in-memory cost tracking."""

import pytest

from phynai.agent.cost import PhynaiCostLedger
from phynai.contracts.work import CostRecord


def test_record_and_total():
    """Recording a cost and calling total returns the accumulated values."""
    ledger = PhynaiCostLedger()
    cost = CostRecord(
        input_tokens=100,
        output_tokens=50,
        model="gpt-4o",
        provider="openai",
        estimated_cost_usd=0.01,
    )
    ledger.record("work-1", cost)

    total = ledger.total()
    assert total.input_tokens == 100
    assert total.output_tokens == 50
    assert total.estimated_cost_usd == pytest.approx(0.01)
    assert total.model == "gpt-4o"


def test_total_with_no_records_returns_zero():
    """total() with no recorded costs returns a zeroed CostRecord."""
    ledger = PhynaiCostLedger()
    total = ledger.total()
    assert total.input_tokens == 0
    assert total.output_tokens == 0
    assert total.estimated_cost_usd == 0.0
    assert total.model == ""


def test_by_model_groups_correctly():
    """by_model groups costs by model name and sums them."""
    ledger = PhynaiCostLedger()

    ledger.record("w1", CostRecord(input_tokens=10, output_tokens=5, model="gpt-4o"))
    ledger.record("w2", CostRecord(input_tokens=20, output_tokens=10, model="claude-3"))
    ledger.record("w3", CostRecord(input_tokens=30, output_tokens=15, model="gpt-4o"))

    by_model = ledger.by_model()
    assert "gpt-4o" in by_model
    assert "claude-3" in by_model

    assert by_model["gpt-4o"].input_tokens == 40
    assert by_model["gpt-4o"].output_tokens == 20
    assert by_model["claude-3"].input_tokens == 20
    assert by_model["claude-3"].output_tokens == 10


def test_multiple_records_same_work_id_accumulate():
    """Multiple records for the same work_id accumulate in total."""
    ledger = PhynaiCostLedger()

    ledger.record("w1", CostRecord(input_tokens=10, output_tokens=5, model="gpt-4o"))
    ledger.record("w1", CostRecord(input_tokens=20, output_tokens=10, model="gpt-4o"))

    total = ledger.total()
    assert total.input_tokens == 30
    assert total.output_tokens == 15


def test_total_filtered_by_session_id():
    """total(session_id=...) returns cost only for that work_id."""
    ledger = PhynaiCostLedger()

    ledger.record("w1", CostRecord(input_tokens=10, output_tokens=5, model="gpt-4o"))
    ledger.record("w2", CostRecord(input_tokens=100, output_tokens=50, model="gpt-4o"))

    total_w1 = ledger.total(session_id="w1")
    assert total_w1.input_tokens == 10
    assert total_w1.output_tokens == 5


def test_total_filtered_nonexistent_returns_zero():
    """total(session_id=...) for unknown id returns zero CostRecord."""
    ledger = PhynaiCostLedger()
    ledger.record("w1", CostRecord(input_tokens=10, model="gpt-4o"))

    total = ledger.total(session_id="nonexistent")
    assert total.input_tokens == 0
    assert total.output_tokens == 0


def test_cache_tokens_tracked():
    """Cache read/write tokens are summed correctly."""
    ledger = PhynaiCostLedger()
    ledger.record("w1", CostRecord(cache_read_tokens=50, cache_write_tokens=30, model="gpt-4o"))
    ledger.record("w1", CostRecord(cache_read_tokens=10, cache_write_tokens=20, model="gpt-4o"))

    total = ledger.total()
    assert total.cache_read_tokens == 60
    assert total.cache_write_tokens == 50


