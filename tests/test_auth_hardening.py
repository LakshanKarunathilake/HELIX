"""Auth-mode input validation and export hardening (I4, I5 server, I7, I8, stage names)."""
from __future__ import annotations

import csv
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import decisions, userdb

EVIL = '=HYPERLINK("http://evil","x")'


def _make_session(root, name: str, kbs=("UMLS", "MESH")) -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["cold", "flu"], "document_id": ["d1", "d1"]}).to_csv(
        inp, index=False)
    (sdir / "session.json").write_text(json.dumps({
        "session_name": name, "annotator": "lakshan", "input_path": str(inp),
        "delimiter": ",", "label_column": "entity_text", "doc_id_col": "document_id",
        "kbs": [{"name": kb, "endpoint_url": "http://127.0.0.1:8765/query"} for kb in kbs]}))


@pytest.fixture
def world(auth_env, make_user):
    _make_session(auth_env, "s1")
    _make_session(auth_env, "other")
    admin = make_user("root@x.org", "admin", name="Root")
    rev = make_user("rev@x.org", "reviewer", name="Rev")
    alice = make_user("alice@x.org", name="Alice")
    for u in (rev, alice):
        userdb.add_member("s1", u.id)
    return {"root": auth_env, "admin": admin, "rev": rev, "alice": alice}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user):
        return login_as(TestClient(app), user)
    return _as


def _layer_path(world, user, name="s1"):
    return world["root"] / name / "annotations" / f"{user.id}.csv"


# ── I4: only the session's KB names may be saved ─────────────────────────────

def test_unknown_annotation_keys_are_rejected_and_layer_untouched(world, as_user):
    alice = world["alice"]
    c = as_user(alice)
    assert c.post("/api/sessions/s1/rows/0",
                  json={"annotations": {"UMLS": "C1"}}).status_code == 200
    before = _layer_path(world, alice).read_bytes()
    for bad in ({"UMLS": "C1", "EVILCOL": "x", "annotator": "Bob"},
                {"_method": "auto_accept"}, {"_elapsed_ms": "1"}):
        r = c.post("/api/sessions/s1/rows/1", json={"annotations": bad})
        assert r.status_code == 400, r.text
        for key in bad:
            if key != "UMLS":
                assert key in r.json()["detail"]
    assert _layer_path(world, alice).read_bytes() == before
    assert len(decisions.list_decisions("s1")) == 1


def test_empty_annotations_are_rejected(world, as_user):
    c = as_user(world["alice"])
    r = c.post("/api/sessions/s1/rows/0", json={"annotations": {}})
    assert (r.status_code, r.json()["detail"]) == (400, "No annotations given.")
    r = c.post("/api/sessions/s1/rows/batch",
               json={"updates": [{"idx": 0, "annotations": {}}]})
    assert r.status_code == 400
    assert decisions.list_decisions("s1") == []


def test_batch_with_one_bad_update_writes_nothing(world, as_user):
    alice = world["alice"]
    r = as_user(alice).post("/api/sessions/s1/rows/batch", json={"updates": [
        {"idx": 0, "annotations": {"UMLS": "C1"}},
        {"idx": 1, "annotations": {"UMLS": "C2", "annotator": "Bob"}}]})
    assert r.status_code == 400
    assert "annotator" in r.json()["detail"]
    assert not _layer_path(world, alice).exists() or \
        pd.read_csv(_layer_path(world, alice), dtype=str,
                    keep_default_na=False)["UMLS"].tolist() == ["", ""]
    assert decisions.list_decisions("s1") == []


def test_auth_off_save_accepts_what_it_did_before(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path, "s1")
    from api.main import app
    c = TestClient(app)
    assert c.post("/api/sessions/s1/rows/0",
                  json={"annotations": {"UMLS": "C1", "EXTRA": "x"}}).status_code == 200
    assert c.post("/api/sessions/s1/rows/1", json={"annotations": {}}).status_code == 200
    assert c.post("/api/sessions/s1/rows/batch", json={"updates": [
        {"idx": 1, "annotations": {"OTHER": "y"}}]}).status_code == 200
    out = pd.read_csv(tmp_path / "s1" / "annotated.csv", dtype=str, keep_default_na=False)
    assert (out.at[0, "UMLS"], out.at[0, "EXTRA"], out.at[1, "OTHER"]) == ("C1", "x", "y")
    # the export is still the file's bytes, unguarded
    out.at[0, "UMLS"] = EVIL
    out.to_csv(tmp_path / "s1" / "annotated.csv", index=False)
    r = c.get("/api/sessions/s1/export")
    assert r.content == (tmp_path / "s1" / "annotated.csv").read_bytes()


def test_layer_exports_are_spreadsheet_guarded(world, as_user):
    alice, admin = world["alice"], world["admin"]
    ca = as_user(alice)
    assert ca.post("/api/sessions/s1/rows/0",
                   json={"annotations": {"UMLS": EVIL, "MESH": "D1"}}).status_code == 200
    assert ca.post("/api/sessions/s1/snapshot?stage=retrieval").status_code == 200
    for client, url in ((as_user(admin), f"/api/sessions/s1/export?user={alice.id}"),
                        (as_user(admin), f"/api/sessions/s1/export?user={alice.id}"
                                         "&stage=retrieval"),
                        (ca, "/api/sessions/s1/export")):
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert rows[0]["UMLS"] == "'" + EVIL, url
        assert (rows[0]["MESH"], rows[0]["entity_text"], rows[0]["annotator"]) == \
            ("D1", "cold", "Alice")


# ── I5 (server): blank adjudication values ───────────────────────────────────

@pytest.mark.parametrize("value", ["", "   "])
def test_blank_adjudication_value_is_rejected(world, as_user, value):
    r = as_user(world["rev"]).post("/api/sessions/s1/adjudications", json={
        "row_idx": 0, "kb_values": {"UMLS": "C1", "MESH": value}, "version": "x"})
    assert r.status_code == 400
    assert "MESH" in r.json()["detail"] and "NIL" in r.json()["detail"]
    assert decisions.current_adjudications("s1") == {}


def test_nil_adjudication_is_accepted(world, as_user):
    assert as_user(world["alice"]).post("/api/sessions/s1/rows/0", json={
        "annotations": {"UMLS": "C1", "MESH": "NIL"}}).status_code == 200
    rev = as_user(world["rev"])
    (row,) = rev.get("/api/sessions/s1/review").json()["rows"]
    r = rev.post("/api/sessions/s1/adjudications", json={
        "row_idx": 0, "kb_values": {"UMLS": "C1", "MESH": "NIL"}, "version": row["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["kb_values"] == {"UMLS": "C1", "MESH": "NIL"}


# ── I7: who reads the decision log ───────────────────────────────────────────

def test_decision_log_access_by_role(world, as_user, make_user):
    alice = world["alice"]
    assert as_user(alice).post("/api/sessions/s1/rows/0",
                               json={"annotations": {"UMLS": "C1"}}).status_code == 200
    stranger_rev = make_user("rev2@x.org", "reviewer", name="Rev2")
    for path in ("/api/admin/decisions", "/api/admin/decisions.csv"):
        admin = as_user(world["admin"])
        assert admin.get(path).status_code == 200
        assert admin.get(f"{path}?session=s1").status_code == 200
        assert admin.get(f"{path}?session=other").status_code == 200

        rev = as_user(world["rev"])
        r = rev.get(f"{path}?session=s1")
        assert r.status_code == 200, r.text
        assert rev.get(path).status_code == 403                    # must name a session
        assert rev.get(f"{path}?session=other").status_code == 404  # not a member
        assert rev.get(f"{path}?session=..").status_code == 404
        assert as_user(stranger_rev).get(f"{path}?session=s1").status_code == 404

        ann = as_user(alice)
        assert ann.get(f"{path}?session=s1").status_code == 403
        assert ann.get(path).status_code == 403
    got = as_user(world["rev"]).get("/api/admin/decisions?session=s1").json()
    assert [(d["user_id"], d["kb_values"]) for d in got] == [
        (alice.id, {"UMLS": "C1", "MESH": ""})]


# ── I8: the legacy annotated.csv, admin only ─────────────────────────────────

def test_legacy_export_is_admin_only_and_raw(world, as_user):
    legacy = world["root"] / "s1" / "annotated.csv"
    c = as_user(world["admin"])
    assert c.get("/api/sessions/s1/export?legacy=1").status_code == 404
    legacy.write_text(f"entity_text,UMLS\ncold,\"{EVIL.replace(chr(34), chr(34) * 2)}\"\n")
    r = c.get("/api/sessions/s1/export?legacy=1")
    assert r.status_code == 200, r.text
    assert r.content == legacy.read_bytes()              # as-is
    assert 'filename="s1_legacy_annotated.csv"' in r.headers["content-disposition"]
    for bad in (f"&user={world['alice'].id}", "&stage=retrieval", "&final=1"):
        assert c.get(f"/api/sessions/s1/export?legacy=1{bad}").status_code == 400, bad
    assert as_user(world["alice"]).get("/api/sessions/s1/export?legacy=1").status_code == 403
    assert as_user(world["rev"]).get("/api/sessions/s1/export?legacy=1").status_code == 403


# ── stage names are matched in full ──────────────────────────────────────────

def test_stage_name_with_trailing_newline_is_rejected(world, as_user):
    c = as_user(world["alice"])
    assert c.post("/api/sessions/s1/rows/0",
                  json={"annotations": {"UMLS": "C1"}}).status_code == 200
    assert c.post("/api/sessions/s1/snapshot", params={"stage": "retrieval\n"}).status_code == 400
    assert c.get("/api/sessions/s1/export", params={"stage": "retrieval\n"}).status_code == 400
    assert c.post("/api/sessions/s1/snapshot", params={"stage": "retrieval"}).status_code == 200
