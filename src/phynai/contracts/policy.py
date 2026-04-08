"""Policy pipeline contracts — decisions, verdicts, and policy check protocol."""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from phynai.contracts.tools import ToolCall, ToolMetadata


class PolicyDecision(str, enum.Enum):
    """Outcome of a policy evaluation."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    CONFIRM = "CONFIRM"


class PolicyVerdict(BaseModel):
    """Result of a single policy check."""

    decision: PolicyDecision
    reason: str
    policy_name: str


@runtime_checkable
class PolicyCheck(Protocol):
    """Protocol for a policy that evaluates tool calls."""

    @property
    def name(self) -> str: ...

    def evaluate(self, tool_call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict: ...
