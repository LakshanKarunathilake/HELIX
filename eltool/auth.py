"""Login gate: the cookie, the middleware and the role dependencies.

Nothing here runs unless ``HELIX_AUTH=1``. Tokens and cookies are never logged.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from eltool import userdb
from eltool.config import SLUG_RE
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


def session_access(name: str, request: Request) -> userdb.User | None:
    """Dependency: the caller may open session ``name`` (admin, or a member).

    With auth off it passes (None). A session the caller may not see is a 404,
    not a 403, so its existence is never revealed. A non-slug name is a 404 too.
    """
    if not isinstance(name, str) or not SLUG_RE.fullmatch(name):
        raise HTTPException(404, "Session not found")
    if not auth_enabled():
        return None
    user = current_user(request)
    if user is None:
        raise HTTPException(404, "Session not found")
    if user.role == "admin" or userdb.is_member(name, user.id):
        return user
    raise HTTPException(404, "Session not found")


def annotator_access(name: str, request: Request) -> userdb.User | None:
    """Dependency: ``session_access`` (404 first), then 403 for a reviewer, who
    judges the annotators' work and so writes none. With auth off it passes."""
    user = session_access(name, request)
    if user is not None and user.role == "reviewer":
        raise HTTPException(403, "Reviewers cannot annotate")
    return user
