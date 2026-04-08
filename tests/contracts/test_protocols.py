"""Tests for all runtime-checkable protocols in phynai.contracts.

For each protocol we create a minimal implementing class and verify isinstance.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from phynai.contracts import (
    # Layer 1-2
    ToolRuntime,
    Middleware,
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
    PolicyCheck,
    PolicyDecision,
    PolicyVerdict,
    ToolCall,
    ToolHandler,
    ToolMetadata,
    ToolResult,
    Risk,
    # Layer 3
    AgentCore,
    ClientManager,
    ContextManager,
    CostLedger,
    CostRecord,
    SessionStore,
    WorkItem,
    WorkResult,
    WorkStatus,
    # Layer 4
    DependencyGraph,
    Scheduler,
    WorkSink,
    WorkSource,
    # Layer 5
    APIInterface,
    CLIInterface,
    GatewayInterface,
    Interface,
)


# ---------------------------------------------------------------------------
# ToolRuntime
# ---------------------------------------------------------------------------

def test_tool_runtime_protocol():
    class MyRuntime:
        def register(self, name, handler, metadata):
            pass

        def unregister(self, name):
            pass

        def dispatch(self, call):
            pass

        def use(self, middleware):
            pass

        def add_policy(self, policy):
            pass

        def list_tools(self):
            return []

        def get_metadata(self, name):
            return None

    assert isinstance(MyRuntime(), ToolRuntime)


# ---------------------------------------------------------------------------
# AgentCore
# ---------------------------------------------------------------------------

def test_agent_core_protocol():
    class MyAgent:
        async def run(self, work_item):
            return WorkResult(work_id="w", status=WorkStatus.completed)

    assert isinstance(MyAgent(), AgentCore)


# ---------------------------------------------------------------------------
# ClientManager
# ---------------------------------------------------------------------------

def test_client_manager_protocol():
    class MyClient:
        async def create_completion(self, messages, model, tools=None, stream=False):
            return {}

        def list_models(self):
            return []

    assert isinstance(MyClient(), ClientManager)


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

def test_context_manager_protocol():
    class MyCtx:
        def build_system_prompt(self, work_item):
            return ""

        def compress(self, messages, target_tokens):
            return messages

        def inject_memory(self, messages):
            return messages

    assert isinstance(MyCtx(), ContextManager)


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

def test_session_store_protocol():
    class MyStore:
        async def save(self, session_id, messages, metadata):
            pass

        async def load(self, session_id):
            return None

        async def list_sessions(self, limit=20):
            return []

        async def search(self, query):
            return []

    assert isinstance(MyStore(), SessionStore)


# ---------------------------------------------------------------------------
# CostLedger
# ---------------------------------------------------------------------------

def test_cost_ledger_protocol():
    class MyLedger:
        def record(self, work_id, cost):
            pass

        def total(self, session_id=None):
            return CostRecord()

        def by_model(self):
            return {}

    assert isinstance(MyLedger(), CostLedger)


# ---------------------------------------------------------------------------
# WorkSource
# ---------------------------------------------------------------------------

def test_work_source_protocol():
    class MySource:
        async def poll(self):
            return []

        async def report(self, result):
            pass

        async def lock(self, item_id):
            return True

        async def release(self, item_id):
            pass

    assert isinstance(MySource(), WorkSource)


# ---------------------------------------------------------------------------
# WorkSink
# ---------------------------------------------------------------------------

def test_work_sink_protocol():
    class MySink:
        async def deliver(self, result):
            pass

    assert isinstance(MySink(), WorkSink)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def test_scheduler_protocol():
    class MySched:
        async def next(self):
            return None

        def add_source(self, source):
            pass

        def add_sink(self, sink):
            pass

        async def run(self):
            pass

    assert isinstance(MySched(), Scheduler)


# ---------------------------------------------------------------------------
# DependencyGraph
# ---------------------------------------------------------------------------

def test_dependency_graph_protocol():
    class MyGraph:
        def add_edge(self, from_id, to_id):
            pass

        def is_eligible(self, item_id):
            return True

        def mark_complete(self, item_id):
            pass

        def get_blocked_by(self, item_id):
            return []

    assert isinstance(MyGraph(), DependencyGraph)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def test_interface_protocol():
    class MyIface:
        async def start(self):
            pass

        async def stop(self):
            pass

    assert isinstance(MyIface(), Interface)


# ---------------------------------------------------------------------------
# CLIInterface
# ---------------------------------------------------------------------------

def test_cli_interface_protocol():
    class MyCLI:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def repl(self):
            pass

    assert isinstance(MyCLI(), CLIInterface)


# ---------------------------------------------------------------------------
# GatewayInterface
# ---------------------------------------------------------------------------

def test_gateway_interface_protocol():
    class MyGateway:
        async def start(self):
            pass

        async def stop(self):
            pass

        @property
        def platform(self):
            return "telegram"

        async def send(self, message, chat_id):
            pass

        def on_message(self, callback):
            pass

    assert isinstance(MyGateway(), GatewayInterface)


# ---------------------------------------------------------------------------
# APIInterface
# ---------------------------------------------------------------------------

def test_api_interface_protocol():
    class MyAPI:
        async def start(self):
            pass

        async def stop(self):
            pass

        @property
        def host(self):
            return "0.0.0.0"

        @property
        def port(self):
            return 8080

    assert isinstance(MyAPI(), APIInterface)


# ---------------------------------------------------------------------------
# Negative checks — plain objects should NOT match
# ---------------------------------------------------------------------------

def test_plain_object_not_protocol():
    class Empty:
        pass

    obj = Empty()
    assert not isinstance(obj, ToolRuntime)
    assert not isinstance(obj, AgentCore)
    assert not isinstance(obj, ClientManager)
    assert not isinstance(obj, ContextManager)
    assert not isinstance(obj, SessionStore)
    assert not isinstance(obj, CostLedger)
    assert not isinstance(obj, WorkSource)
    assert not isinstance(obj, WorkSink)
    assert not isinstance(obj, Scheduler)
    assert not isinstance(obj, DependencyGraph)
    assert not isinstance(obj, Interface)
    assert not isinstance(obj, CLIInterface)
    assert not isinstance(obj, GatewayInterface)
    assert not isinstance(obj, APIInterface)
