"""Tests for phynai.interfaces.gateway — TelegramGateway."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from phynai.contracts.work import WorkResult, WorkStatus
from phynai.interfaces.gateway import TelegramGateway


def _mock_agent(response: str = "gateway reply") -> MagicMock:
    agent = MagicMock()
    agent.run = AsyncMock(return_value=WorkResult(
        work_id="gw-1",
        status=WorkStatus.completed,
        response=response,
    ))
    return agent


class TestTelegramGateway:
    def test_instantiate(self):
        gw = TelegramGateway(agent=_mock_agent(), token="fake-token")
        assert gw.platform == "telegram"
        assert gw._token == "fake-token"

    def test_instantiate_no_token(self):
        gw = TelegramGateway(agent=_mock_agent())
        assert gw._token == ""

    def test_on_message_registers_callback(self):
        gw = TelegramGateway(agent=_mock_agent(), token="t")
        assert gw._on_message_callback is None
        cb = MagicMock()
        gw.on_message(cb)
        assert gw._on_message_callback is cb

    @pytest.mark.asyncio
    async def test_handle_incoming_creates_work_and_runs(self):
        agent = _mock_agent("test reply")
        gw = TelegramGateway(agent=agent, token="t")
        gw.send = AsyncMock()

        await gw._handle_incoming("hello bot", "chat123")

        agent.run.assert_awaited_once()
        work = agent.run.call_args[0][0]
        assert work.prompt == "hello bot"
        assert work.source == "telegram"
        assert work.metadata["chat_id"] == "chat123"
        gw.send.assert_awaited_once_with("test reply", "chat123")

    @pytest.mark.asyncio
    async def test_handle_incoming_with_callback(self):
        agent = _mock_agent("cb reply")
        gw = TelegramGateway(agent=agent, token="t")
        gw.send = AsyncMock()
        cb = MagicMock()
        gw.on_message(cb)

        await gw._handle_incoming("hi", "c1")

        cb.assert_called_once_with("hi", "c1", "cb reply")

    @pytest.mark.asyncio
    async def test_handle_incoming_failed_result(self):
        agent = MagicMock()
        agent.run = AsyncMock(return_value=WorkResult(
            work_id="f1", status=WorkStatus.failed, error="oops",
        ))
        gw = TelegramGateway(agent=agent, token="t")
        gw.send = AsyncMock()

        await gw._handle_incoming("fail", "c2")

        sent_text = gw.send.call_args[0][0]
        assert "Error: oops" in sent_text
