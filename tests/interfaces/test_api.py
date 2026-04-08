"""Tests for phynai.interfaces.api — PhynaiAPI."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from phynai.contracts.work import WorkResult, WorkStatus
from phynai.interfaces.api import PhynaiAPI


def _mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.run = AsyncMock(return_value=WorkResult(
        work_id="test-1",
        status=WorkStatus.completed,
        response="api response",
    ))
    return agent


class TestPhynaiAPI:
    def test_instantiate(self):
        api = PhynaiAPI(agent=_mock_agent())
        assert api.host == "0.0.0.0"
        assert api.port == 8080

    def test_instantiate_custom(self):
        api = PhynaiAPI(agent=_mock_agent(), host="127.0.0.1", port=9090)
        assert api.host == "127.0.0.1"
        assert api.port == 9090

    def test_parse_request_get(self):
        raw = b"GET /v1/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        method, path, body = PhynaiAPI._parse_request(raw)
        assert method == "GET"
        assert path == "/v1/health"
        assert body == {}

    def test_parse_request_post(self):
        payload = json.dumps({"prompt": "hello"})
        raw = (
            f"POST /v1/run HTTP/1.1\r\nContent-Type: application/json\r\n\r\n{payload}"
        ).encode()
        method, path, body = PhynaiAPI._parse_request(raw)
        assert method == "POST"
        assert path == "/v1/run"
        assert body["prompt"] == "hello"

    def test_parse_request_strips_query(self):
        raw = b"GET /v1/health?foo=bar HTTP/1.1\r\n\r\n"
        _, path, _ = PhynaiAPI._parse_request(raw)
        assert path == "/v1/health"

    @pytest.mark.asyncio
    async def test_send_response(self):
        writer = MagicMock()
        writer.drain = AsyncMock()
        await PhynaiAPI._send_response(writer, 200, {"status": "ok"})
        writer.write.assert_called_once()
        data = writer.write.call_args[0][0]
        assert b"HTTP/1.1 200 OK" in data
        assert b'"status": "ok"' in data
        assert b"Content-Type: application/json" in data

    @pytest.mark.asyncio
    async def test_send_response_404(self):
        writer = MagicMock()
        writer.drain = AsyncMock()
        await PhynaiAPI._send_response(writer, 404, {"error": "not found"})
        data = writer.write.call_args[0][0]
        assert b"404 Not Found" in data
