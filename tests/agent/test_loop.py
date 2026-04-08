"""Tests for PhynaiAgent — the core agent execution loop.

Uses mock implementations of ClientManager, ToolRuntime, and other
dependencies to test the loop logic without real HTTP or tool execution.
"""

import json
import pytest

from phynai.agent.context import PhynaiContextManager
from phynai.agent.cost import PhynaiCostLedger
from phynai.agent.loop import PhynaiAgent
from phynai.agent.session import PhynaiSessionStore
from phynai.contracts.tools import ToolCall, ToolMetadata, ToolResult, Risk
from phynai.contracts.work import WorkItem, WorkConstraints, WorkStatus


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _make_text_response(content: str, model: str = "mock-model") -> dict:
    """Build a mock OpenAI-format response with no tool calls."""
    return {
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _make_tool_call_response(
    tool_name: str, arguments: dict, call_id: str = "tc-1", model: str = "mock-model"
) -> dict:
    """Build a mock OpenAI-format response with a single tool call."""
    return {
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class MockClientManager:
    """A mock ClientManager that returns predefined responses in sequence."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._call_index = 0
        self.model = "mock-model"
        self.provider = "mock"

    async def create_completion(self, messages, model=None, tools=None, stream=False):
        if self._call_index >= len(self._responses):
            return _make_text_response("(exhausted)")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


class MockToolRuntime:
    """A mock ToolRuntime with a single 'echo' tool."""

    def __init__(self):
        self._meta = ToolMetadata(
            name="echo",
            description="Echoes input back",
            risk=Risk.LOW,
            mutates=False,
            capabilities=["echo"],
            requires_confirmation=False,
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
            },
        )

    def register(self, name, handler, metadata):
        pass

    def unregister(self, name):
        pass

    def dispatch(self, call: ToolCall) -> ToolResult:
        text = call.arguments.get("text", "")
        return ToolResult(
            tool_name=call.tool_name,
            call_id=call.call_id,
            success=True,
            output=f"echo: {text}",
            duration_ms=1.0,
        )

    def use(self, middleware):
        pass

    def add_policy(self, policy):
        pass

    def list_tools(self) -> list[ToolMetadata]:
        return [self._meta]

    def get_metadata(self, name):
        return self._meta if name == "echo" else None


def _build_agent(
    responses: list[dict],
    tmp_path,
    tool_runtime=None,
) -> PhynaiAgent:
    """Build a PhynaiAgent with mock dependencies."""
    client = MockClientManager(responses)
    tools = tool_runtime or MockToolRuntime()
    context = PhynaiContextManager(system_prompt="You are a test agent.")
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()
    return PhynaiAgent(
        client=client,
        tools=tools,
        context=context,
        session=session,
        ledger=ledger,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simple_prompt_no_tool_calls(tmp_path):
    """A prompt where the LLM returns text directly produces a completed result."""
    agent = _build_agent(
        [_make_text_response("Hello, world!")],
        tmp_path,
    )
    wi = WorkItem(prompt="Say hello")
    result = await agent.run(wi)

    assert result.status == WorkStatus.completed
    assert result.response == "Hello, world!"
    assert result.work_id == wi.id
    assert result.cost.input_tokens > 0


@pytest.mark.asyncio
async def test_prompt_with_one_tool_call(tmp_path):
    """LLM calls a tool, gets result, then produces final text."""
    responses = [
        _make_tool_call_response("echo", {"text": "ping"}),
        _make_text_response("The echo said: echo: ping"),
    ]
    agent = _build_agent(responses, tmp_path)
    wi = WorkItem(prompt="Echo ping for me")
    result = await agent.run(wi)

    assert result.status == WorkStatus.completed
    assert "echo" in result.response.lower() or "ping" in result.response.lower()


@pytest.mark.asyncio
async def test_max_iterations_respected(tmp_path):
    """When the LLM always returns tool calls, the loop stops at max_iterations."""
    # Always return tool calls — never a text response
    responses = [
        _make_tool_call_response("echo", {"text": f"iter-{i}"}, call_id=f"tc-{i}")
        for i in range(20)
    ]
    agent = _build_agent(responses, tmp_path)
    constraints = WorkConstraints(max_iterations=3)
    wi = WorkItem(prompt="Loop forever", constraints=constraints)
    result = await agent.run(wi)

    # Should complete (not error) but stop after max_iterations
    assert result.status == WorkStatus.completed
    # The mock client should have been called exactly 3 times
    assert agent._client._call_index == 3


@pytest.mark.asyncio
async def test_exception_in_client_returns_failed(tmp_path):
    """If the client raises an exception, result status is failed."""

    class FailingClient:
        model = "fail-model"
        provider = "fail"

        async def create_completion(self, messages, model=None, tools=None, stream=False):
            raise RuntimeError("Connection refused")

    tools = MockToolRuntime()
    context = PhynaiContextManager(system_prompt="Test")
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()

    agent = PhynaiAgent(
        client=FailingClient(),
        tools=tools,
        context=context,
        session=session,
        ledger=ledger,
    )

    wi = WorkItem(prompt="This will fail")
    result = await agent.run(wi)

    assert result.status == WorkStatus.failed
    assert "Connection refused" in result.error


@pytest.mark.asyncio
async def test_cost_is_recorded_in_ledger(tmp_path):
    """After a run, costs should be recorded in the ledger."""
    responses = [_make_text_response("done")]
    client = MockClientManager(responses)
    tools = MockToolRuntime()
    context = PhynaiContextManager(system_prompt="Test")
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()

    agent = PhynaiAgent(
        client=client, tools=tools, context=context,
        session=session, ledger=ledger,
    )

    wi = WorkItem(prompt="Track cost")
    result = await agent.run(wi)

    assert result.status == WorkStatus.completed
    total = ledger.total()
    assert total.input_tokens > 0


@pytest.mark.asyncio
async def test_session_is_persisted(tmp_path):
    """After a run, the session should be saved and loadable."""
    responses = [_make_text_response("saved")]
    client = MockClientManager(responses)
    tools = MockToolRuntime()
    context = PhynaiContextManager(system_prompt="Test")
    session = PhynaiSessionStore(base_path=str(tmp_path))
    ledger = PhynaiCostLedger()

    agent = PhynaiAgent(
        client=client, tools=tools, context=context,
        session=session, ledger=ledger,
    )

    wi = WorkItem(prompt="Persist me")
    await agent.run(wi)

    loaded = await session.load(wi.session_id)
    assert loaded is not None
    msgs, meta = loaded
    assert len(msgs) > 0  # at least user + assistant messages
    assert meta["work_id"] == wi.id
