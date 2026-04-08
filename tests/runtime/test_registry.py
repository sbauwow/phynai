"""Tests for ToolRegistry."""

import pytest

from phynai.contracts.tools import Risk, ToolCall, ToolMetadata, ToolResult
from phynai.runtime.registry import ToolRegistry


def _make_metadata(name: str = "echo") -> ToolMetadata:
    return ToolMetadata(
        name=name,
        description=f"A {name} tool",
        risk=Risk.LOW,
        mutates=False,
        capabilities=["test"],
        requires_confirmation=False,
    )


def _echo_handler(arguments: dict) -> ToolResult:
    return ToolResult(
        tool_name="echo",
        call_id="test-call",
        success=True,
        output=str(arguments),
        duration_ms=0.0,
    )


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


class TestRegisterAndRetrieve:
    def test_register_and_get(self, registry: ToolRegistry):
        meta = _make_metadata("echo")
        registry.register("echo", _echo_handler, meta)
        entry = registry.get("echo")
        assert entry is not None
        handler, metadata = entry
        assert handler is _echo_handler
        assert metadata.name == "echo"

    def test_get_missing_returns_none(self, registry: ToolRegistry):
        assert registry.get("nonexistent") is None


class TestRegisterDuplicate:
    def test_duplicate_raises_value_error(self, registry: ToolRegistry):
        meta = _make_metadata("echo")
        registry.register("echo", _echo_handler, meta)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("echo", _echo_handler, meta)


class TestUnregister:
    def test_unregister_removes_tool(self, registry: ToolRegistry):
        meta = _make_metadata("echo")
        registry.register("echo", _echo_handler, meta)
        registry.unregister("echo")
        assert registry.get("echo") is None
        assert not registry.has("echo")

    def test_unregister_missing_raises_key_error(self, registry: ToolRegistry):
        with pytest.raises(KeyError, match="not registered"):
            registry.unregister("nonexistent")


class TestListTools:
    def test_list_tools_returns_all_metadata(self, registry: ToolRegistry):
        for name in ["a", "b", "c"]:
            registry.register(name, _echo_handler, _make_metadata(name))
        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert names == {"a", "b", "c"}


class TestGetMetadata:
    def test_existing(self, registry: ToolRegistry):
        meta = _make_metadata("echo")
        registry.register("echo", _echo_handler, meta)
        assert registry.get_metadata("echo") == meta

    def test_missing(self, registry: ToolRegistry):
        assert registry.get_metadata("missing") is None


class TestHas:
    def test_has_true(self, registry: ToolRegistry):
        registry.register("echo", _echo_handler, _make_metadata("echo"))
        assert registry.has("echo") is True

    def test_has_false(self, registry: ToolRegistry):
        assert registry.has("nope") is False


class TestClear:
    def test_clear_empties_registry(self, registry: ToolRegistry):
        for name in ["a", "b"]:
            registry.register(name, _echo_handler, _make_metadata(name))
        assert len(registry) == 2
        registry.clear()
        assert len(registry) == 0
        assert registry.list_tools() == []


class TestLen:
    def test_len_empty(self, registry: ToolRegistry):
        assert len(registry) == 0

    def test_len_after_register(self, registry: ToolRegistry):
        registry.register("a", _echo_handler, _make_metadata("a"))
        registry.register("b", _echo_handler, _make_metadata("b"))
        assert len(registry) == 2
