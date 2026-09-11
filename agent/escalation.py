"""Session store for human-in-the-loop escalations (Section 3.6).

When replay hits a state its ``escalation_policy`` routes to *escalate*
(not *retry*), it records the blocked step here and then polls this store
for a human's decision. The operator console (:mod:`agent.operator_console`)
reads the same store, shows the pending escalation with its context and
screenshot, and writes back ``status = "resumed"`` when the operator has
taken control of the live browser and wants replay to continue.

Two processes touch one SQLite file (replay + console), so every method
opens a short-lived connection and the schema is created in WAL mode.
The DB lives under ``evidence/sessions/`` and is git-ignored like the
rest of the raw run evidence -- it is operational state, not a
deliverable.

Statuses:

* ``pending``  -- replay is blocked, waiting for an operator.
* ``resumed``  -- an operator has intervened on the live session; replay
  may re-enter SETTLE/CHECK for the paused step (never ACT).
* ``timed_out`` -- ``human_handoff_timeout_seconds`` elapsed with no
  operator; replay has already hard-failed the run.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = "evidence/sessions/escalations.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    capability_id   TEXT NOT NULL,
    goal            TEXT,
    step_id         TEXT NOT NULL,
    step_ordinal    INTEGER,
    trigger         TEXT NOT NULL,
    phase           TEXT,
    expected        TEXT,
    observed        TEXT,
    screenshot_path TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    resumed_at      TEXT,
    resumed_by      TEXT,
    operator_note   TEXT,
    handoff_deadline_at TEXT
);
"""

_VALID_TRIGGERS = {
    "on_step_timeout",
    "on_hard_failure",
    "on_unrecognized_dialog",
    "on_checkpoint_failure",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # -- connection -------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    # -- writes (replay side) -----------------------------------

    def open_escalation(
        self,
        *,
        run_id: str,
        capability_id: str,
        step_id: str,
        trigger: str,
        goal: str | None = None,
        step_ordinal: int | None = None,
        phase: str | None = None,
        expected: str | None = None,
        observed: str | None = None,
        screenshot_path: str | None = None,
        handoff_deadline_at: str | None = None,
    ) -> int:
        if trigger not in _VALID_TRIGGERS:
            raise ValueError(
                f"unknown escalation trigger {trigger!r}; expected one of "
                f"{sorted(_VALID_TRIGGERS)}"
            )
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO escalations (
                    run_id, capability_id, goal, step_id, step_ordinal,
                    trigger, phase, expected, observed, screenshot_path,
                    status, created_at, handoff_deadline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (run_id, capability_id, goal, step_id, step_ordinal, trigger,
                 phase, expected, observed, screenshot_path, _now_iso(),
                 handoff_deadline_at),
            )
            assert cur.lastrowid is not None, "lastrowid is only None before an INSERT executes"
            return cur.lastrowid

    def mark_timed_out(self, escalation_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE escalations SET status='timed_out' "
                "WHERE id=? AND status='pending'",
                (escalation_id,),
            )

    # -- writes (operator side) --------------------------------

    def mark_resumed(
        self,
        escalation_id: int,
        *,
        resumed_by: str = "operator",
        operator_note: str | None = None,
    ) -> bool:
        """Record an operator's resume. Returns False if it was not still
        pending (already resumed, timed out, or gone)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE escalations SET status='resumed', resumed_at=?, "
                "resumed_by=?, operator_note=? WHERE id=? AND status='pending'",
                (_now_iso(), resumed_by, operator_note, escalation_id),
            )
            return cur.rowcount == 1

    # -- reads ------------------------------------------------

    def get(self, escalation_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM escalations WHERE id=?", (escalation_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_pending(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM escalations WHERE status='pending' "
                "ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM escalations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_for_run(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM escalations WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]
