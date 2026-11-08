"""Optional bearer-token auth for the KB server."""
from __future__ import annotations

import hmac

from flask import Flask, jsonify, request


def install_bearer_auth(app: Flask, token: str | None) -> None:
    """Require ``Authorization: Bearer <token>`` on every route except /health.

    A no-op when ``token`` is empty, so local use stays open.
    """
    if not token:
        return
    expected = f"Bearer {token}".encode("utf-8")

    @app.before_request
    def _check_bearer():
        if request.path == "/health":
            return None
        given = request.headers.get("Authorization", "").encode("utf-8")
        if not hmac.compare_digest(given, expected):
            return jsonify({"error": "unauthorized"}), 401
        return None
