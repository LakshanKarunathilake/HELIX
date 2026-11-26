"""The decision log: one append-only row per server-side write to a user's layer.

Rows (and reviewers' adjudications) are only ever inserted. There is deliberately no update or delete here,
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


def has_adjudications(session_name: str) -> bool:
    conn = userdb.connect()
    try:
        return conn.execute("SELECT 1 FROM adjudications WHERE session_name = ? LIMIT 1",
                            (session_name,)).fetchone() is not None
    finally:
        conn.close()


def _select_decisions(conn: sqlite3.Connection, session_name: str | None = None,
                      user_id: int | None = None) -> list[Decision]:
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
    return [_decision(r) for r in conn.execute(sql + " ORDER BY id", args)]


def list_decisions(session_name: str | None = None,
                   user_id: int | None = None) -> list[Decision]:
    """Every logged decision, oldest first, optionally for one session and/or user."""
    conn = userdb.connect()
    try:
        return _select_decisions(conn, session_name, user_id)
    finally:
        conn.close()


def _latest(oldest_first: list[Decision]) -> dict[tuple[int, int], Decision]:
    latest: dict[tuple[int, int], Decision] = {}
    for d in oldest_first:
        if d.action != "feedback":
            latest[(d.user_id, d.row_idx)] = d      # ordered by id: later wins
    return latest


def latest_human_or_script(session_name: str) -> dict[tuple[int, int], Decision]:
    """The latest non-feedback decision per (user_id, row_idx) in a session."""
    return _latest(list_decisions(session_name))


def _adjudication(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "ts": row["ts"], "row_idx": row["row_idx"],
            "reviewer_id": row["reviewer_id"],
            "kb_values": json.loads(row["kb_values"] or "{}"), "note": row["note"] or "",
            # NULL on decisions saved before these were recorded
            "basis": row["basis"] or "legacy",
            "source_users": json.loads(row["source_users"] or "[]"),
            "based_on": None if row["based_on"] is None else json.loads(row["based_on"])}


def _insert_adjudication(conn: sqlite3.Connection, session_name: str, row_idx: int,
                         reviewer_id: int, kb_values: dict, note: str, *,
                         basis: str | None, source_users: list[int] | None,
                         based_on: dict | None) -> dict:
    cur = conn.execute(
        "INSERT INTO adjudications (ts, session_name, row_idx, reviewer_id, "
        "kb_values, note, basis, source_users, based_on) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
         session_name, int(row_idx), reviewer_id, json.dumps(kb_values), note, basis,
         None if source_users is None else json.dumps(source_users),
         None if based_on is None else json.dumps(based_on)))
    return _adjudication(conn.execute("SELECT * FROM adjudications WHERE id = ?",
                                      (cur.lastrowid,)).fetchone())


def _current_adjudications(conn: sqlite3.Connection, session_name: str) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM adjudications WHERE session_name = ? ORDER BY id",
                        (session_name,))
    return {r["row_idx"]: _adjudication(r) for r in rows}   # ordered by id: later wins


def add_adjudication(session_name: str, row_idx: int, reviewer_id: int,
                     kb_values: dict, note: str = "", *, basis: str | None = None,
                     source_users: list[int] | None = None,
                     based_on: dict | None = None) -> dict:
    """Append a reviewer's final values for one row. Adjudications are append-only
    like decisions: posting again for the row supersedes, never overwrites."""
    conn = userdb.connect()
    try:
        with conn:
            return _insert_adjudication(
                conn, session_name, row_idx, reviewer_id, kb_values, note,
                basis=basis, source_users=source_users, based_on=based_on)
    finally:
        conn.close()


def current_adjudications(session_name: str) -> dict[int, dict]:
    """The latest adjudication per row of a session, keyed by row index."""
    conn = userdb.connect()
    try:
        return _current_adjudications(conn, session_name)
    finally:
        conn.close()


class _ReviewTransaction:
    """One session's reads and adjudication inserts on one open SQLite
    transaction (see ``review_transaction``)."""

    def __init__(self, conn: sqlite3.Connection, session_name: str):
        self._conn = conn
        self._session = session_name

    def latest(self) -> dict[tuple[int, int], Decision]:
        """As ``latest_human_or_script``."""
        return _latest(_select_decisions(self._conn, self._session))

    def adjudications(self) -> dict[int, dict]:
        """As ``current_adjudications``, including ones added in this transaction."""
        return _current_adjudications(self._conn, self._session)

    def add(self, row_idx: int, reviewer_id: int, kb_values: dict, note: str, *,
            basis: str | None, source_users: list[int] | None,
            based_on: dict | None) -> dict:
        """As ``add_adjudication``; committed when the transaction ends."""
        return _insert_adjudication(
            self._conn, self._session, row_idx, reviewer_id, kb_values, note,
            basis=basis, source_users=source_users, based_on=based_on)


@contextmanager
def review_transaction(session_name: str) -> Iterator[_ReviewTransaction]:
    """Check a row and record its adjudication with nothing changing in between.

    Holds the write lock for the whole block: no annotator save or other
    adjudication can land between ``latest``/``adjudications`` and ``add``.
    Commits on clean exit, rolls back if anything in the block raises. Do not
    write through another connection inside the block: it would wait on this one.
    """
    conn = userdb.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            yield _ReviewTransaction(conn, session_name)
    finally:
        conn.close()
