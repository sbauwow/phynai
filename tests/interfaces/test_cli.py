"""Tests for phynai.interfaces.cli — PhynaiCLI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from phynai.contracts.work import Artifact, CostRecord, WorkResult, WorkStatus
from phynai.interfaces.cli import PhynaiCLI


def _mock_agent(response: str = "Hello from agent") -> MagicMock:
    agent = MagicMock()
    agent.run = AsyncMock(return_value=WorkResult(
        work_id="test-1",
        status=WorkStatus.completed,
        response=response,
    ))
    return agent


class TestPhynaiCLI:
    def test_instantiate_agent_only(self):
        cli = PhynaiCLI(agent=_mock_agent())
        assert cli._agent is not None
        assert cli._scheduler is None

    def test_instantiate_with_scheduler(self):
        scheduler = MagicMock()
        cli = PhynaiCLI(agent=_mock_agent(), scheduler=scheduler)
        assert cli._scheduler is scheduler

    def test_format_result_completed(self):
        result = WorkResult(work_id="1", status=WorkStatus.completed, response="OK")
        assert PhynaiCLI._format_result(result) == "OK"

    def test_format_result_failed(self):
        result = WorkResult(work_id="1", status=WorkStatus.failed, error="boom")
        assert "[failed] boom" in PhynaiCLI._format_result(result)

    def test_format_result_with_artifacts(self):
        result = WorkResult(
            work_id="1", status=WorkStatus.completed, response="done",
            artifacts=[Artifact(type="file", path="/tmp/x", description="output")],
        )
        formatted = PhynaiCLI._format_result(result)
        assert "output" in formatted
        assert "/tmp/x" in formatted
        assert "↳" in formatted

    def test_format_result_with_cost(self):
        result = WorkResult(
            work_id="1", status=WorkStatus.completed, response="hi",
            cost=CostRecord(input_tokens=100, output_tokens=50, estimated_cost_usd=0.01, model="gpt-4o"),
        )
        formatted = PhynaiCLI._format_result(result)
        assert "150" in formatted  # total tokens
        assert "gpt-4o" in formatted
        assert "0.0100" in formatted

    def test_print_banner(self, capsys):
        cli = PhynaiCLI(agent=_mock_agent())
        cli._print_banner()
        captured = capsys.readouterr()
        assert "agent" in captured.out.lower()
        assert "0.1.0" in captured.out

    def test_show_help(self, capsys):
        PhynaiCLI._show_help()
        captured = capsys.readouterr()
        assert "/help" in captured.out
        assert "/quit" in captured.out
        assert "/clear" in captured.out

    def test_handle_command_quit(self):
        cli = PhynaiCLI(agent=_mock_agent())
        cli._running = True
        assert cli._handle_command("/quit") is True
        assert cli._running is False

    def test_handle_command_unknown(self):
        cli = PhynaiCLI(agent=_mock_agent())
        assert cli._handle_command("/nonexistent") is False
