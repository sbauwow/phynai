"""PhynaiContextManager — prompt construction and context compression.

Implements the ``ContextManager`` protocol from ``phynai.contracts.agent``.
"""

from __future__ import annotations

import json
from typing import Any

from phynai.contracts.work import WorkItem


class PhynaiContextManager:
    """Builds, compresses, and enriches the message context for the LLM.

    Parameters
    ----------
    system_prompt:
        Base system prompt prepended to every conversation.
    max_context_tokens:
        Hard ceiling for estimated token count.
    """

    def __init__(
        self,
        system_prompt: str = "",
        max_context_tokens: int = 128_000,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_context_tokens = max_context_tokens

    # -- prompt construction ------------------------------------------------

    def build_system_prompt(self, work_item: WorkItem) -> str:
        """Generate the system prompt for a given work item.

        Combines the base prompt with serialised work-item context and
        any tool-allowlist constraints.
        """
        parts: list[str] = []

        if self._system_prompt:
            parts.append(self._system_prompt)

        # Serialize work-item context if present
        if work_item.context:
            parts.append(
                "## Additional Context\n"
                + json.dumps(work_item.context, indent=2, default=str)
            )

        # Mention allowed tools if constrained
        if work_item.constraints.allowed_tools:
            tools_str = ", ".join(work_item.constraints.allowed_tools)
            parts.append(
                f"## Allowed Tools\nYou may ONLY use: {tools_str}"
            )

        return "\n\n".join(parts)

    # -- compression --------------------------------------------------------

    @staticmethod
    def _estimate_tokens(msg: dict[str, Any]) -> int:
        """Rough token estimate: 1 token ≈ 4 characters."""
        return len(str(msg)) // 4

    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Compress message history to fit within *target_tokens*.

        Current strategy: keep the first message (system prompt) plus as
        many trailing messages as fit.  A future version will use
        LLM-based summarisation for the pruned middle.
        """
        if not messages:
            return messages

        total = sum(self._estimate_tokens(m) for m in messages)
        if total <= target_tokens:
            return messages

        # Always keep the system message
        system = [messages[0]]
        budget = target_tokens - self._estimate_tokens(messages[0])

        kept: list[dict[str, Any]] = []
        for msg in reversed(messages[1:]):
            cost = self._estimate_tokens(msg)
            if budget - cost < 0:
                break
            kept.insert(0, msg)
            budget -= cost

        return system + kept

    # -- memory injection ---------------------------------------------------
    # TODO: long-term memory integration (RAG retrieval → inject into messages)
