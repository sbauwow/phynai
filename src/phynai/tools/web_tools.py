"""Web tools — search the web and extract page content."""

from __future__ import annotations

import html.parser
import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import Any

from phynai.contracts.tools import Risk, ToolResult
from phynai.tools.decorator import tool

MAX_PAGE_CHARS = 5000

# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / cloud metadata
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
]


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Return (True, "") if safe to fetch, or (False, reason) if blocked."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' is not allowed (http/https only)"

    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"

    # Resolve hostname to IP — block if it resolves to a private/reserved range
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"

    for _, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return False, f"URL resolves to blocked address {ip} ({net})"

    return True, ""


class _TextExtractor(html.parser.HTMLParser):
    """Simple HTML-to-text extractor."""

    _skip_tags = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._text.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self._text)).strip()


def _html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except (ValueError, AssertionError, RecursionError):
        # Fallback: strip tags via regex (malformed HTML)
        return re.sub(r"<[^>]+>", " ", raw_html)[:MAX_PAGE_CHARS]
    return parser.get_text()


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

@tool(
    name="web_search",
    description="Search the web",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["web", "search"],
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results", "default": 5},
        },
        "required": ["query"],
    },
)
async def web_search_tool(arguments: dict[str, Any]) -> ToolResult:
    query = arguments.get("query", "")
    limit = min(20, max(1, arguments.get("limit", 5)))

    if not query:
        return ToolResult(
            tool_name="web_search", success=False, output="",
            error="No query provided", duration_ms=0.0,
        )

    try:
        import httpx  # noqa: E402
    except ImportError:
        return ToolResult(
            tool_name="web_search", success=False, output="",
            error="httpx is not installed — cannot perform web search",
            duration_ms=0.0,
        )

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "phynai-agent/0.1"},
            )
            resp.raise_for_status()

        # Parse DuckDuckGo HTML results
        body = resp.text
        results: list[str] = []
        # Each result lives in a <div class="result ..."> ... </div> block
        for match in re.finditer(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            body,
            re.DOTALL,
        ):
            if len(results) >= limit:
                break
            url = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
            results.append(f"[{len(results)+1}] {title}\n    {url}\n    {snippet}")

        if not results:
            return ToolResult(
                tool_name="web_search", success=True,
                output="No results found.", duration_ms=0.0,
            )

        return ToolResult(
            tool_name="web_search", success=True,
            output="\n\n".join(results), duration_ms=0.0,
        )
    except httpx.HTTPStatusError as exc:
        return ToolResult(
            tool_name="web_search", success=False, output="",
            error=f"HTTP {exc.response.status_code}", duration_ms=0.0,
        )
    except httpx.RequestError as exc:
        return ToolResult(
            tool_name="web_search", success=False, output="",
            error=f"Request failed: {type(exc).__name__}", duration_ms=0.0,
        )


# ---------------------------------------------------------------------------
# web_extract
# ---------------------------------------------------------------------------

@tool(
    name="web_extract",
    description="Extract content from web URLs",
    risk=Risk.LOW,
    mutates=False,
    capabilities=["web"],
    parameters={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "URLs to extract content from",
            },
        },
        "required": ["urls"],
    },
)
async def web_extract_tool(arguments: dict[str, Any]) -> ToolResult:
    urls: list[str] = arguments.get("urls", [])
    if not urls:
        return ToolResult(
            tool_name="web_extract", success=False, output="",
            error="No URLs provided", duration_ms=0.0,
        )

    try:
        import httpx  # noqa: E402
    except ImportError:
        return ToolResult(
            tool_name="web_extract", success=False, output="",
            error="httpx is not installed — cannot fetch URLs",
            duration_ms=0.0,
        )

    sections: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for url in urls[:10]:  # cap at 10 URLs
                safe, reason = _is_safe_url(url)
                if not safe:
                    sections.append(f"--- {url} ---\nBlocked: {reason}")
                    continue
                try:
                    resp = await client.get(
                        url,
                        headers={"User-Agent": "phynai-agent/0.1"},
                    )
                    resp.raise_for_status()
                    # Re-validate after redirect
                    if str(resp.url) != url:
                        safe, reason = _is_safe_url(str(resp.url))
                        if not safe:
                            sections.append(f"--- {url} ---\nBlocked after redirect: {reason}")
                            continue
                    text = _html_to_text(resp.text)[:MAX_PAGE_CHARS]
                    sections.append(f"--- {url} ---\n{text}")
                except httpx.HTTPStatusError as page_exc:
                    sections.append(f"--- {url} ---\nHTTP error: {page_exc.response.status_code}")
                except httpx.RequestError as page_exc:
                    sections.append(f"--- {url} ---\nRequest error: {type(page_exc).__name__}")

        return ToolResult(
            tool_name="web_extract", success=True,
            output="\n\n".join(sections), duration_ms=0.0,
        )
    except httpx.RequestError as exc:
        return ToolResult(
            tool_name="web_extract", success=False, output="",
            error=f"Request failed: {type(exc).__name__}", duration_ms=0.0,
        )
