"""Account endpoints: sign in, who am I, sign out, the admin's invites and the decision log."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from eltool import decisions, idtoken, invites, userdb
from eltool.auth import COOKIE_NAME, clear_login_cookie, current_user, require_admin, set_login_cookie
from eltool.config import SLUG_RE
from eltool.hosting import auth_enabled
from eltool.session import list_sessions
from eltool.store import spreadsheet_safe_cell

router = APIRouter()
logger = logging.getLogger(__name__)

# One message for every sign-in refusal: it must not reveal which check failed.
_REFUSED = "This Google account is not authorised for HELIX"


def _auth_mode_only() -> None:
    if not auth_enabled():
        raise HTTPException(404, "Not found")


class SignInRequest(BaseModel):
    id_token: str


class InviteRequest(BaseModel):
    email: str
    role: str
    session_names: list[str] = Field(default_factory=list)


def _invite_json(inv: invites.Invite) -> dict:
    return {"id": inv.id, "email": inv.email, "role": inv.role,
            "session_names": inv.session_names, "status": inv.status}


@router.post("/api/auth/session", dependencies=[Depends(_auth_mode_only)])
def sign_in(body: SignInRequest, response: Response):
    try:
        user = invites.provision(idtoken.verify_id_token(body.id_token))
    except idtoken.IdTokenError as e:
        # IdTokenError messages are fixed strings (no token content).
        logger.warning("sign-in refused: token (%s)", e)
        raise HTTPException(403, _REFUSED) from None
    except invites.NotAuthorised as e:
        logger.warning("sign-in refused: %s", e.reason)
        raise HTTPException(403, _REFUSED) from None
    set_login_cookie(response, userdb.create_auth_session(user.id))
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Not signed in")
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token and auth_enabled():
        userdb.delete_auth_session(token)
    clear_login_cookie(response)
    return {"ok": True}


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None


class MembershipsRequest(BaseModel):
    session_names: list[str]


def _user_json(user: userdb.User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
            "active": user.active, "session_names": userdb.sessions_for(user.id)}


@router.get("/api/admin/users",
            dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def list_users():
    return [_user_json(u) for u in userdb.list_users()]


@router.patch("/api/admin/users/{user_id}",
              dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def update_user(user_id: int, body: UserUpdate):
    try:
        user = userdb.update_user(user_id, role=body.role, active=body.active)
    except LookupError:
        raise HTTPException(404, "No such user") from None
    except userdb.LastAdminError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return _user_json(user)


@router.put("/api/admin/users/{user_id}/sessions",
            dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def replace_user_sessions(user_id: int, body: MembershipsRequest):
    known = set(list_sessions())
    unknown = [n for n in body.session_names if n not in known]
    if unknown:
        raise HTTPException(400, f"Unknown session: {', '.join(unknown)}")
    try:
        userdb.replace_memberships(user_id, body.session_names)
    except LookupError:
        raise HTTPException(404, "No such user") from None
    return _user_json(userdb.get_user(user_id))


@router.post("/api/admin/invites", dependencies=[Depends(_auth_mode_only)])
def create_invite(body: InviteRequest, admin: userdb.User = Depends(require_admin)):
    known = set(list_sessions())
    unknown = [n for n in body.session_names if n not in known]
    if unknown:
        raise HTTPException(400, f"Unknown session: {', '.join(unknown)}")
    try:
        invite, registered = invites.create_invite(
            body.email, body.role, body.session_names, admin.id)
    except invites.DuplicateInvite as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"invite": _invite_json(invite),
            "sign_in_url": os.environ.get("HELIX_PUBLIC_URL") or "http://localhost:5173",
            "already_registered": registered}


@router.get("/api/admin/invites",
            dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def list_invites():
    return [_invite_json(i) for i in invites.list_invites()]


_DECISION_CSV_COLUMNS = [
    "id", "ts", "session_name", "row_idx", "user_id", "user_name", "user_email", "actor",
    "action", "kb_values", "previous", "llm_used", "method", "elapsed_ms", "selected_rank",
    "revision"]


@router.get("/api/admin/decisions",
            dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def list_decisions(session: str | None = None, user: int | None = None):
    return [asdict(d) for d in decisions.list_decisions(session, user)]


@router.get("/api/admin/decisions.csv",
            dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def export_decisions(session: str | None = None):
    users = {u.id: u for u in userdb.list_users()}
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(_DECISION_CSV_COLUMNS)
    for d in decisions.list_decisions(session):
        who = users.get(d.user_id)
        row = {**asdict(d), "kb_values": json.dumps(d.kb_values),
               "previous": json.dumps(d.previous),
               "user_name": who.name if who else "", "user_email": who.email if who else ""}
        out.writerow([spreadsheet_safe_cell(row[c]) for c in _DECISION_CSV_COLUMNS])
    fname = f"{session}_decisions.csv" if session and SLUG_RE.fullmatch(session) \
        else "decisions.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/api/admin/invites/{invite_id}/revoke",
             dependencies=[Depends(_auth_mode_only), Depends(require_admin)])
def revoke_invite(invite_id: int):
    try:
        invites.revoke(invite_id)
    except LookupError:
        raise HTTPException(404, "No such invite") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True}
