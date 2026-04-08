"""Interfaces — Layer 5 implementations.

User-facing surfaces that translate external input into WorkItems
and present WorkResults back: CLI REPL, HTTP API, chat-platform gateways.
"""

from phynai.interfaces.cli import PhynaiCLI
from phynai.interfaces.gateway import PhynaiGateway, SlackGateway

__all__ = [
    "PhynaiCLI",
    "PhynaiGateway",
    "SlackGateway",
]
