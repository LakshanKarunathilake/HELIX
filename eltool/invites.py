"""Invites: an allow-list of emails that may sign in, and first-sign-in provisioning.

An invite carries no token and no expiry; it only says "this Google address may
create an account with this role and these sessions". Revoke it to withdraw it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from eltool import userdb
from eltool.session import list_sessions

# Only Google sign-in is accepted for now.
ALLOWED_PROVIDERS = frozenset({"google.com"})


class NotAuthorised(Exception):
    """A sign-in was refused. Callers must not tell the user which check failed.

    ``reason`` is a fixed category for the server log only ("claims",
    "not_invited", "inactive", "uid_mismatch", "invite_withdrawn", "conflict");
    it never holds an email, uid or token."""

    def __init__(self, reason: str = "refused"):
        super().__init__(reason)
        self.reason = reason


class DuplicateInvite(ValueError):
    """The email already has a pending invite."""


@dataclass
class Invite:
    id: int
    email: str
    role: str
    session_names: list[str]
    status: str  # pending | accepted | revoked


def _invite(row: sqlite3.Row) -> Invite:
    if row["revoked_at"]:
        status = "revoked"
    elif row["accepted_at"]:
        status = "accepted"
    else:
        status = "pending"
    return Invite(id=row["id"], email=row["email"], role=row["role"],
                  session_names=json.loads(row["session_names"]), status=status)


def _pending_for(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM invites WHERE email = ? AND accepted_at IS NULL AND revoked_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (email,)).fetchone()


def create_invite(email: str, role: str, session_names: list[str],
                  created_by: int | None) -> tuple[Invite, bool]:
    """Allow ``email`` to sign in. Returns (invite, already_registered).

    If the email already has an account its memberships are added at once and
    the invite is stored as accepted (its role is not applied; change roles on
    the Users page).
    """
    if role not in userdb.ROLES:
        raise ValueError(f"Unknown role '{role}'")
    email = userdb._norm_email(email)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("Enter a valid email address")
    session_names = list(dict.fromkeys(session_names))
    existing = userdb.get_user_by_email(email)
    conn = userdb.connect()
    try:
        if existing is None and _pending_for(conn, email) is not None:
            raise DuplicateInvite("This email already has a pending invite; revoke it first")
        with conn:
            cur = conn.execute(
                "INSERT INTO invites (email, role, session_names, created_by, created_at, "
                "accepted_at) VALUES (?, ?, ?, ?, ?, ?)",
                (email, role, json.dumps(session_names), created_by, userdb._now(),
                 userdb._now() if existing else None))
            if existing:
                conn.executemany(
                    "INSERT OR IGNORE INTO memberships (session_name, user_id) VALUES (?, ?)",
                    [(s, existing.id) for s in session_names])
        return _invite(conn.execute(
            "SELECT * FROM invites WHERE id = ?", (cur.lastrowid,)).fetchone()), existing is not None
    finally:
        conn.close()


def list_invites() -> list[Invite]:
    conn = userdb.connect()
    try:
        return [_invite(r) for r in conn.execute("SELECT * FROM invites ORDER BY id DESC")]
    finally:
        conn.close()


def revoke(invite_id: int) -> None:
    """Withdraw a pending invite. Raises LookupError if there is none such id,
    ValueError if it is no longer pending."""
    conn = userdb.connect()
    try:
        row = conn.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
        if row is None:
            raise LookupError(f"No invite with id {invite_id}")
        if _invite(row).status != "pending":
            raise ValueError("Only a pending invite can be revoked")
        with conn:
            conn.execute("UPDATE invites SET revoked_at = ? WHERE id = ?",
                         (userdb._now(), invite_id))
    finally:
        conn.close()


def _apply_invite(conn: sqlite3.Connection, invite: Invite, user_id: int) -> bool:
    """Consume a still-pending invite and add its memberships, on ``conn`` and
    without committing. False (and nothing added) if it was revoked or accepted
    since it was read."""
    cur = conn.execute(
        "UPDATE invites SET accepted_at = ? "
        "WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
        (userdb._now(), invite.id))
    if cur.rowcount != 1:
        return False
    # A session deleted since the invite was made grants nothing, even if a new
    # session has taken its name.
    existing = set(list_sessions())
    conn.executemany(
        "INSERT OR IGNORE INTO memberships (session_name, user_id) VALUES (?, ?)",
        [(s, user_id) for s in invite.session_names if s in existing])
    return True


def forget_session(session_name: str) -> None:
    """Remove a deleted session's name from every invite, so a pending invite
    cannot make its invitee a member of a later session with the same name."""
    conn = userdb.connect()
    try:
        with conn:
            rows = conn.execute("SELECT id, session_names FROM invites").fetchall()
            for row in rows:
                names = json.loads(row["session_names"])
                if session_name in names:
                    conn.execute("UPDATE invites SET session_names = ? WHERE id = ?",
                                 (json.dumps([n for n in names if n != session_name]),
                                  row["id"]))
    finally:
        conn.close()


def provision(claims: dict) -> userdb.User:
    """The user a verified ID token signs in as, creating or linking the account
    on first sign-in. Raises NotAuthorised for every refusal; a refusal changes
    nothing, and the user, memberships and accepted mark are written together."""
    uid = claims.get("uid") or ""
    email = userdb._norm_email(claims.get("email") or "")
    if (not uid or not email or claims.get("email_verified") is not True
            or claims.get("provider") not in ALLOWED_PROVIDERS):
        raise NotAuthorised("claims")

    user = userdb.get_user_by_uid(uid)
    if user is None:
        conn = userdb.connect()
        try:
            user = userdb.get_user_by_email(email)
            if user is not None:
                if not user.active:
                    raise NotAuthorised("inactive")
                # A registered row without a uid (bootstrap admin) links now; one
                # already linked to another Google identity matches no row.
                row = _pending_for(conn, email)
                with conn:
                    cur = conn.execute(
                        "UPDATE users SET firebase_uid = ? WHERE id = ? AND firebase_uid IS NULL",
                        (uid, user.id))
                    if cur.rowcount != 1:
                        raise NotAuthorised("uid_mismatch")
                    if row is not None:
                        _apply_invite(conn, _invite(row), user.id)
            else:
                row = _pending_for(conn, email)
                if row is None:
                    raise NotAuthorised("not_invited")
                invite = _invite(row)
                name = (claims.get("name") or "").strip() or email.split("@")[0]
                with conn:
                    user_id = userdb.insert_user(conn, email, name, invite.role, uid)
                    if not _apply_invite(conn, invite, user_id):
                        raise NotAuthorised("invite_withdrawn")
                user = userdb.get_user(user_id)
        except sqlite3.IntegrityError:
            raise NotAuthorised("conflict") from None
        finally:
            conn.close()
    if not user.active:
        raise NotAuthorised("inactive")
    userdb.touch_login(user.id)
    return user
