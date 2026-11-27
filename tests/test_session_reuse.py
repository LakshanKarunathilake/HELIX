"""A deleted session's name carries nothing over: no adjudications, no invite memberships (I2)."""
from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from eltool import decisions, idtoken, invites, userdb


def _make_session(root, name: str) -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["cold", "flu"]}).to_csv(inp, index=False)
    (sdir / "session.json").write_text(json.dumps({
        "session_name": name, "annotator": "x", "input_path": str(inp), "delimiter": ",",
        "label_column": "entity_text",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}]}))


@pytest.fixture
def admin_client(auth_env, make_user, login_as):
    from api.main import app
    admin = make_user("root@x.org", "admin", name="Root")
    return admin, login_as(TestClient(app), admin)


def _sign_in(monkeypatch, email, uid):
    from api.main import app
    monkeypatch.setattr(idtoken, "verify_id_token", lambda token: {
        "uid": uid, "email": email, "email_verified": True, "name": "N",
        "provider": "google.com"})
    return TestClient(app).post("/api/auth/session", json={"id_token": "t"})


def test_delete_refused_when_only_adjudications_exist(auth_env, admin_client):
    admin, c = admin_client
    _make_session(auth_env, "s1")
    decisions.add_adjudication("s1", 0, admin.id, {"UMLS": "C1"})
    assert not decisions.has_decisions("s1") and decisions.has_adjudications("s1")
    r = c.delete("/api/sessions/s1")
    assert r.status_code == 409
    assert r.json()["detail"] == "This session has adjudications and cannot be deleted"
    assert (auth_env / "s1").exists()
    assert not decisions.has_adjudications("other")


def test_delete_strips_the_name_from_every_invite(auth_env, admin_client, make_user):
    admin, c = admin_client
    _make_session(auth_env, "s1")
    _make_session(auth_env, "keep")
    pending, _ = invites.create_invite("new@x.org", "annotator", ["s1", "keep"], admin.id)
    old = make_user("old@x.org")
    accepted, registered = invites.create_invite("old@x.org", "annotator", ["s1"], admin.id)
    assert registered
    assert c.delete("/api/sessions/s1").status_code == 200
    got = {i.id: (i.session_names, i.status) for i in invites.list_invites()}
    assert got[pending.id] == (["keep"], "pending")
    assert got[accepted.id] == ([], "accepted")
    assert userdb.sessions_for(old.id) == []


def test_recreated_name_does_not_inherit_a_pending_invite(auth_env, admin_client, monkeypatch):
    admin, c = admin_client
    _make_session(auth_env, "s1")
    invites.create_invite("new@x.org", "annotator", ["s1"], admin.id)
    assert c.delete("/api/sessions/s1").status_code == 200
    _make_session(auth_env, "s1")                     # same name, a different session
    r = _sign_in(monkeypatch, "new@x.org", "uid-new")
    assert r.status_code == 200, r.text
    assert userdb.sessions_for(r.json()["id"]) == []
    assert userdb.members("s1") == []


def test_apply_invite_skips_sessions_that_no_longer_exist(auth_env, admin_client, monkeypatch):
    admin, _ = admin_client
    _make_session(auth_env, "gone")
    _make_session(auth_env, "here")
    invites.create_invite("new@x.org", "reviewer", ["gone", "here"], admin.id)
    shutil.rmtree(auth_env / "gone")                  # removed outside the API
    r = _sign_in(monkeypatch, "new@x.org", "uid-new")
    assert r.status_code == 200, r.text
    assert userdb.sessions_for(r.json()["id"]) == ["here"]
