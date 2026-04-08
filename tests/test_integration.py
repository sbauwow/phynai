"""End-to-end integration test for the PhynAI agent stack.

Wires up a MockLLMClient, a real PhynaiToolRuntime with a mock read_file
tool, and all real agent components to verify the full request lifecycle
without hitting any external services.
"""

from __future__ import annotations

import json
import pytest

from phynai.agent.context import PhynaiContextManager
from phynai.agent.cost import PhynaiCostLedger
from phynai.agent.loop import PhynaiAgent
from phynai.agent.session import PhynaiSessionStore
from phynai.contracts.tools import Risk, ToolCall, ToolMetadata, ToolResult
from phynai.contracts.work import WorkItem, WorkStatus
from phynai.prompts import build_system_prompt


# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------

class MockLLMClient:
    """Fake LLM that returns a tool call on the first turn and text on the second."""

    def __init__(self) -> None:
        self._call_count = 0
        self.model = "mock-model"
        self.provider = "mock"

    async def create_completion(self, messages, model=None, tools=None, stream=False):
        self._call_count += 1

        if self._call_count == 1:
            # First call: ask to read a file
            return {
                "model": self.model,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-001",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "test.txt"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            }

        # Second call: return a text answer incorporating the tool result
        return {
            "model": self.model,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The file contains: hello world",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 80, "completion_tokens": 15},
        }


# ---------------------------------------------------------------------------
# Mock tool runtime (sync dispatch, matching loop.py expectations)
# ---------------------------------------------------------------------------

class MockToolRuntime:
    """Minimal runtime that only knows about a mock read_file tool."""

    _meta = ToolMetadata(
        name="read_file",
        description="Read a file from disk",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["filesystem"],
        requires_confirmation=False,
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
            },
            "required": ["path"],
        },
    )

    def list_tools(self) -> list[ToolMetadata]:
        return [self._meta]

    def get_metadata(self, name: str):
        return self._meta if name == "read_file" else None

    def dispatch(self, call: ToolCall) -> ToolResult:
        if call.tool_name == "read_file":
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=True,
                output="hello world",
                duration_ms=0.5,
            )
        return ToolResult(
            tool_name=call.tool_name,
            call_id=call.call_id,
            success=False,
            output="",
            error=f"Unknown tool: {call.tool_name}",
            duration_ms=0.0,
        )

    def register(self, name, handler, metadata):
        pass

    def unregister(self, name):
        pass

    def use(self, middleware):
        pass

    def add_policy(self, policy):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_agent_lifecycle(tmp_path):
    """Run a complete work item through the agent and verify the outcome."""
    system_prompt = build_system_prompt(["read_file"], workdir="/tmp")

    client = MockLLMClient()
    tools = MockToolRuntime()
    context = PhynaiContextManager(system_prompt=system_prompt)
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()

    agent = PhynaiAgent(
        client=client,
        tools=tools,
        context=context,
        session=session,
        ledger=ledger,
    )

    work_item = WorkItem(prompt="Read test.txt")
    result = await agent.run(work_item)

    # Status should be completed
    assert result.status == WorkStatus.completed

    # Response should contain the expected text
    assert "The file contains: hello world" in result.response

    # Cost tracking should have accumulated tokens from both LLM calls
    assert result.cost.input_tokens == 130  # 50 + 80
    assert result.cost.output_tokens == 35  # 20 + 15

    # Ledger should have recorded the cost
    total = ledger.total()
    assert total.input_tokens == 130

    # LLM was called exactly twice (tool call + final answer)
    assert client._call_count == 2

    # Session should be persisted
    loaded = await session.load(work_item.session_id)
    assert loaded is not None
    messages, meta = loaded
    assert len(messages) >= 3  # user, assistant (tool call), tool, assistant (text)
    assert meta["work_id"] == work_item.id


@pytest.mark.asyncio
async def test_system_prompt_is_used(tmp_path):
    """Verify the system prompt is included in the messages sent to the LLM."""
    captured_messages = []

    class CapturingClient:
        model = "capture-model"
        provider = "test"

        async def create_completion(self, messages, model=None, tools=None, stream=False):
            captured_messages.extend(messages)
            return {
                "model": self.model,
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }

    system_prompt = build_system_prompt(["read_file", "terminal"], workdir="/home/user")
    context = PhynaiContextManager(system_prompt=system_prompt)
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()

    agent = PhynaiAgent(
        client=CapturingClient(),
        tools=MockToolRuntime(),
        context=context,
        session=session,
        ledger=ledger,
    )

    await agent.run(WorkItem(prompt="hi"))

    # The system message should contain our prompt text
    system_msg = captured_messages[0]
    assert system_msg["role"] == "system"
    assert "PhynAI" in system_msg["content"]
    assert "read_file" in system_msg["content"]
    assert "/home/user" in system_msg["content"]
