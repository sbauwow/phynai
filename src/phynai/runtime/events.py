"""Event bus and execution journal for runtime observability."""

from __future__ import annotations

import json
import sqlite3
import stat
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from phynai.contracts.events import Event, EventType

_DEFAULT_DB = Path("~/.phynai/journal.db").expanduser()


class EventBus:
    """Simple synchronous publish-subscribe event bus.

    Listeners are registered per EventType and invoked synchronously
    in registration order when an event of that type is emitted.
    """

    def __init__(self) -> None:
        self._listeners: dict[EventType, list[Callable[[Event], Any]]] = defaultdict(list)

    def on(self, event_type: EventType, callback: Callable[[Event], Any]) -> None:
        """Register a listener for the given event type."""
        self._listeners[event_type].append(callback)

    def off(self, event_type: EventType, callback: Callable[[Event], Any]) -> None:
        """Remove a previously registered listener.

        Raises:
            ValueError: If the callback was not registered for that event type.
        """
        try:
            self._listeners[event_type].remove(callback)
        except ValueError:
            raise ValueError(
                f"Callback not registered for event type {event_type.value}"
            )

    def emit(self, event: Event) -> None:
        """Emit an event, invoking all registered listeners synchronously."""
        for callback in self._listeners.get(event.event_type, []):
            callback(event)

    def clear(self) -> None:
        """Remove all registered listeners."""
        self._listeners.clear()

    def __repr__(self) -> str:
        counts = {k.value: len(v) for k, v in self._listeners.items() if v}
        return f"EventBus(listeners={counts})"


class ExecutionJournal:
    """Append-only journal that persists runtime events to SQLite.

    Every call to ``record()`` is immediately written to disk so the audit
    trail survives process crashes.  An in-memory list mirrors the DB for
    fast in-process queries.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Defaults to ``~/.phynai/journal.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _DEFAULT_DB).expanduser() if db_path else _DEFAULT_DB
        self._events: list[Event] = []
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Create the database and events table if they don't exist."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id  TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source    TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload   TEXT NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp  ON events(timestamp)")
            self._conn.commit()
            try:
                self._db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                pass
        except sqlite3.Error:
            # DB unavailable — fall back to in-memory-only mode silently
            self._conn = None

    def record(self, event: Event) -> None:
        """Append an event to the journal (in-memory + SQLite)."""
        self._events.append(event)
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO events (trace_id, event_type, source, timestamp, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event.trace_id,
                    event.event_type.value,
                    event.source,
                    event.timestamp.isoformat(),
                    json.dumps(event.payload, default=str),
                ),
            )
            self._conn.commit()
        except sqlite3.Error:
            pass  # in-memory list is the fallback

    def query(
        self,
        event_type: EventType | None = None,
        tool_name: str | None = None,
        since: datetime | None = None,
    ) -> list[Event]:
        """Return events matching the given filters (in-memory scan)."""
        results: list[Event] = []
        for event in self._events:
            if event_type is not None and event.event_type != event_type:
                continue
            if tool_name is not None:
                if event.payload.get("tool_name", "") != tool_name:
                    continue
            if since is not None and event.timestamp < since:
                continue
            results.append(event)
        return results

    def count(self) -> int:
        """Return total number of recorded events."""
        return len(self._events)

    def clear(self) -> None:
        """Remove all in-memory events (does NOT purge the SQLite DB)."""
        self._events.clear()

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize all in-memory events to a list of dicts."""
        return [event.model_dump() for event in self._events]

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __repr__(self) -> str:
        return f"ExecutionJournal(events={len(self._events)}, db={self._db_path})"
