"""Tests for phynai.cli_main — CLI entry point."""

from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pytest

from phynai.cli_main import main, _build_parser, _build_agent


class TestBuildParser:
    def test_subcommands_exist(self):
        parser = _build_parser()
        # Parse each subcommand to verify they're registered
        args = parser.parse_args(["run", "hello"])
        assert args.command == "run"
        assert args.prompt == "hello"

    def test_chat_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["chat"])
        assert args.command == "chat"

    def test_serve_subcommand_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.host == "0.0.0.0"
        assert args.port == 8080

    def test_serve_custom_port(self):
        parser = _build_parser()
        args = parser.parse_args(["serve", "--port", "3000", "--host", "127.0.0.1"])
        assert args.port == 3000
        assert args.host == "127.0.0.1"

    def test_gateway_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["gateway", "telegram"])
        assert args.command == "gateway"
        assert args.platform == "telegram"

    def test_version_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_verbose_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["-v", "version"])
        assert args.verbose is True


class TestMain:
    def test_main_is_callable(self):
        assert callable(main)

    def test_build_agent_is_callable(self):
        assert callable(_build_agent)
