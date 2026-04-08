"""SharedMemory — namespaced key-value store for inter-agent communication."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    value: Any
    agent: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class SharedMemory:
    """Thread-safe namespaced memory shared between agents in a team.

    Keys are namespaced as ``<agent_name>/<key>``. Any agent can read
    any namespace, but writes are scoped to the writing agent's namespace.
    All operations are guarded by a lock for safe concurrent access.
    """

    def __init__(self) -> None:
        self._store: dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()

    def write(self, agent: str, key: str, value: Any, **metadata: Any) -> None:
        """Write a value into the agent's namespace."""
        ns_key = f"{agent}/{key}"
        with self._lock:
            self._store[ns_key] = MemoryEntry(
                value=value, agent=agent, metadata=metadata,
            )

    def read(self, key: str) -> Any | None:
        """Read a value by full namespaced key (e.g. 'researcher/findings')."""
        with self._lock:
            entry = self._store.get(key)
            return entry.value if entry else None

    def read_agent(self, agent: str) -> dict[str, Any]:
        """Return all entries written by a specific agent."""
        prefix = f"{agent}/"
        with self._lock:
            return {
                k[len(prefix):]: e.value
                for k, e in self._store.items()
                if k.startswith(prefix)
            }

    def summary(self) -> str:
        """Render a markdown summary of all memory for prompt injection."""
        with self._lock:
            return self._render_summary()

    def _render_summary(self) -> str:
        """Internal summary render — caller must hold the lock."""
        if not self._store:
            return ""

        by_agent: dict[str, list[tuple[str, Any]]] = {}
        for key, entry in self._store.items():
            parts = key.split("/", 1)
            agent = parts[0]
            short_key = parts[1] if len(parts) > 1 else key
            by_agent.setdefault(agent, []).append((short_key, entry.value))

        lines = ["## Shared Team Memory\n"]
        for agent, entries in by_agent.items():
            lines.append(f"### {agent}")
            for k, v in entries:
                text = str(v)[:200]
                lines.append(f"- **{k}**: {text}")
            lines.append("")
        return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
