"""Tests for phynai.contracts.policy — PolicyDecision, PolicyVerdict, PolicyCheck."""

import pytest

from phynai.contracts import (
    PolicyCheck,
    PolicyDecision,
    PolicyVerdict,
    Risk,
    ToolCall,
    ToolMetadata,
)


# ---------------------------------------------------------------------------
# PolicyDecision enum
# ---------------------------------------------------------------------------

def test_policy_decision_enum_values():
    assert set(PolicyDecision) == {PolicyDecision.ALLOW, PolicyDecision.DENY, PolicyDecision.CONFIRM}
    assert PolicyDecision.ALLOW.value == "ALLOW"
    assert PolicyDecision.DENY.value == "DENY"
    assert PolicyDecision.CONFIRM.value == "CONFIRM"


def test_policy_decision_is_str_enum():
    assert isinstance(PolicyDecision.ALLOW, str)


# ---------------------------------------------------------------------------
# PolicyVerdict
# ---------------------------------------------------------------------------

def test_policy_verdict_creation():
    v = PolicyVerdict(
        decision=PolicyDecision.ALLOW,
        reason="tool is safe",
        policy_name="safety_check",
    )
    assert v.decision == PolicyDecision.ALLOW
    assert v.reason == "tool is safe"
    assert v.policy_name == "safety_check"


def test_policy_verdict_deny():
    v = PolicyVerdict(
        decision=PolicyDecision.DENY,
        reason="tool is too dangerous",
        policy_name="risk_gate",
    )
    assert v.decision == PolicyDecision.DENY


def test_policy_verdict_serialization_round_trip():
    v = PolicyVerdict(
        decision=PolicyDecision.CONFIRM,
        reason="requires user approval",
        policy_name="confirmation",
    )
    data = v.model_dump()
    restored = PolicyVerdict.model_validate(data)
    assert restored.decision == v.decision
    assert restored.reason == v.reason
    assert restored.policy_name == v.policy_name


# ---------------------------------------------------------------------------
# PolicyCheck protocol
# ---------------------------------------------------------------------------

def test_policy_check_protocol_isinstance():
    """A class with name property and evaluate method satisfies PolicyCheck."""

    class MyPolicy:
        @property
        def name(self) -> str:
            return "my_policy"

        def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
            return PolicyVerdict(
                decision=PolicyDecision.ALLOW,
                reason="ok",
                policy_name=self.name,
            )

    p = MyPolicy()
    assert isinstance(p, PolicyCheck)


def test_non_policy_fails_isinstance():

    class NotAPolicy:
        pass

    assert not isinstance(NotAPolicy(), PolicyCheck)
