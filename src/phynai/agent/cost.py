"""PhynaiCostLedger — persistent cost tracking.

Implements the ``CostLedger`` protocol from ``phynai.contracts.agent``.
Records are persisted to SQLite on every ``record()`` call and kept
in-memory for fast aggregation. Falls back to in-memory only if the
database is unavailable.
"""

from __future__ import annotations

import sqlite3
import stat
from collections import defaultdict
from pathlib import Path
from typing import Optional

from phynai.contracts.work import CostRecord

_DEFAULT_DB = Path("~/.phynai/cost_ledger.db").expanduser()


class PhynaiCostLedger:
    """Accumulates, persists, and queries cost records.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.  Defaults to ``~/.phynai/cost_ledger.db``.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._records: dict[str, list[CostRecord]] = defaultdict(list)
        self._db_path = (db_path or _DEFAULT_DB).expanduser() if db_path else _DEFAULT_DB
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cost_records (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_id     TEXT NOT NULL,
                    model       TEXT,
                    provider    TEXT,
                    input_tokens    INTEGER DEFAULT 0,
                    output_tokens   INTEGER DEFAULT 0,
                    cache_read_tokens  INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0.0,
                    recorded_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cost_work ON cost_records(work_id)"
            )
            self._conn.commit()
            try:
                self._db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        except sqlite3.Error:
            self._conn = None

    # -- recording ----------------------------------------------------------

    def record(self, work_id: str, cost: CostRecord) -> None:
        """Record a cost entry for a work item."""
        self._records[work_id].append(cost)
        if self._conn is not None:
            try:
                self._conn.execute(
                    """
                    INSERT INTO cost_records
                        (work_id, model, provider, input_tokens, output_tokens,
                         cache_read_tokens, cache_write_tokens, estimated_cost_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work_id,
                        cost.model,
                        cost.provider,
                        cost.input_tokens,
                        cost.output_tokens,
                        cost.cache_read_tokens,
                        cost.cache_write_tokens,
                        cost.estimated_cost_usd,
                    ),
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    # -- aggregation --------------------------------------------------------

    @staticmethod
    def _sum(records: list[CostRecord]) -> CostRecord:
        """Sum a list of CostRecords into a single aggregate."""
        total = CostRecord()
        for r in records:
            total.input_tokens += r.input_tokens
            total.output_tokens += r.output_tokens
            total.cache_read_tokens += r.cache_read_tokens
            total.cache_write_tokens += r.cache_write_tokens
            total.estimated_cost_usd += r.estimated_cost_usd
        if records:
            total.model = records[-1].model
            total.provider = records[-1].provider
        return total

    def total(self, session_id: str | None = None) -> CostRecord:
        """Aggregate cost across all work items (or filtered by *session_id*)."""
        if session_id is not None:
            return self._sum(self._records.get(session_id, []))
        all_records = [r for recs in self._records.values() for r in recs]
        return self._sum(all_records)

    def by_model(self) -> dict[str, CostRecord]:
        """Return aggregated costs keyed by model name."""
        by: dict[str, list[CostRecord]] = defaultdict(list)
        for recs in self._records.values():
            for r in recs:
                by[r.model].append(r)
        return {model: self._sum(recs) for model, recs in by.items()}

    def lifetime_total(self) -> CostRecord:
        """Aggregate all costs ever recorded (from SQLite, not just this session)."""
        if self._conn is None:
            return self.total()
        try:
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(output_tokens), 0),
                       COALESCE(SUM(cache_read_tokens), 0),
                       COALESCE(SUM(cache_write_tokens), 0),
                       COALESCE(SUM(estimated_cost_usd), 0.0)
                FROM cost_records
                """
            ).fetchone()
            return CostRecord(
                input_tokens=row[0],
                output_tokens=row[1],
                cache_read_tokens=row[2],
                cache_write_tokens=row[3],
                estimated_cost_usd=row[4],
            )
        except sqlite3.Error:
            return self.total()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
