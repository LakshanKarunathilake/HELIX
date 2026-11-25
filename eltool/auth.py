"""Login gate: the cookie, the middleware and the role dependencies.

Nothing here runs unless ``HELIX_AUTH=1``. Tokens and cookies are never logged.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from eltool import userdb
from eltool.hosting import auth_enabled, hosted_mode

COOKIE_NAME = "helix_session"
# Exact paths an anonymous caller may use. Every other /api/ path needs a login.
PUBLIC_PATHS = frozenset({"/api/info", "/api/auth/session"})


def set_login_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=userdb.AUTH_SESSION_DAYS * 24 * 3600,
        httponly=True, samesite="strict", secure=hosted_mode(), path="/")


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="strict", secure=hosted_mode(), path="/")


async def auth_gate(request: Request, call_next):
    """Middleware: with auth on, every /api/ path but PUBLIC_PATHS needs a login."""
    path = request.url.path
    if auth_enabled() and path.startswith("/api/") and path not in PUBLIC_PATHS:
        token = request.cookies.get(COOKIE_NAME)
        user = await run_in_threadpool(userdb.user_for_token, token) if token else None
        if user is None:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        request.state.user = user
    return await call_next(request)


def current_user(request: Request) -> userdb.User | None:
    """The signed-in user, or None when auth is off."""
    if not auth_enabled():
        return None
    return getattr(request.state, "user", None)


def require_admin(request: Request) -> userdb.User | None:
    """Dependency: admin only. With auth off there are no roles, so it passes."""
    if not auth_enabled():
        return None
    user = current_user(request)
    if user is None or user.role != "admin":
        raise HTTPException(403, "Administrators only")
    return user


def require_reviewer(request: Request) -> userdb.User | None:
    """Dependency: admin or reviewer. With auth off there are no roles, so it passes."""
    if not auth_enabled():
        return None
    user = current_user(request)
    if user is None or user.role not in ("admin", "reviewer"):
        raise HTTPException(403, "Reviewers only")
    return user
