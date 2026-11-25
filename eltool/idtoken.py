"""Verify a Firebase ID token (the one the browser gets after Google sign-in).

google-auth is imported inside the function so the API imports without it.
The token is never logged and never put in an error message.
"""
from __future__ import annotations

import os


class IdTokenError(Exception):
    """The token could not be verified (bad, expired, foreign project, no config)."""


def verify_id_token(token: str) -> dict:
    """Return ``{uid, email, email_verified, name, provider}`` for a valid token."""
    project = os.environ.get("HELIX_FIREBASE_PROJECT_ID", "").strip()
    if not project:
        raise IdTokenError("HELIX_FIREBASE_PROJECT_ID is not set")
    try:
        import google.auth.transport.requests as google_requests
        import google.oauth2.id_token as google_id_token

        payload = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=project)
        if not payload:
            raise IdTokenError("empty token payload")
        return {
            "uid": payload["sub"],
            "email": payload.get("email") or "",
            "email_verified": bool(payload.get("email_verified")),
            "name": payload.get("name") or "",
            "provider": (payload.get("firebase") or {}).get("sign_in_provider") or "",
        }
    except IdTokenError:
        raise
    except Exception as e:  # noqa: BLE001 - every failure is the same to the caller
        raise IdTokenError(f"token rejected ({type(e).__name__})") from None
