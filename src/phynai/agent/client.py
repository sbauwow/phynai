"""PhynaiClientManager — LLM API client for OpenAI-compatible providers.

Implements the ``ClientManager`` protocol from ``phynai.contracts.agent``.
Uses ``httpx`` for async HTTP calls to ``/v1/chat/completions``.

NOTE: ``httpx`` must be listed as a project dependency.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api",
    "local": "http://localhost:11434",
}

_API_KEY_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


_REASONING_BUDGET_TOKENS: dict[str, int] = {
    "low": 1024,
    "medium": 4096,
    "high": 16384,
}


class PhynaiClientManager:
    """Manages LLM API connections and completions.

    Parameters
    ----------
    provider:
        Provider key (e.g. ``"openai"``, ``"local"``).
    model:
        Default model identifier.
    api_key:
        Bearer token.  Falls back to ``OPENAI_API_KEY`` env var.
    base_url:
        Override the provider base URL.
    reasoning:
        Extended thinking budget level: ``"none"``, ``"low"``, ``"medium"``,
        ``"high"``.  Only supported for Anthropic models.
    """

    def __init__(
        self,
        provider: str = "anthropic",
        model: str = "claude-opus-4-6",
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning: str | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._reasoning = reasoning

        # Resolve API key: explicit > PHYNAI_API_KEY > auth system > provider env var
        # If PHYNAI_API_KEY_SOURCE=env, read directly from the environment (set via .bashrc)
        if os.environ.get("PHYNAI_API_KEY_SOURCE") == "env":
            api_key = os.environ.get(_API_KEY_ENV_VARS.get(provider, "OPENAI_API_KEY"), "")

        resolved_key = api_key or os.environ.get("PHYNAI_API_KEY", "")
        if not resolved_key:
            try:
                from phynai.auth import resolve_api_key_provider_credentials, resolve_anthropic_token, PROVIDER_REGISTRY
                if provider == "anthropic":
                    resolved_key = resolve_anthropic_token() or ""
                elif provider in PROVIDER_REGISTRY:
                    creds = resolve_api_key_provider_credentials(provider)
                    resolved_key = creds.get("api_key", "")
                    if not base_url and creds.get("base_url"):
                        base_url = creds["base_url"]
            except Exception:
                pass
        if not resolved_key:
            env_var = _API_KEY_ENV_VARS.get(provider, "OPENAI_API_KEY")
            resolved_key = os.environ.get(env_var, "")

        self._api_key = resolved_key
        self._base_url = (
            base_url
            or _DEFAULT_BASE_URLS.get(provider, "https://api.openai.com")
        )

    # -- properties ---------------------------------------------------------

    @property
    def provider(self) -> str:
        """Return the configured provider key."""
        return self._provider

    @property
    def model(self) -> str:
        """Return the default model identifier."""
        return self._model

    # -- completions --------------------------------------------------------

    async def create_completion(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> AsyncIterator[Any] | dict[str, Any]:
        """Send a chat completion request to the provider.

        Parameters
        ----------
        messages:
            Conversation messages in OpenAI format.
        model:
            Override the default model for this call.
        tools:
            OpenAI function-calling tool schemas.
        stream:
            If ``True``, return an async iterator of SSE chunks.
            (Streaming support is a stub — currently raises.)

        Returns
        -------
        dict
            The raw JSON response from the provider.

        Raises
        ------
        RuntimeError
            On non-2xx HTTP status codes.
        """
        if stream:
            raise NotImplementedError("Streaming completions not yet supported")

        headers: dict[str, str] = {"Content-Type": "application/json"}

        if self._provider == "anthropic":
            if self._api_key:
                headers["x-api-key"] = self._api_key
                headers["anthropic-version"] = "2023-06-01"
            payload = self._build_anthropic_payload(
                model or self._model, messages, tools, self._reasoning,
            )
            url = f"{self._base_url.rstrip('/')}/v1/messages"
        else:
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            payload = {"model": model or self._model, "messages": messages}
            if tools:
                payload["tools"] = tools
            url = f"{self._base_url.rstrip('/')}/v1/chat/completions"

        logger.debug(
            "LLM request: %s model=%s messages=%d tools=%d",
            url, payload.get("model", "?"), len(messages),
            len(tools) if tools else 0,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    break
                except httpx.TimeoutException:
                    if attempt == 2:
                        raise
                    wait = (attempt + 1) * 2.0
                    logger.warning("LLM request timed out, retrying in %.0fs (attempt %d/3)", wait, attempt + 1)
                    import asyncio
                    await asyncio.sleep(wait)

        logger.debug(
            "LLM response: status=%d tokens=%s",
            resp.status_code,
            resp.json().get("usage", {}) if resp.status_code < 400 else "n/a",
        )

        if resp.status_code >= 400:
            raise RuntimeError(
                f"LLM request failed [{resp.status_code}]: {resp.text}"
            )

        raw = resp.json()
        if self._provider == "anthropic":
            return self._normalize_anthropic_response(raw)
        return raw  # type: ignore[return-value]

    # -- Anthropic format helpers -------------------------------------------

    @staticmethod
    def _build_anthropic_payload(
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        """Convert OpenAI-format messages + tools to Anthropic Messages API format."""
        # Extract system prompt (Anthropic takes it as top-level param)
        system_prompt = ""
        converted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                system_prompt = msg.get("content", "")
                continue
            if role == "tool":
                # OpenAI tool result → Anthropic tool_result content block
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }],
                })
                continue
            if role == "assistant" and msg.get("tool_calls"):
                # OpenAI tool_calls → Anthropic tool_use content blocks
                content: list[dict[str, Any]] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    try:
                        inp = json.loads(func.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        inp = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc.get("id", str(uuid.uuid4())),
                        "name": func.get("name", ""),
                        "input": inp,
                    })
                converted.append({"role": "assistant", "content": content})
                continue
            converted.append(msg)

        budget = _REASONING_BUDGET_TOKENS.get(reasoning or "", 0)
        if budget:
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": 16384,
                "thinking": {"type": "enabled", "budget_tokens": budget},
                "messages": converted,
            }
        else:
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": 8096,
                "messages": converted,
            }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
                if t.get("type") == "function"
            ]
        return payload

    @staticmethod
    def _normalize_anthropic_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Convert Anthropic Messages API response to OpenAI-compatible format."""
        content_blocks = raw.get("content", [])
        text = ""
        tool_calls = []

        for block in content_blocks:
            if block.get("type") == "thinking":
                # Extended thinking block — skip (not surfaced to user)
                continue
            elif block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", str(uuid.uuid4())),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        usage = raw.get("usage", {})
        return {
            "choices": [{"message": assistant_msg, "finish_reason": raw.get("stop_reason")}],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            },
            "model": raw.get("model", ""),
        }

    # -- model listing ------------------------------------------------------

    def list_models(self) -> list[str]:
        """Return available model identifiers (static for now)."""
        return [self._model]
