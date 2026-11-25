"""Account endpoints: who am I, and sign out. (Sign-in itself is added separately.)"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from eltool import userdb
from eltool.auth import COOKIE_NAME, clear_login_cookie, current_user

router = APIRouter()


@router.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Not signed in")
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        userdb.delete_auth_session(token)
    clear_login_cookie(response)
    return {"ok": True}
