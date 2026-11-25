"""SQLite store for accounts, invites, auth sessions, memberships and the
decision log (sessions/helix.db).

The path is resolved from ``eltool.session.SESSIONS_DIR`` on every call so
tests (and operators) can point it elsewhere without re-importing.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import eltool.session as session_mod

ROLES = ("admin", "reviewer", "annotator")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'annotator')),
    firebase_uid  TEXT UNIQUE,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'annotator')),
    session_names TEXT NOT NULL DEFAULT '[]',
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    accepted_at   TEXT,
    revoked_at    TEXT
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    session_name TEXT NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (session_name, user_id)
);
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    session_name  TEXT NOT NULL,
    row_idx       INTEGER NOT NULL,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    actor         TEXT NOT NULL CHECK (actor IN ('human', 'auto_annotate', 'auto_match')),
    action        TEXT NOT NULL CHECK (action IN ('save', 'feedback', 'batch', 'auto')),
    kb_values     TEXT,
    previous      TEXT,
    llm_used      INTEGER,
    method        TEXT,
    elapsed_ms    INTEGER,
    selected_rank INTEGER,
    revision      INTEGER
);
CREATE INDEX IF NOT EXISTS decisions_by_row ON decisions (session_name, row_idx);
CREATE TABLE IF NOT EXISTS adjudications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_name TEXT NOT NULL,
    row_idx      INTEGER NOT NULL,
    reviewer_id  INTEGER NOT NULL REFERENCES users(id),
    kb_values    TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS adjudications_by_row ON adjudications (session_name, row_idx);
"""


@dataclass
class User:
    id: int
    email: str
    name: str
    role: str
    active: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role '{role}'")


def connect() -> sqlite3.Connection:
    """Open helix.db (created with its schema if missing)."""
    path = session_mod.SESSIONS_DIR / "helix.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA user_version=1")
    return conn


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(id=row["id"], email=row["email"], name=row["name"],
                role=row["role"], active=bool(row["active"]))


def _one(sql: str, args: tuple) -> User | None:
    conn = connect()
    try:
        return _user(conn.execute(sql, args).fetchone())
    finally:
        conn.close()


def create_user(email: str, name: str, role: str,
                firebase_uid: str | None = None) -> User:
    _check_role(role)
    email = _norm_email(email)
    conn = connect()
    try:
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO users (email, name, role, firebase_uid, active, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (email, name, role, firebase_uid, _now()))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"A user with this email or sign-in already exists: {e}") from e
        return _user(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def get_user(user_id: int) -> User | None:
    return _one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_email(email: str) -> User | None:
    return _one("SELECT * FROM users WHERE email = ?", (_norm_email(email),))


def get_user_by_uid(uid: str) -> User | None:
    return _one("SELECT * FROM users WHERE firebase_uid = ?", (uid,))


def link_uid(user_id: int, uid: str) -> None:
    conn = connect()
    try:
        try:
            with conn:
                conn.execute("UPDATE users SET firebase_uid = ? WHERE id = ?", (uid, user_id))
        except sqlite3.IntegrityError as e:
            raise ValueError("This sign-in is already linked to another user") from e
    finally:
        conn.close()


def list_users() -> list[User]:
    conn = connect()
    try:
        return [_user(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]
    finally:
        conn.close()


def set_role(user_id: int, role: str) -> None:
    _check_role(role)
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    finally:
        conn.close()


def set_active(user_id: int, active: bool) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
    finally:
        conn.close()


def touch_login(user_id: int) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
    finally:
        conn.close()
