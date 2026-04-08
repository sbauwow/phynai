"""PhynaiSessionStore — file-based JSON session persistence.

Implements the ``SessionStore`` protocol from ``phynai.contracts.agent``.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PhynaiSessionStore:
    """Persists and retrieves conversation sessions as JSON files.

    Parameters
    ----------
    base_path:
        Directory where session JSON files are stored.
        Tilde (``~``) is expanded automatically.
    """

    def __init__(self, base_path: str = "~/.phynai/sessions") -> None:
        self._base = Path(base_path).expanduser()
        self._base.mkdir(parents=True, exist_ok=True)

    # -- helpers ------------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        """Return the file path for a given session ID."""
        return self._base / f"{session_id}.json"

    # -- persistence --------------------------------------------------------

    async def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        """Persist a session's messages and metadata to disk."""
        data = {
            "session_id": session_id,
            "messages": messages,
            "metadata": metadata,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._session_path(session_id)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — owner read/write only
        except OSError:
            pass

    async def load(
        self, session_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        """Load a session. Returns ``None`` if not found."""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["messages"], data.get("metadata", {})

    async def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions, newest first."""
        files = sorted(
            self._base.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append(
                    {
                        "session_id": data.get("session_id", f.stem),
                        "saved_at": data.get("saved_at"),
                        "message_count": len(data.get("messages", [])),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return results

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search sessions by substring match across message content."""
        query_lower = query.lower()
        matches: list[dict[str, Any]] = []
        for f in self._base.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                text = json.dumps(data.get("messages", []))
                if query_lower in text.lower():
                    matches.append(
                        {
                            "session_id": data.get("session_id", f.stem),
                            "saved_at": data.get("saved_at"),
                            "message_count": len(data.get("messages", [])),
                        }
                    )
            except (json.JSONDecodeError, KeyError):
                continue
        return matches
