"""Tests for PhynaiSessionStore — file-based JSON session persistence."""

import pytest

from phynai.agent.session import PhynaiSessionStore


@pytest.mark.asyncio
async def test_save_and_load_roundtrip(tmp_path):
    """Saving a session and loading it back returns the same data."""
    store = PhynaiSessionStore(base_path=str(tmp_path))
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    metadata = {"source": "test", "work_id": "w1"}

    await store.save("sess-1", messages, metadata)
    result = await store.load("sess-1")

    assert result is not None
    loaded_msgs, loaded_meta = result
    assert loaded_msgs == messages
    assert loaded_meta["source"] == "test"
    assert loaded_meta["work_id"] == "w1"


@pytest.mark.asyncio
async def test_load_nonexistent_returns_none(tmp_path):
    """Loading a non-existent session returns None."""
    store = PhynaiSessionStore(base_path=str(tmp_path))
    result = await store.load("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_list_sessions_returns_saved(tmp_path):
    """list_sessions returns entries for all saved sessions."""
    store = PhynaiSessionStore(base_path=str(tmp_path))

    await store.save("s1", [{"role": "user", "content": "a"}], {})
    await store.save("s2", [{"role": "user", "content": "b"}], {})

    sessions = await store.list_sessions()
    session_ids = {s["session_id"] for s in sessions}
    assert "s1" in session_ids
    assert "s2" in session_ids
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_list_sessions_respects_limit(tmp_path):
    """list_sessions returns at most `limit` results."""
    store = PhynaiSessionStore(base_path=str(tmp_path))

    for i in range(5):
        await store.save(f"s{i}", [{"role": "user", "content": f"msg{i}"}], {})

    sessions = await store.list_sessions(limit=2)
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_list_sessions_has_message_count(tmp_path):
    """Each entry in list_sessions includes accurate message_count."""
    store = PhynaiSessionStore(base_path=str(tmp_path))
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    await store.save("s1", msgs, {})

    sessions = await store.list_sessions()
    assert sessions[0]["message_count"] == 2


@pytest.mark.asyncio
async def test_search_finds_matching_session(tmp_path):
    """search returns sessions whose messages contain the query string."""
    store = PhynaiSessionStore(base_path=str(tmp_path))

    await store.save("s1", [{"role": "user", "content": "deploy the app"}], {})
    await store.save("s2", [{"role": "user", "content": "run tests"}], {})

    results = await store.search("deploy")
    assert len(results) == 1
    assert results[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_search_returns_empty_for_no_match(tmp_path):
    """search returns empty list when no session matches."""
    store = PhynaiSessionStore(base_path=str(tmp_path))
    await store.save("s1", [{"role": "user", "content": "hello world"}], {})

    results = await store.search("nonexistent_query_xyz")
    assert results == []


@pytest.mark.asyncio
async def test_search_is_case_insensitive(tmp_path):
    """search is case-insensitive."""
    store = PhynaiSessionStore(base_path=str(tmp_path))
    await store.save("s1", [{"role": "user", "content": "Deploy The App"}], {})

    results = await store.search("deploy the app")
    assert len(results) == 1
