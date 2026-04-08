"""Tests for PolicyPipeline."""

import pytest

from phynai.contracts.policy import PolicyCheck, PolicyDecision, PolicyVerdict
from phynai.contracts.tools import Risk, ToolCall, ToolMetadata
from phynai.runtime.policy import PolicyPipeline


# --- Mock PolicyCheck implementations ---


class AllowPolicy:
    def __init__(self, policy_name: str = "allow_all"):
        self._name = policy_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
        return PolicyVerdict(
            decision=PolicyDecision.ALLOW,
            reason="allowed",
            policy_name=self._name,
        )


class DenyPolicy:
    def __init__(self, policy_name: str = "deny_all"):
        self._name = policy_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
        return PolicyVerdict(
            decision=PolicyDecision.DENY,
            reason="denied",
            policy_name=self._name,
        )


class ConfirmPolicy:
    def __init__(self, policy_name: str = "confirm_all"):
        self._name = policy_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
        return PolicyVerdict(
            decision=PolicyDecision.CONFIRM,
            reason="needs confirmation",
            policy_name=self._name,
        )


def _make_call() -> ToolCall:
    return ToolCall(tool_name="echo", arguments={}, trace_id="trace-1")


def _make_metadata() -> ToolMetadata:
    return ToolMetadata(
        name="echo",
        description="test",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["test"],
        requires_confirmation=False,
    )


@pytest.fixture
def pipeline() -> PolicyPipeline:
    return PolicyPipeline()


class TestEmptyPipeline:
    def test_returns_allow(self, pipeline: PolicyPipeline):
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.policy_name == "pipeline"


class TestSingleAllowPolicy:
    def test_single_allow(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy())
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.ALLOW


class TestDenyShortCircuits:
    def test_deny_wins_over_allow(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy("first"))
        pipeline.add(DenyPolicy("blocker"))
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.DENY
        assert verdict.policy_name == "blocker"

    def test_deny_first_short_circuits(self, pipeline: PolicyPipeline):
        pipeline.add(DenyPolicy("early_deny"))
        pipeline.add(AllowPolicy("late_allow"))
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.DENY
        assert verdict.policy_name == "early_deny"


class TestConfirmReturned:
    def test_confirm_when_no_deny(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy())
        pipeline.add(ConfirmPolicy("needs_confirm"))
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.CONFIRM
        assert verdict.policy_name == "needs_confirm"

    def test_first_confirm_wins(self, pipeline: PolicyPipeline):
        pipeline.add(ConfirmPolicy("first_confirm"))
        pipeline.add(ConfirmPolicy("second_confirm"))
        verdict = pipeline.evaluate(_make_call(), _make_metadata())
        assert verdict.decision == PolicyDecision.CONFIRM
        assert verdict.policy_name == "first_confirm"


class TestRemovePolicy:
    def test_remove_by_name(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy("a"))
        pipeline.add(DenyPolicy("b"))
        pipeline.remove("b")
        assert pipeline.list_policies() == ["a"]

    def test_remove_missing_raises_key_error(self, pipeline: PolicyPipeline):
        with pytest.raises(KeyError, match="not found"):
            pipeline.remove("nonexistent")


class TestClear:
    def test_clear_removes_all(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy())
        pipeline.add(DenyPolicy())
        pipeline.clear()
        assert len(pipeline) == 0
        assert pipeline.list_policies() == []


class TestListPolicies:
    def test_list_returns_names_in_order(self, pipeline: PolicyPipeline):
        pipeline.add(AllowPolicy("alpha"))
        pipeline.add(DenyPolicy("beta"))
        pipeline.add(ConfirmPolicy("gamma"))
        assert pipeline.list_policies() == ["alpha", "beta", "gamma"]
