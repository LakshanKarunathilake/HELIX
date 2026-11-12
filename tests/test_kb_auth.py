import pytest
from flask import Flask, jsonify

from kb.auth import install_bearer_auth


def _app(token):
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/query")
    def query():
        return jsonify({"results": []})

    install_bearer_auth(app, token)
    return app.test_client()


def test_no_token_is_open():
    assert _app(None).post("/query").status_code == 200
    assert _app("   ").post("/query").status_code == 200
    assert _app("").post("/query").status_code == 200


def test_token_required():
    c = _app("t")
    r = c.post("/query")
    assert r.status_code == 401
    assert r.get_json() == {"error": "unauthorized"}
    assert c.post("/query", headers={"Authorization": "Bearer x"}).status_code == 401
    assert c.post("/query", headers={"Authorization": "t"}).status_code == 401
    assert c.post("/query", headers={"Authorization": "Bearer t"}).status_code == 200


def test_health_is_public():
    assert _app("t").get("/health").status_code == 200
