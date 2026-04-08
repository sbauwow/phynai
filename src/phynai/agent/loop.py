"""PhynaiAgent — the core agent execution loop.

Implements the ``AgentCore`` protocol from ``phynai.contracts.agent``.
Orchestrates client, tools, context, sessions, and cost tracking into
a single ``run()`` method that processes a ``WorkItem`` end-to-end.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from phynai.contracts.agent import ClientManager, ContextManager, CostLedger, SessionStore
from phynai.contracts.runtime import ToolRuntime
from phynai.contracts.tools import ToolCall, ToolMetadata
from phynai.contracts.work import CostRecord, WorkItem, WorkResult, WorkStatus

logger = logging.getLogger(__name__)


class PhynaiAgent:
    """The main agent loop — takes a WorkItem and drives it to completion.

    Parameters
    ----------
    client:
        LLM client conforming to :class:`ClientManager`.
    tools:
        Tool runtime conforming to :class:`ToolRuntime`.
    context:
        Context manager conforming to :class:`ContextManager`.
    session:
        Session store conforming to :class:`SessionStore`.
    ledger:
        Cost ledger conforming to :class:`CostLedger`.
    """

    def __init__(
        self,
        client: ClientManager,
        tools: ToolRuntime,
        context: ContextManager,
        session: SessionStore,
        ledger: CostLedger,
        on_tool_start: Any | None = None,
    ) -> None:
        self._client = client
        self._tools = tools
        self._context = context
        self._session = session
        self._ledger = ledger
        self._on_tool_start = on_tool_start  # callback(tool_name: str) for UI hooks

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def run(self, work_item: WorkItem) -> WorkResult:
        """Execute a WorkItem through the agent loop and return a result.

        Steps:
        1. Load existing session (if any).
        2. Build system prompt via context manager.
        3. Initialise message history.
        4. Inject long-term memory.
        5. Append user prompt.
        6. Loop: call LLM → handle tool calls or collect final answer.
        7. Package result, record costs, save session.
        """
        total_cost = CostRecord(
            model=getattr(self._client, "model", ""),
            provider=getattr(self._client, "provider", ""),
        )

        try:
            # 1. Load session history
            history: list[dict[str, Any]] = []
            session_meta: dict[str, Any] = {}
            loaded = await self._session.load(work_item.session_id)
            if loaded is not None:
                history, session_meta = loaded

            # 2. Build system prompt
            system_prompt = self._context.build_system_prompt(work_item)

            # 3. Initialise messages
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
            ]
            messages.extend(history)

            # 4. (Memory injection — future milestone)

            # 5. Add user prompt
            messages.append({"role": "user", "content": work_item.prompt})

            # 6. Build tool schemas
            tool_schemas = self._build_tool_schemas()

            # Respect allowed_tools constraint
            if work_item.constraints.allowed_tools is not None:
                allowed = set(work_item.constraints.allowed_tools)
                tool_schemas = [
                    t for t in tool_schemas
                    if t.get("function", {}).get("name") in allowed
                ]

            # Filter out denied tools
            if work_item.constraints.denied_tools:
                denied = set(work_item.constraints.denied_tools)
                tool_schemas = [
                    t for t in tool_schemas
                    if t.get("function", {}).get("name") not in denied
                ]

            max_iterations = work_item.constraints.max_iterations
            final_text = ""

            # 7. Agent loop
            for iteration in range(max_iterations):
                logger.debug("Agent loop iteration %d/%d", iteration + 1, max_iterations)

                # Compress context if needed
                messages = self._context.compress(
                    messages, self._context._max_context_tokens
                    if hasattr(self._context, "_max_context_tokens")
                    else 128_000
                )

                response = await self._client.create_completion(
                    messages=messages,
                    model=getattr(self._client, "model", None),
                    tools=tool_schemas or None,
                )

                # Track cost
                step_cost = self._extract_cost(response)
                total_cost.input_tokens += step_cost.input_tokens
                total_cost.output_tokens += step_cost.output_tokens
                total_cost.estimated_cost_usd += step_cost.estimated_cost_usd

                # Parse assistant message
                choices = response.get("choices", [])
                if not choices:
                    final_text = ""
                    break

                assistant_msg = choices[0].get("message", {})
                messages.append(assistant_msg)

                tool_calls = assistant_msg.get("tool_calls")

                if tool_calls:
                    # Handle each tool call
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "")
                        raw_args = func.get("arguments", "{}")
                        try:
                            arguments = json.loads(raw_args)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Malformed tool arguments for %s (call %s): %s",
                                tool_name, tc.get("id", "?"), raw_args[:200],
                            )
                            arguments = {}

                        call = ToolCall(
                            tool_name=tool_name,
                            call_id=tc.get("id", str(uuid.uuid4())),
                            arguments=arguments,
                            trace_id=work_item.id,
                        )

                        logger.debug("Dispatching tool: %s", tool_name)
                        if self._on_tool_start:
                            self._on_tool_start(tool_name)
                        result = await self._tools.dispatch(call)

                        # Append tool result as a message
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": result.output if result.success else (result.error or "Tool failed"),
                        })
                else:
                    # No tool calls — this is the final answer
                    final_text = assistant_msg.get("content", "") or ""
                    break
            else:
                # Exhausted iterations
                final_text = assistant_msg.get("content", "") or ""  # type: ignore[possibly-undefined]

            # 8. Record costs
            self._ledger.record(work_item.id, total_cost)

            # 9. Save session (exclude system prompt from persisted history)
            await self._session.save(
                session_id=work_item.session_id,
                messages=messages[1:],  # skip system prompt
                metadata={
                    "work_id": work_item.id,
                    "source": work_item.source,
                    "cost": total_cost.model_dump(),
                },
            )

            # 10. Return result
            return WorkResult(
                work_id=work_item.id,
                status=WorkStatus.completed,
                response=final_text,
                cost=total_cost,
                completed_at=datetime.now(timezone.utc),
            )

        except Exception as exc:
            logger.exception("Agent run failed for work_id=%s", work_item.id)
            return WorkResult(
                work_id=work_item.id,
                status=WorkStatus.failed,
                error=str(exc),
                cost=total_cost,
                completed_at=datetime.now(timezone.utc),
            )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        """Convert registered ToolMetadata into OpenAI function-calling format."""
        tools: list[ToolMetadata] = self._tools.list_tools()
        schemas: list[dict[str, Any]] = []
        for meta in tools:
            schemas.append({
                "type": "function",
                "function": {
                    "name": meta.name,
                    "description": meta.description,
                    "parameters": meta.parameters or {"type": "object", "properties": {}},
                },
            })
        return schemas

    # Per-million-token pricing: (input, output)
    _PRICING: dict[str, tuple[float, float]] = {
        # Anthropic
        "claude-opus-4-6":          (15.0, 75.0),
        "claude-opus-4-20250916":   (15.0, 75.0),
        "claude-sonnet-4-6":        (3.0, 15.0),
        "claude-sonnet-4-20250514": (3.0, 15.0),
        "claude-haiku-4-5":         (0.80, 4.0),
        "claude-haiku-4-5-20251001":(0.80, 4.0),
        # OpenAI
        "gpt-4o":                   (2.50, 10.0),
        "gpt-4o-mini":              (0.15, 0.60),
        "gpt-4-turbo":              (10.0, 30.0),
        "o3":                       (2.0, 8.0),
        "o3-mini":                  (1.10, 4.40),
        "o4-mini":                  (1.10, 4.40),
    }

    @classmethod
    def _estimate_cost_usd(cls, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD from token counts and model pricing."""
        pricing = cls._PRICING.get(model)
        if pricing is None:
            # Try prefix match (e.g. "claude-opus-4-6-20260401" → "claude-opus-4-6")
            for key, val in cls._PRICING.items():
                if model.startswith(key):
                    pricing = val
                    break
        if pricing is None:
            return 0.0
        input_price, output_price = pricing
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    @classmethod
    def _extract_cost(cls, response: dict[str, Any]) -> CostRecord:
        """Parse token usage from an LLM response into a CostRecord."""
        usage = response.get("usage", {})
        model = response.get("model", "")
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cache_read = (
            usage.get("cache_read_input_tokens", 0)
            or usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        )
        cache_write = usage.get("cache_creation_input_tokens", 0)
        return CostRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            model=model,
            provider="",
            estimated_cost_usd=cls._estimate_cost_usd(model, input_tokens, output_tokens),
        )
