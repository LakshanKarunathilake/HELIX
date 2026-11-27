"""SQLite store for accounts, invites, auth sessions, memberships and the
decision log (sessions/helix.db).

The path is resolved from ``eltool.session.SESSIONS_DIR`` on every call so
tests (and operators) can point it elsewhere without re-importing.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import eltool.session as session_mod

ROLES = ("admin", "reviewer", "annotator")
AUTH_SESSION_DAYS = 14
SCHEMA_VERSION = 2
# added to ``adjudications`` in version 2; NULL on decisions saved before it
_V2_COLUMNS = ("basis", "source_users", "based_on")
# SQLite fails the switch to WAL with "database is locked", without waiting, when
# threads make it at once, so they take turns (the app runs a single worker)
_wal_lock = threading.Lock()

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
    llm_used      TEXT,
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
    note         TEXT,
    basis        TEXT,
    source_users TEXT,
    based_on     TEXT
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


def _require_one(cur: sqlite3.Cursor, user_id: int) -> None:
    if cur.rowcount != 1:
        raise ValueError(f"No user with id {user_id}")


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role '{role}'")


def _upgrade(conn: sqlite3.Connection) -> None:
    """Create the schema, or bring a version-1 database up to date, in one
    write transaction. Only adds tables and columns; existing rows are kept."""
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        # read again under the lock: another connection may have just done it
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        # one statement at a time: executescript() would commit and drop the lock
        for statement in _SCHEMA.split(";"):
            conn.execute(statement)
        present = {r["name"] for r in conn.execute("PRAGMA table_info(adjudications)")}
        for column in _V2_COLUMNS:
            if column not in present:
                conn.execute(f"ALTER TABLE adjudications ADD COLUMN {column} TEXT")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def connect() -> sqlite3.Connection:
    """Open helix.db (created if missing, upgraded if its schema is older)."""
    path = session_mod.SESSIONS_DIR / "helix.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with _wal_lock:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        _upgrade(conn)
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


def insert_user(conn: sqlite3.Connection, email: str, name: str, role: str,
                firebase_uid: str | None = None) -> int:
    """INSERT a user on ``conn`` without committing, so a caller can write the
    user and related rows in one transaction. Returns the new id."""
    _check_role(role)
    cur = conn.execute(
        "INSERT INTO users (email, name, role, firebase_uid, active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (_norm_email(email), name, role, firebase_uid, _now()))
    return cur.lastrowid


def create_user(email: str, name: str, role: str,
                firebase_uid: str | None = None) -> User:
    conn = connect()
    try:
        try:
            with conn:
                new_id = insert_user(conn, email, name, role, firebase_uid)
        except sqlite3.IntegrityError as e:
            raise ValueError(f"A user with this email or sign-in already exists: {e}") from e
        return _user(conn.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone())
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
                _require_one(conn.execute(
                    "UPDATE users SET firebase_uid = ? WHERE id = ?", (uid, user_id)), user_id)
        except sqlite3.IntegrityError as e:
            raise ValueError("This sign-in is already linked to another user") from e
    finally:
        conn.close()


def add_member(session_name: str, user_id: int) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO memberships (session_name, user_id) VALUES (?, ?)",
                (session_name, user_id))
    finally:
        conn.close()


def remove_member(session_name: str, user_id: int) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM memberships WHERE session_name = ? AND user_id = ?",
                         (session_name, user_id))
    finally:
        conn.close()


def delete_memberships_for_session(session_name: str) -> None:
    """Forget every membership of a deleted session, so a reused name starts empty."""
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM memberships WHERE session_name = ?", (session_name,))
    finally:
        conn.close()


def members(session_name: str) -> list[User]:
    conn = connect()
    try:
        return [_user(r) for r in conn.execute(
            "SELECT u.* FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.session_name = ? ORDER BY u.id", (session_name,))]
    finally:
        conn.close()


def sessions_for(user_id: int) -> list[str]:
    conn = connect()
    try:
        return [r["session_name"] for r in conn.execute(
            "SELECT session_name FROM memberships WHERE user_id = ? ORDER BY session_name",
            (user_id,))]
    finally:
        conn.close()


def is_member(session_name: str, user_id: int) -> bool:
    conn = connect()
    try:
        return conn.execute(
            "SELECT 1 FROM memberships WHERE session_name = ? AND user_id = ?",
            (session_name, user_id)).fetchone() is not None
    finally:
        conn.close()


def count_active_admins() -> int:
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]
    finally:
        conn.close()


class LastAdminError(Exception):
    """The change would leave no active admin."""


def update_user(user_id: int, role: str | None = None, active: bool | None = None) -> User:
    """Change a user's role and/or active flag in one write transaction, so the
    last-admin check and the update cannot be raced. Deactivating also deletes
    the user's login sessions. Raises ValueError (bad role), LookupError (no
    such user) or LastAdminError."""
    if role is not None:
        _check_role(role)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")  # take the write lock before reading the admin count
        with conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise LookupError(f"No user with id {user_id}")
            new_role = row["role"] if role is None else role
            new_active = bool(row["active"]) if active is None else active
            was_admin = row["role"] == "admin" and bool(row["active"])
            if was_admin and not (new_role == "admin" and new_active):
                others = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                    (user_id,)).fetchone()[0]
                if others == 0:
                    raise LastAdminError("Cannot remove the last admin")
            conn.execute("UPDATE users SET role = ?, active = ? WHERE id = ?",
                         (new_role, 1 if new_active else 0, user_id))
            if not new_active:
                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            return _user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    finally:
        conn.close()


def replace_memberships(user_id: int, session_names: list[str]) -> list[str]:
    """Make ``session_names`` exactly the user's memberships, atomically.
    Raises LookupError if the user does not exist. Returns the new sorted list."""
    wanted = sorted(set(session_names))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise LookupError(f"No user with id {user_id}")
            conn.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
            conn.executemany(
                "INSERT INTO memberships (session_name, user_id) VALUES (?, ?)",
                [(n, user_id) for n in wanted])
        return wanted
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
            _require_one(conn.execute(
                "UPDATE users SET role = ? WHERE id = ?", (role, user_id)), user_id)
    finally:
        conn.close()


def set_active(user_id: int, active: bool) -> None:
    conn = connect()
    try:
        with conn:
            _require_one(conn.execute(
                "UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id)), user_id)
    finally:
        conn.close()


def touch_login(user_id: int) -> None:
    conn = connect()
    try:
        with conn:
            _require_one(conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id)), user_id)
    finally:
        conn.close()


def create_auth_session(user_id: int) -> str:
    """Start a login session; returns the raw token (only its hash is stored)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=AUTH_SESSION_DAYS)
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (_now(),))
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash(token), user_id, now.isoformat(timespec="seconds"),
                 expires.isoformat(timespec="seconds")))
    finally:
        conn.close()
    return token


def user_for_token(token: str) -> User | None:
    """The active user a login token belongs to; None if unknown or expired."""
    return _one(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
        (token_hash(token), _now()))


def delete_auth_session(token: str) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))
    finally:
        conn.close()


def delete_user_auth_sessions(user_id: int) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    finally:
        conn.close()
