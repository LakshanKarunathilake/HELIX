"""Write-only secrets: masked on GET, kept on blank PUT, never redirected."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.config import SessionConfig
from eltool.secrets import fill_graph_secret, fill_kb_secret

HDR = '{"Authorization": "Bearer t"}'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app
    sdir = tmp_path / "s1"
    sdir.mkdir()
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung"], "document_id": ["d1"]}).to_csv(inp, index=False)
    cfg = {
        "session_name": "s1", "annotator": "a", "input_path": str(inp),
        "delimiter": ",", "label_column": "entity_text",
        "llm_api_key": "sk-secret",
        "kbs": [{"name": "UMLS", "endpoint_url": "https://kb.example.com/query",
                 "headers": HDR}],
        "graph": {"uri": "neo4j+s://db.example.com:7687", "user": "neo4j",
                  "password": "hunter2pw"},
    }
    (sdir / "session.json").write_text(json.dumps(cfg))
    return TestClient(app)


def _stored(tmp_path):
    return json.loads((tmp_path / "s1" / "session.json").read_text())


def test_get_masks_all_secrets(client):
    d = client.get("/api/sessions/s1").json()
    assert d["kbs"][0]["headers"] == ""
    assert d["kbs"][0]["headers_set"] is True
    assert d["graph"]["password"] == ""
    assert d["graph"]["password_set"] is True
    assert d["llm_api_key"] == ""
    assert d["llm_api_key_set"] is True


def test_put_blank_keeps_secrets(client, tmp_path):
    body = client.get("/api/sessions/s1").json()
    assert client.put("/api/sessions/s1", json=body).status_code == 200
    s = _stored(tmp_path)
    assert s["kbs"][0]["headers"] == HDR
    assert s["graph"]["password"] == "hunter2pw"
    assert s["llm_api_key"] == "sk-secret"
    assert "headers_set" not in s["kbs"][0]
    assert "password_set" not in s["graph"]


def test_blank_secret_not_reused_when_endpoint_changes(client, tmp_path):
    body = client.get("/api/sessions/s1").json()
    body["kbs"][0]["endpoint_url"] = "https://evil.example.com/query"
    body["graph"]["uri"] = "neo4j+s://evil.example.com:7687"
    assert client.put("/api/sessions/s1", json=body).status_code == 200
    s = _stored(tmp_path)
    assert s["kbs"][0]["headers"] == "{}"
    assert s["graph"]["password"] == ""


def test_kb_test_uses_stored_headers(client, monkeypatch):
    seen = {}

    class R:
        status_code = 200
        headers = {}
        text = "[]"
        is_redirect = False
        ok = True
        def raise_for_status(self): pass
        def json(self): return []

    def fake_post(url, **kw):
        seen.update(kw.get("headers") or {})
        return R()

    monkeypatch.setattr("eltool.kb_client.requests.post", fake_post)
    kb = client.get("/api/sessions/s1").json()["kbs"][0]
    r = client.post("/api/kb/test", json={"kb_config": kb, "session_name": "s1"})
    assert r.status_code == 200
    assert seen.get("Authorization") == "Bearer t"


def test_graph_test_fills_stored_password(client, monkeypatch):
    got = {}

    class FakeClient:
        def ping(self): return 1
        def close(self): pass

    def fake_from(conn):
        got["pw"] = conn.password
        return FakeClient()

    monkeypatch.setattr("api.main.Neo4jGraphClient.from_connection", staticmethod(fake_from))
    g = client.get("/api/sessions/s1").json()["graph"]
    r = client.post("/api/graph/test", json={"graph": g, "session_name": "s1"}).json()
    assert r["ok"] is True and got["pw"] == "hunter2pw"


def test_graph_test_validation_error_leaks_no_secret(client):
    # Missing uri is a model-level error: pydantic's str(e) embeds the whole dict.
    from pydantic import ValidationError
    from eltool.config import GraphConnection
    with pytest.raises(ValidationError) as ei:
        GraphConnection(password="hunter2pw")
    assert "hunter2pw" in str(ei.value)   # the old behaviour would have leaked
    r = client.post("/api/graph/test", json={"graph": {"password": "hunter2pw"}})
    assert r.json()["ok"] is False and "hunter2pw" not in r.text


def test_failed_put_does_not_echo_stored_secrets(client):
    # Key order matters: pydantic truncates its input repr, so the filled-in
    # secrets must sit early in the dict for the old str(e) to show them.
    kb = {"name": "UMLS", "endpoint_url": "https://kb.example.com/query", "headers": ""}
    body = {
        "llm_api_key": "",
        "kbs": [dict(kb), dict(kb)],          # duplicate names -> validation error
        "graph": {"uri": "neo4j+s://db.example.com:7687", "user": "neo4j"},
        "session_name": "s1", "annotator": "a",
        "input_path": "x.csv", "label_column": "entity_text",
    }
    r = client.put("/api/sessions/s1", json=body)
    assert r.status_code == 400
    for secret in ("hunter2pw", "sk-secre", "Bearer t"):
        assert secret not in r.text


def _capture_kb(monkeypatch):
    seen = {}

    class R:
        status_code = 200
        headers = {}
        text = "[]"
        is_redirect = False
        ok = True
        def raise_for_status(self): pass
        def json(self): return []

    def fake_post(url, **kw):
        seen["headers"] = dict(kw.get("headers") or {})
        return R()

    monkeypatch.setattr("eltool.kb_client.requests.post", fake_post)
    return seen


@pytest.mark.parametrize("change", [
    {"endpoint_url": "https://other.example.com/query"},
    {"name": "Other"},
])
def test_kb_test_does_not_reuse_headers_when_target_changes(client, monkeypatch, change):
    seen = _capture_kb(monkeypatch)
    kb = client.get("/api/sessions/s1").json()["kbs"][0]
    kb.update(change)
    r = client.post("/api/kb/test", json={"kb_config": kb, "session_name": "s1"})
    assert r.status_code == 200
    assert "Authorization" not in seen["headers"]


@pytest.mark.parametrize("change", [{"user": "other"}, {"uri": "neo4j+s://other.example.com:7687"}])
def test_graph_test_does_not_reuse_password_when_target_changes(client, monkeypatch, change):
    got = {}

    class FakeClient:
        def ping(self): return 1
        def close(self): pass

    def fake_from(conn):
        got["pw"] = conn.password
        return FakeClient()

    monkeypatch.setattr("api.main.Neo4jGraphClient.from_connection", staticmethod(fake_from))
    g = client.get("/api/sessions/s1").json()["graph"]
    g.update(change)
    r = client.post("/api/graph/test", json={"graph": g, "session_name": "s1"}).json()
    assert r["ok"] is True and got["pw"] == ""


@pytest.mark.parametrize("sname", ["does-not-exist", "../x"])
def test_kb_test_unknown_session_name_is_not_404(client, monkeypatch, sname):
    seen = _capture_kb(monkeypatch)
    kb = client.get("/api/sessions/s1").json()["kbs"][0]
    r = client.post("/api/kb/test", json={"kb_config": kb, "session_name": sname})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["headers"] == {}


def test_unknown_session_name_uses_no_existing(client):
    kb = fill_kb_secret({"name": "x", "endpoint_url": "u", "headers": " "}, None)
    assert kb["headers"] == "{}"
    assert fill_graph_secret({"uri": "u", "password": None}, None)["password"] == ""


def test_helpers_do_not_mutate(client, tmp_path):
    existing = SessionConfig(**_stored(tmp_path))
    kb = {"name": "UMLS", "endpoint_url": "https://kb.example.com/query",
          "headers": "", "headers_set": True}
    out = fill_kb_secret(kb, existing)
    assert out["headers"] == HDR and kb["headers"] == "" and "headers_set" in kb
