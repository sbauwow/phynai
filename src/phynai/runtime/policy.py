"""Policy pipeline — ordered evaluation of policy checks."""

from __future__ import annotations

from phynai.contracts.policy import PolicyCheck, PolicyDecision, PolicyVerdict
from phynai.contracts.tools import ToolCall, ToolMetadata


class PolicyPipeline:
    """Evaluates an ordered list of policies against a tool call.

    Evaluation semantics:
    - First DENY short-circuits and is returned immediately.
    - If no DENY but at least one CONFIRM, the first CONFIRM is returned.
    - If all policies ALLOW, an aggregate ALLOW verdict is returned.
    """

    def __init__(self) -> None:
        self._policies: list[PolicyCheck] = []

    def add(self, policy: PolicyCheck) -> None:
        """Append a policy check to the pipeline."""
        self._policies.append(policy)

    def remove(self, name: str) -> None:
        """Remove a policy by its name.

        Args:
            name: The name property of the policy to remove.

        Raises:
            KeyError: If no policy with that name exists.
        """
        for i, policy in enumerate(self._policies):
            if policy.name == name:
                self._policies.pop(i)
                return
        raise KeyError(f"Policy '{name}' not found in pipeline")

    def evaluate(self, call: ToolCall, metadata: ToolMetadata) -> PolicyVerdict:
        """Run all policies in order and return the aggregate verdict."""
        first_confirm: PolicyVerdict | None = None

        for policy in self._policies:
            verdict = policy.evaluate(call, metadata)

            if verdict.decision == PolicyDecision.DENY:
                return verdict

            if verdict.decision == PolicyDecision.CONFIRM and first_confirm is None:
                first_confirm = verdict

        if first_confirm is not None:
            return first_confirm

        return PolicyVerdict(
            decision=PolicyDecision.ALLOW,
            reason="all policies passed",
            policy_name="pipeline",
        )

    def list_policies(self) -> list[str]:
        """Return the names of all registered policies in order."""
        return [p.name for p in self._policies]

    def clear(self) -> None:
        """Remove all policies from the pipeline."""
        self._policies.clear()

    def __len__(self) -> int:
        return len(self._policies)

    def __repr__(self) -> str:
        return f"PolicyPipeline(policies={self.list_policies()})"
