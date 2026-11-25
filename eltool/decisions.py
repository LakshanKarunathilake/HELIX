"""The decision log: one append-only row per server-side write to a user's layer.

Rows are only ever inserted. There is deliberately no update or delete here,
so the log cannot be rewritten through this module or the API.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from eltool import userdb


@dataclass
class Decision:
    id: int
    ts: str
    session_name: str
    row_idx: int
    user_id: int
    actor: str
    action: str
    kb_values: dict
    previous: dict
    llm_used: str | None
    method: str
    elapsed_ms: int | None
    selected_rank: int | None
    revision: int | None


def _decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"], ts=row["ts"], session_name=row["session_name"], row_idx=row["row_idx"],
        user_id=row["user_id"], actor=row["actor"], action=row["action"],
        kb_values=json.loads(row["kb_values"] or "{}"),
        previous=json.loads(row["previous"] or "{}"),
        llm_used=row["llm_used"], method=row["method"] or "",
        elapsed_ms=row["elapsed_ms"], selected_rank=row["selected_rank"],
        revision=row["revision"])


class _Transaction:
    """Inserts decisions on one open SQLite transaction (see ``transaction``)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, session_name: str, row_idx: int, user_id: int, actor: str, action: str,
            kb_values: dict, previous: dict, *, llm_used: str | None = None,
            method: str = "", elapsed_ms: int | None = None,
            selected_rank: int | None = None, revision: int | None = None) -> None:
        self._conn.execute(
            "INSERT INTO decisions (ts, session_name, row_idx, user_id, actor, action, "
            "kb_values, previous, llm_used, method, elapsed_ms, selected_rank, revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="milliseconds"), session_name,
             int(row_idx), user_id, actor, action, json.dumps(kb_values), json.dumps(previous),
             llm_used, method, elapsed_ms, selected_rank, revision))


@contextmanager
def transaction() -> Iterator[_Transaction]:
    """Log decisions atomically with the write they describe.

    ``add`` inserts immediately; everything commits when the block exits
    cleanly and rolls back if anything in it raises, so a caller doing
    ``tx.add(...)`` then the CSV write keeps the log and the layer in step.
    """
    conn = userdb.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with conn:   # commit on clean exit, rollback on exception
            yield _Transaction(conn)
    finally:
        conn.close()


def has_decisions(session_name: str) -> bool:
    conn = userdb.connect()
    try:
        return conn.execute("SELECT 1 FROM decisions WHERE session_name = ? LIMIT 1",
                            (session_name,)).fetchone() is not None
    finally:
        conn.close()


def list_decisions(session_name: str | None = None,
                   user_id: int | None = None) -> list[Decision]:
    """Every logged decision, oldest first, optionally for one session and/or user."""
    where, args = [], []
    if session_name is not None:
        where.append("session_name = ?")
        args.append(session_name)
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    sql = "SELECT * FROM decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    conn = userdb.connect()
    try:
        return [_decision(r) for r in conn.execute(sql + " ORDER BY id", args)]
    finally:
        conn.close()


def latest_human_or_script(session_name: str) -> dict[tuple[int, int], Decision]:
    """The latest non-feedback decision per (user_id, row_idx) in a session."""
    latest: dict[tuple[int, int], Decision] = {}
    for d in list_decisions(session_name):
        if d.action != "feedback":
            latest[(d.user_id, d.row_idx)] = d      # ordered by id: later wins
    return latest
