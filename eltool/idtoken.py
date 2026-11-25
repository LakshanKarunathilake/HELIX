"""Verify a Firebase ID token (the one the browser gets after Google sign-in).

google-auth is imported inside the function so the API imports without it.
The token is never logged and never put in an error message.
"""
from __future__ import annotations

import os


class IdTokenError(Exception):
    """The token could not be verified (bad, expired, foreign project, no config).

    Messages are fixed strings (at most an exception type name), never token
    content, so they are safe to log."""


# Tolerated clock difference between this server and Google's token issuer.
CLOCK_SKEW_SECONDS = 10
# Timeout for fetching Google's signing certificates (google-auth defaults to 120 s).
CERT_FETCH_TIMEOUT = 5


def verify_id_token(token: str) -> dict:
    """Return ``{uid, email, email_verified, name, provider}`` for a valid token."""
    project = os.environ.get("HELIX_FIREBASE_PROJECT_ID", "").strip()
    if not project:
        raise IdTokenError("HELIX_FIREBASE_PROJECT_ID is not set")
    try:
        import google.auth.transport.requests as google_requests
        import google.oauth2.id_token as google_id_token

        class _ShortTimeoutRequest(google_requests.Request):
            """A transport whose calls default to a short timeout."""

            def __call__(self, url, method="GET", body=None, headers=None,
                         timeout=CERT_FETCH_TIMEOUT, **kwargs):
                return super().__call__(url, method=method, body=body, headers=headers,
                                        timeout=timeout, **kwargs)

        payload = google_id_token.verify_firebase_token(
            token, _ShortTimeoutRequest(), audience=project,
            clock_skew_in_seconds=CLOCK_SKEW_SECONDS)
        if not payload:
            raise IdTokenError("empty token payload")
        if payload.get("iss") != f"https://securetoken.google.com/{project}":
            raise IdTokenError("token rejected (issuer)")
        return {
            "uid": payload["sub"],
            "email": payload.get("email") or "",
            "email_verified": payload.get("email_verified") is True,
            "name": payload.get("name") or "",
            "provider": (payload.get("firebase") or {}).get("sign_in_provider") or "",
        }
    except IdTokenError:
        raise
    except Exception as e:  # noqa: BLE001 - every failure is the same to the caller
        raise IdTokenError(f"token rejected ({type(e).__name__})") from None
