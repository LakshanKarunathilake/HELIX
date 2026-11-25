"""Invites: an allow-list of emails that may sign in, and first-sign-in provisioning.

An invite carries no token and no expiry; it only says "this Google address may
create an account with this role and these sessions". Revoke it to withdraw it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from eltool import userdb

# Only Google sign-in is accepted for now.
ALLOWED_PROVIDERS = frozenset({"google.com"})


class NotAuthorised(Exception):
    """A sign-in was refused. Callers must not say which check failed."""


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


def _mark_accepted(invite_id: int) -> None:
    conn = userdb.connect()
    try:
        with conn:
            conn.execute("UPDATE invites SET accepted_at = ? WHERE id = ?",
                         (userdb._now(), invite_id))
    finally:
        conn.close()


def provision(claims: dict) -> userdb.User:
    """The user a verified ID token signs in as, creating or linking the account
    on first sign-in. Raises NotAuthorised for every refusal."""
    uid = claims.get("uid") or ""
    email = userdb._norm_email(claims.get("email") or "")
    if (not uid or not email or claims.get("email_verified") is not True
            or claims.get("provider") not in ALLOWED_PROVIDERS):
        raise NotAuthorised()

    user = userdb.get_user_by_uid(uid)
    if user is None:
        user = userdb.get_user_by_email(email)
        if user is not None:
            # A registered row (bootstrap admin) links on first sign-in; one
            # already linked to some other Google identity is refused.
            if userdb.get_firebase_uid(user.id) is not None:
                raise NotAuthorised()
            try:
                userdb.link_uid(user.id, uid)
            except ValueError:
                raise NotAuthorised() from None
        else:
            conn = userdb.connect()
            try:
                row = _pending_for(conn, email)
            finally:
                conn.close()
            if row is None:
                raise NotAuthorised()
            invite = _invite(row)
            name = (claims.get("name") or "").strip() or email.split("@")[0]
            try:
                user = userdb.create_user(email, name, invite.role, firebase_uid=uid)
            except ValueError:
                raise NotAuthorised() from None
            for session_name in invite.session_names:
                userdb.add_member(session_name, user.id)
            _mark_accepted(invite.id)
    if not user.active:
        raise NotAuthorised()
    userdb.touch_login(user.id)
    return user
