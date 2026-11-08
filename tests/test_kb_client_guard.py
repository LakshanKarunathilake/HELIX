"""KB calls go through the URL guard, never follow redirects, and manual
entity lookups send the KB's configured headers."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.config import KBConfig
from eltool.hosting import UnsafeURLError
from eltool.kb_client import KBClient, fetch_entity


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._body


class Recorder:
    def __init__(self, resp=None):
        self.calls = []
        self.resp = resp or FakeResp(body=[])

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.resp


def _cfg(url="http://127.0.0.1:8765/query", **kw):
    return KBConfig(name="UMLS", endpoint_url=url, **kw)


def test_query_disables_redirects(monkeypatch):
    rec = Recorder(FakeResp(body=[]))
    monkeypatch.setattr("eltool.kb_client.requests.post", rec)
    KBClient(_cfg()).query("x")
    assert rec.calls[0][1]["allow_redirects"] is False


def test_redirects_are_not_followed(monkeypatch):
    rec = Recorder(FakeResp(302, headers={"Location": "http://169.254.169.254/"}))
    monkeypatch.setattr("eltool.kb_client.requests.post", rec)
    with pytest.raises(ValueError, match="redirects are not followed"):
        KBClient(_cfg()).query("x")


def test_hosted_private_kb_blocked_before_request(monkeypatch):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    rec = Recorder()
    monkeypatch.setattr("eltool.kb_client.requests.post", rec)
    with pytest.raises(UnsafeURLError):
        KBClient(_cfg("https://127.0.0.1/query")).query("x")
    assert rec.calls == []


def test_fetch_entity_sends_headers(monkeypatch):
    rec = Recorder(FakeResp(body={"entity_id": "C1"}))
    monkeypatch.setattr("eltool.kb_client.requests.get", rec)
    out = fetch_entity(_cfg(headers='{"Authorization": "Bearer t"}'), "C1")
    url, kw = rec.calls[0]
    assert url.endswith("/entity/C1")
    assert kw["headers"] == {"Authorization": "Bearer t"}
    assert kw["allow_redirects"] is False
    assert out == {"entity_id": "C1"}


def test_fetch_entity_404_is_none(monkeypatch):
    monkeypatch.setattr("eltool.kb_client.requests.get", Recorder(FakeResp(404)))
    assert fetch_entity(_cfg(), "C1") is None


def _make_session(tmp_path, kb: dict) -> None:
    sdir = tmp_path / "s"
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung"], "document_id": ["d1"]}).to_csv(inp, index=False)
    cfg = {
        "session_name": "s",
        "annotator": "a",
        "input_path": str(inp),
        "delimiter": ",",
        "label_column": "entity_text",
        "kbs": [kb],
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app
    return TestClient(app)


def test_lookup_entity_route_uses_kb_headers(client, tmp_path, monkeypatch):
    _make_session(tmp_path, {"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query",
                             "headers": '{"X-Key": "k"}'})
    rec = Recorder(FakeResp(body={"entity_id": "C1", "primary_alias": "lung"}))
    monkeypatch.setattr("eltool.kb_client.requests.get", rec)
    resp = client.get("/api/sessions/s/kb/UMLS/entity/C1")
    assert resp.status_code == 200
    assert resp.json()["found"] is True
    assert rec.calls[0][1]["headers"] == {"X-Key": "k"}


def test_lookup_entity_unsafe_url_is_400(client, tmp_path, monkeypatch):
    _make_session(tmp_path, {"name": "UMLS", "endpoint_url": "https://127.0.0.1/query"})
    monkeypatch.setenv("HELIX_HOSTED", "1")
    resp = client.get("/api/sessions/s/kb/UMLS/entity/C1")
    assert resp.status_code == 400
