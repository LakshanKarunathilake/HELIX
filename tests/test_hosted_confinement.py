"""Session confinement and outbound-URL checks (final whole-branch review).

C1: PUT cannot repoint input_path / session_name; hosted _load_cfg backstop.
C2/C5: create_session validates everything before any side effect; tmp_path
       must be an upload; shutil.move survives a cross-device move.
C3: path-param session names are slugs (no '..' / '.').
C4/I3: llm_base_url / KB / graph URLs are guarded in hosted mode; a stored LLM
       key is not redirected to a new destination.
"""

from __future__ import annotations

import errno
import json
import os
import socket

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod

VICTIM_KEY = "sk-VICTIM-SECRET"
VICTIM_TOKEN = "VICTIMTOKEN"


def _public_dns(monkeypatch):
    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]
    monkeypatch.setattr("eltool.hosting.socket.getaddrinfo", fake)


def _write_session(sessions, name, **extra):
    d = sessions / name
    d.mkdir(exist_ok=True)
    inp = d / "input.csv"
    pd.DataFrame({"entity_text": ["lung"], "document_id": ["session"]}).to_csv(inp, index=False)
    cfg = {"session_name": name, "annotator": "a", "input_path": str(inp),
           "delimiter": ",", "label_column": "entity_text",
           "llm_api_key": VICTIM_KEY if name == "victim" else None,
           "kbs": [{"name": "UMLS", "endpoint_url": "https://kb.example.com/query",
                    "headers": json.dumps({"Authorization": f"Bearer {VICTIM_TOKEN}"})}]}
    cfg.update(extra)
    (d / "session.json").write_text(json.dumps(cfg, indent=2))
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Local mode by default; tests opt into hosted with the `hosted` fixture."""
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    monkeypatch.delenv("HELIX_URL_ALLOWLIST", raising=False)
    _public_dns(monkeypatch)
    root = tmp_path / "app"
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    up = root / "up"
    up.mkdir()
    (root / "canary.txt").write_text("server file")
    (root / "annotated.csv").write_text("ROOT-SECRET\n")
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", sessions)
    import api.main as m
    monkeypatch.setattr(m, "_TMP", up)
    _write_session(sessions, "attacker")
    _write_session(sessions, "victim")
    return TestClient(m.app), sessions, root, up


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("HELIX_HOSTED", "1")


def _upload(up, name="in.csv"):
    f = up / f"abc123_{name}"
    f.write_text("entity_text,document_id\nlung,d1\n")
    return f


def _create_body(**kw):
    body = {"session_name": "new", "annotator": "a", "original_filename": "in.csv",
            "delimiter": ",", "label_column": "entity_text"}
    body.update(kw)
    return body


# ── C1: PUT cannot repoint the session ──────────────────────────────────────

@pytest.mark.parametrize("mode", ["local", "hosted"])
def test_put_input_path_is_ignored(env, monkeypatch, mode):
    """Port of the reviewer PoC: reading another session's session.json via rows/export."""
    if mode == "hosted":
        monkeypatch.setenv("HELIX_HOSTED", "1")
    c, sessions, root, up = env
    own_input = json.loads((sessions / "attacker" / "session.json").read_text())["input_path"]
    body = c.get("/api/sessions/attacker").json()
    body["input_path"] = str(sessions / "victim" / "session.json")
    body["delimiter"] = "\x01"   # one column per line
    body["label_column"] = "{"
    c.put("/api/sessions/attacker", json=body)
    stored = json.loads((sessions / "attacker" / "session.json").read_text())
    assert stored["input_path"] == own_input
    texts = [c.get(f"/api/sessions/attacker/rows/{i}").text for i in range(5)]
    texts.append(c.get("/api/sessions/attacker/export").text)
    for t in texts:
        assert VICTIM_TOKEN not in t and VICTIM_KEY not in t


def test_put_session_name_is_ignored(env):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["session_name"] = "victim"
    assert c.put("/api/sessions/attacker", json=body).status_code == 200
    stored = json.loads((sessions / "attacker" / "session.json").read_text())
    assert stored["session_name"] == "attacker"
    assert json.loads((sessions / "victim" / "session.json").read_text())["llm_api_key"] == VICTIM_KEY


def test_hosted_load_rejects_input_outside_session_dir(env, hosted):
    c, sessions, root, up = env
    _write_session(sessions, "attacker",
                   input_path=str(sessions / "victim" / "session.json"))
    r = c.get("/api/sessions/attacker/rows/0")
    assert r.status_code == 400
    assert VICTIM_TOKEN not in r.text


def test_hosted_load_rejects_context_folder_outside_context_dir(env, hosted):
    c, sessions, root, up = env
    _write_session(sessions, "attacker", context_folder=str(sessions / "attacker"),
                   doc_id_col="document_id")
    r = c.get("/api/sessions/attacker/rows/0")
    assert r.status_code == 400
    assert "Bearer" not in r.text


def test_local_load_allows_server_paths(env):
    c, sessions, root, up = env
    _write_session(sessions, "attacker",
                   input_path=str(sessions / "victim" / "input.csv"),
                   context_folder=str(root), doc_id_col="document_id")
    assert c.get("/api/sessions/attacker/rows/0").status_code == 200


def test_hosted_put_context_folder_must_be_context_subdir(env, hosted):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["context_folder"] = str(sessions / "attacker")   # doc_id "session" -> session.json
    body["doc_id_col"] = "document_id"
    assert c.put("/api/sessions/attacker", json=body).status_code == 400
    (sessions / "attacker" / "context").mkdir()
    body["context_folder"] = str(sessions / "attacker" / "context")
    assert c.put("/api/sessions/attacker", json=body).status_code == 200


# ── C2 / C5: create validates before any side effect ────────────────────────

@pytest.mark.parametrize("mode", ["local", "hosted"])
def test_create_tmp_path_outside_uploads_rejected(env, monkeypatch, mode):
    if mode == "hosted":
        monkeypatch.setenv("HELIX_HOSTED", "1")
    c, sessions, root, up = env
    victim_cfg = sessions / "victim" / "session.json"
    r = c.post("/api/sessions", json=_create_body(
        session_name="stolen", tmp_path=str(victim_cfg), delimiter="\x01", label_column="{"))
    assert r.status_code == 400
    assert victim_cfg.exists()
    assert not (sessions / "stolen").exists()


def test_create_tmp_path_must_be_direct_child_of_uploads(env):
    c, sessions, root, up = env
    (up / "sub").mkdir()
    f = up / "sub" / "x.csv"
    f.write_text("entity_text\nlung\n")
    for p in (f, up / ".." / "canary.txt", up / "missing.csv"):
        r = c.post("/api/sessions", json=_create_body(tmp_path=str(p)))
        assert r.status_code == 400, p
    assert f.exists() and (root / "canary.txt").exists()
    assert not (sessions / "new").exists()


def test_create_tmp_path_symlink_out_of_uploads_rejected(env):
    c, sessions, root, up = env
    link = up / "abc_link.csv"
    link.symlink_to(sessions / "victim" / "session.json")
    r = c.post("/api/sessions", json=_create_body(tmp_path=str(link)))
    assert r.status_code == 400
    assert (sessions / "victim" / "session.json").exists()


@pytest.mark.parametrize("bad", ["../outside_dir", "..", ".", "new\n", "a/b", ""])
def test_create_bad_session_name_has_no_side_effects(env, bad):
    c, sessions, root, up = env
    f = _upload(up)
    before = sorted(p.name for p in root.iterdir())
    r = c.post("/api/sessions", json=_create_body(session_name=bad, tmp_path=str(f)))
    assert r.status_code == 400
    assert f.exists()
    assert sorted(p.name for p in root.iterdir()) == before
    assert sorted(p.name for p in sessions.iterdir()) == ["attacker", "victim"]


@pytest.mark.parametrize("bad", ["", ".", "..", "x/..", "/"])
def test_create_bad_original_filename_rejected(env, bad):
    c, sessions, root, up = env
    f = _upload(up)
    r = c.post("/api/sessions", json=_create_body(tmp_path=str(f), original_filename=bad))
    assert r.status_code == 400
    assert f.exists() and not (sessions / "new").exists()


def test_create_original_filename_uses_basename(env):
    c, sessions, root, up = env
    f = _upload(up)
    victim_cfg = (sessions / "victim" / "session.json").read_text()
    r = c.post("/api/sessions", json=_create_body(
        tmp_path=str(f), original_filename="x/../../victim/session.json"))
    assert r.status_code == 200
    assert (sessions / "victim" / "session.json").read_text() == victim_cfg
    stored = json.loads((sessions / "new" / "session.json").read_text())
    assert stored["input_path"] == str(sessions / "new" / "input_session.json")
    assert not f.exists()


def test_hosted_create_existing_session_is_409(env, hosted):
    c, sessions, root, up = env
    f = _upload(up)
    victim_cfg = (sessions / "victim" / "session.json").read_text()
    r = c.post("/api/sessions", json=_create_body(session_name="victim", tmp_path=str(f)))
    assert r.status_code == 409
    assert (sessions / "victim" / "session.json").read_text() == victim_cfg
    assert f.exists()


def test_local_create_existing_session_still_overwrites(env):
    c, sessions, root, up = env
    f = _upload(up)
    r = c.post("/api/sessions", json=_create_body(session_name="victim", tmp_path=str(f)))
    assert r.status_code == 200
    assert json.loads((sessions / "victim" / "session.json").read_text())["llm_api_key"] is None


def test_create_invalid_config_has_no_side_effects(env):
    c, sessions, root, up = env
    f = _upload(up)
    r = c.post("/api/sessions", json=_create_body(tmp_path=str(f), annotator="  "))
    assert r.status_code == 400
    assert f.exists() and not (sessions / "new").exists()


def test_create_survives_cross_device_move(env, hosted, monkeypatch):
    """C5: /tmp and the sessions volume are different filesystems in Docker."""
    c, sessions, root, up = env
    f = _upload(up)
    real_rename = os.rename

    def exdev(src, dst, *a, **k):
        if str(src) == str(f):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *a, **k)

    monkeypatch.setattr(os, "rename", exdev)
    r = c.post("/api/sessions", json=_create_body(tmp_path=str(f)))
    assert r.status_code == 200
    assert (sessions / "new" / "input_in.csv").exists() and not f.exists()


# ── C3: path-param names are slugs ──────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/sessions/%2e%2e", "/api/sessions/%2E", "/api/sessions/."])
def test_delete_dot_names_404_and_nothing_deleted(env, path):
    c, sessions, root, up = env
    r = c.delete(path)
    # A literal "/." is normalised away by the client and never reaches the
    # route (405 on /api/sessions/); the encoded forms do reach it.
    assert r.status_code == (405 if path.endswith("/.") else 404)
    assert root.exists() and sessions.exists() and (root / "canary.txt").exists()
    assert (sessions / "victim" / "session.json").exists()


def test_export_dotdot_not_served(env):
    c, sessions, root, up = env
    r = c.get("/api/sessions/%2e%2e/export")
    assert r.status_code == 404
    assert "ROOT-SECRET" not in r.text


@pytest.mark.parametrize("route", ["", "/progress", "/rows/0", "/snapshots", "/reflection",
                                   "/metrics/export"])
def test_get_routes_reject_dotdot(env, route):
    c, sessions, root, up = env
    assert c.get(f"/api/sessions/%2e%2e{route}").status_code == 404


def test_existing_or_none_uses_fullmatch(env):
    from api.main import _existing_or_none
    assert _existing_or_none("victim") is not None
    assert _existing_or_none("victim\n") is None


# ── C4: LLM base URL guard and key binding ──────────────────────────────────

@pytest.mark.parametrize("change", [
    {"llm_base_url": "https://evil.example.com/v1"},
    {"llm_provider": "openai"},
])
def test_llm_key_dropped_when_destination_changes(env, change):
    c, sessions, root, up = env
    body = c.get("/api/sessions/victim").json()
    assert body["llm_api_key_set"] is True and body["llm_api_key"] == ""
    body.update(change)
    assert c.put("/api/sessions/victim", json=body).status_code == 200
    assert json.loads((sessions / "victim" / "session.json").read_text())["llm_api_key"] is None


def test_llm_key_kept_when_destination_unchanged(env):
    c, sessions, root, up = env
    body = c.get("/api/sessions/victim").json()
    body["llm_model"] = "other-model"
    assert c.put("/api/sessions/victim", json=body).status_code == 200
    assert json.loads((sessions / "victim" / "session.json").read_text())["llm_api_key"] == VICTIM_KEY


def test_merge_secrets_llm_binding():
    from eltool.config import SessionConfig
    from eltool.secrets import merge_secrets
    ex = SessionConfig(session_name="s", annotator="a", input_path="x", label_column="l",
                       llm_provider="openai", llm_base_url="https://a.example.com/v1",
                       llm_api_key="sk-1")
    same = {"llm_provider": "openai", "llm_base_url": "https://a.example.com/v1", "llm_api_key": ""}
    assert merge_secrets(same, ex)["llm_api_key"] == "sk-1"
    moved = {**same, "llm_base_url": "https://b.example.com/v1"}
    assert merge_secrets(moved, ex)["llm_api_key"] is None
    # an explicitly typed key is always kept
    assert merge_secrets({**moved, "llm_api_key": "sk-2"}, ex)["llm_api_key"] == "sk-2"


@pytest.mark.parametrize("url", ["https://127.0.0.1/v1", "http://api.example.com/v1",
                                 "https://100.100.100.200/v1"])
def test_hosted_put_private_llm_base_url_rejected(env, hosted, url):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["llm_provider"] = "openai"
    body["llm_base_url"] = url
    r = c.put("/api/sessions/attacker", json=body)
    assert r.status_code == 400 and "llm_base_url" in r.json()["detail"]


def test_hosted_create_private_llm_base_url_rejected(env, hosted):
    c, sessions, root, up = env
    f = _upload(up)
    r = c.post("/api/sessions", json=_create_body(
        tmp_path=str(f), llm_provider="openai", llm_base_url="https://169.254.169.254/"))
    assert r.status_code == 400 and "llm_base_url" in r.json()["detail"]
    assert f.exists() and not (sessions / "new").exists()


def test_local_put_loopback_llm_base_url_allowed(env):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["llm_provider"] = "openai"
    body["llm_base_url"] = "http://127.0.0.1:11434/v1"
    assert c.put("/api/sessions/attacker", json=body).status_code == 200


def _fake_kb(monkeypatch, exc=None):
    from eltool.kb_client import Candidate

    def query(self, label):
        from eltool.hosting import check_outbound_url
        check_outbound_url(self.cfg.endpoint_url)
        if exc:
            raise exc
        return [Candidate(id="C1", label="lung", score=0.9)]

    monkeypatch.setattr("api.main.KBClient.query", query)


def test_hosted_reranker_private_base_url_is_llm_error_not_500(env, hosted, monkeypatch):
    c, sessions, root, up = env
    _write_session(sessions, "attacker", enable_llm_rerank=True, llm_provider="openai",
                   llm_base_url="https://10.0.0.1/v1", llm_api_key="sk-a")
    _fake_kb(monkeypatch)

    def boom(*a, **k):
        raise AssertionError("must not contact the LLM")

    monkeypatch.setattr("eltool.llm_reranker.LLMReranker._request", boom)  # every provider call goes through it
    r = c.post("/api/sessions/attacker/candidates",
               json={"label": "lung", "kb_name": "UMLS", "enable_lexical": False})
    assert r.status_code == 200
    body = r.json()
    assert body["dense_candidates"] and body["llm"]["error"]
    assert "sk-a" not in r.text


def test_openai_client_does_not_follow_redirects():
    pytest.importorskip("openai")
    from eltool.llm_reranker import LLMReranker
    rr = LLMReranker(provider="openai", api_key="sk-x", base_url="https://api.example.com/v1")
    client = rr._client_lazy()
    assert client._client.follow_redirects is False


# ── I3: hosted URL validation at create / PUT, and /candidates errors ───────

def test_hosted_put_private_kb_rejected(env, hosted):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["kbs"][0]["endpoint_url"] = "https://192.168.1.10/query"
    r = c.put("/api/sessions/attacker", json=body)
    assert r.status_code == 400
    assert "kbs[0].endpoint_url" in r.json()["detail"]


def test_hosted_put_unencrypted_graph_rejected(env, hosted):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["graph"] = {"uri": "bolt://db.example.com:7687", "user": "neo4j", "password": "pw"}
    r = c.put("/api/sessions/attacker", json=body)
    assert r.status_code == 400 and "graph.uri" in r.json()["detail"]
    assert "pw" not in r.json()["detail"]


def test_hosted_create_private_kb_rejected(env, hosted):
    c, sessions, root, up = env
    f = _upload(up)
    r = c.post("/api/sessions", json=_create_body(
        tmp_path=str(f), kbs=[{"name": "K", "endpoint_url": "https://127.0.0.1:8765/query"}]))
    assert r.status_code == 400 and "kbs[0].endpoint_url" in r.json()["detail"]
    assert f.exists() and not (sessions / "new").exists()


def test_local_put_loopback_kb_allowed(env):
    c, sessions, root, up = env
    body = c.get("/api/sessions/attacker").json()
    body["kbs"][0]["endpoint_url"] = "http://127.0.0.1:8765/query"
    assert c.put("/api/sessions/attacker", json=body).status_code == 200


def test_hosted_candidates_reports_blocked_kb(env, hosted, monkeypatch):
    c, sessions, root, up = env
    _write_session(sessions, "attacker", kbs=[
        {"name": "UMLS", "endpoint_url": "https://10.1.2.3/query",
         "headers": json.dumps({"Authorization": "Bearer ATTACKERTOKEN"})}])
    _fake_kb(monkeypatch)
    r = c.post("/api/sessions/attacker/candidates",
               json={"label": "lung", "kb_name": "UMLS"})
    assert r.status_code == 200
    body = r.json()
    assert body["dense_candidates"] == [] and body["error"]
    assert "ATTACKERTOKEN" not in r.text
    # AI runners decide "retry, never record an answer" from retrieval_errors alone:
    # a blocked KB must show up there, not read as "nothing retrieved".
    assert body["retrieval_errors"]["dense"] and body["retrieval_errors"]["lexical"]
    assert "ATTACKERTOKEN" not in str(body["retrieval_errors"])


def test_candidates_error_is_none_when_ok(env, monkeypatch):
    c, sessions, root, up = env
    _fake_kb(monkeypatch)
    body = c.post("/api/sessions/attacker/candidates",
                  json={"label": "lung", "kb_name": "UMLS", "enable_lexical": False}).json()
    assert body["dense_candidates"] and body["error"] is None
    assert body["retrieval_errors"] == {"dense": None, "lexical": None}
