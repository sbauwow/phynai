"""Tests for PhynaiContextManager — prompt building, compression, memory."""

from phynai.agent.context import PhynaiContextManager
from phynai.contracts.work import WorkItem, WorkConstraints


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_empty_context():
    """Empty work-item context produces just the base prompt."""
    cm = PhynaiContextManager(system_prompt="You are a helpful assistant.")
    wi = WorkItem(prompt="hello")
    result = cm.build_system_prompt(wi)
    assert result == "You are a helpful assistant."


def test_build_system_prompt_no_base_prompt():
    """No base prompt and no context produces empty string."""
    cm = PhynaiContextManager(system_prompt="")
    wi = WorkItem(prompt="hello")
    result = cm.build_system_prompt(wi)
    assert result == ""


def test_build_system_prompt_includes_work_item_context():
    """Work-item context dict should appear as JSON under '## Additional Context'."""
    cm = PhynaiContextManager(system_prompt="Base prompt.")
    wi = WorkItem(prompt="do stuff", context={"repo": "phynai", "branch": "main"})
    result = cm.build_system_prompt(wi)
    assert "## Additional Context" in result
    assert '"repo"' in result
    assert '"phynai"' in result
    assert "Base prompt." in result


def test_build_system_prompt_includes_allowed_tools():
    """allowed_tools constraint should appear as '## Allowed Tools' section."""
    cm = PhynaiContextManager(system_prompt="Base.")
    constraints = WorkConstraints(allowed_tools=["read_file", "write_file"])
    wi = WorkItem(prompt="do stuff", constraints=constraints)
    result = cm.build_system_prompt(wi)
    assert "## Allowed Tools" in result
    assert "read_file" in result
    assert "write_file" in result
    assert "You may ONLY use:" in result


def test_build_system_prompt_no_allowed_tools_section_when_none():
    """When allowed_tools is None, no '## Allowed Tools' section should appear."""
    cm = PhynaiContextManager(system_prompt="Base.")
    wi = WorkItem(prompt="hello", constraints=WorkConstraints(allowed_tools=None))
    result = cm.build_system_prompt(wi)
    assert "## Allowed Tools" not in result


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------

def test_compress_keeps_system_message():
    """System message (first) is always preserved after compression."""
    cm = PhynaiContextManager()
    system = {"role": "system", "content": "You are helpful."}
    msgs = [system] + [
        {"role": "user", "content": "x" * 2000}
        for _ in range(20)
    ]
    result = cm.compress(msgs, target_tokens=200)
    assert result[0] == system


def test_compress_returns_all_when_under_target():
    """When total tokens are under target, all messages returned as-is."""
    cm = PhynaiContextManager()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    result = cm.compress(msgs, target_tokens=100_000)
    assert result == msgs


def test_compress_truncates_when_over_target():
    """When over target, result has fewer messages than input."""
    cm = PhynaiContextManager()
    system = {"role": "system", "content": "sys"}
    msgs = [system] + [
        {"role": "user", "content": "message " * 100}
        for _ in range(50)
    ]
    result = cm.compress(msgs, target_tokens=100)
    assert len(result) < len(msgs)
    assert result[0] == system


def test_compress_keeps_most_recent_messages():
    """The most recent messages should be kept, older ones dropped."""
    cm = PhynaiContextManager()
    system = {"role": "system", "content": "s"}
    old_msg = {"role": "user", "content": "old " * 500}
    recent_msg = {"role": "user", "content": "recent"}
    msgs = [system, old_msg, recent_msg]
    # Set target low enough to force dropping old_msg but keeping recent_msg
    result = cm.compress(msgs, target_tokens=30)
    # System always kept; recent should be kept over old
    assert result[0] == system
    contents = [m["content"] for m in result]
    assert "recent" in contents


def test_compress_empty_messages():
    """Empty message list returns empty list."""
    cm = PhynaiContextManager()
    assert cm.compress([], target_tokens=100) == []


# ---------------------------------------------------------------------------
# inject_memory
# ---------------------------------------------------------------------------

def test_inject_memory_returns_messages_unchanged():
    """inject_memory is a no-op — returns input unchanged."""
    cm = PhynaiContextManager()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    result = cm.inject_memory(msgs)
    assert result == msgs
    assert result is msgs  # same object, not a copy
