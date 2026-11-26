"""Decision-log integrity: script saves are never human (C1), and every decision
holds the row's complete state across the session's KBs (I1)."""
from __future__ import annotations

import csv
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import decisions, userdb


def _make_session(root, name: str, kbs=("UMLS",)) -> None:
    sdir = root / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["cold", "flu", "virus"],
                  "document_id": ["d1", "d1", "d2"]}).to_csv(inp, index=False)
    (sdir / "session.json").write_text(json.dumps({
        "session_name": name, "annotator": "lakshan", "input_path": str(inp),
        "delimiter": ",", "label_column": "entity_text", "doc_id_col": "document_id",
        "kbs": [{"name": kb, "endpoint_url": "http://127.0.0.1:8765/query"} for kb in kbs],
    }))


@pytest.fixture
def world(auth_env, make_user):
    _make_session(auth_env, "s1")
    _make_session(auth_env, "s2", kbs=("UMLS", "MESH"))
    admin = make_user("root@x.org", "admin", name="Root")
    rev = make_user("rev@x.org", "reviewer", name="Rev")
    alice = make_user("alice@x.org", name="Alice")
    bob = make_user("bob@x.org", name="Bob")
    for s in ("s1", "s2"):
        for u in (rev, alice, bob):
            userdb.add_member(s, u.id)
    return {"root": auth_env, "admin": admin, "rev": rev, "alice": alice, "bob": bob}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user):
        return login_as(TestClient(app), user)
    return _as


def _post(client, name, idx, body):
    r = client.post(f"/api/sessions/{name}/rows/{idx}", json=body)
    assert r.status_code == 200, r.text


def _modal_save(client, name, idx, annotations):
    """Exactly what AutoAnnotateModal step 1 sends: api.saveRow(name, idx, newAnn,
    undefined, undefined, {auto: true}) -> JSON {annotations, auto: true}
    (an undefined llm_used is dropped by JSON.stringify)."""
    _post(client, name, idx, {"annotations": annotations, "auto": True})


def _final(client, name):
    r = client.get(f"/api/sessions/{name}/export?final=1")
    assert r.status_code == 200, r.text
    return list(csv.DictReader(io.StringIO(r.text)))


def _review_rows(client, name):
    r = client.get(f"/api/sessions/{name}/review")
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def _confirm(client, name, idx, values):
    """The reviewer settles one row with ``values``, which must follow from the answers."""
    row = next(r for r in _review_rows(client, name) if r["idx"] == idx)
    r = client.post(f"/api/sessions/{name}/adjudications", json={
        "row_idx": idx, "kb_values": values, "version": row["version"]})
    assert r.status_code == 200, r.text


# ── C1: the Auto-annotate modal's saves are script decisions ─────────────────

def test_modal_save_is_logged_as_auto_annotate(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _modal_save(as_user(alice), "s1", 0, {"UMLS": "C1"})
    _modal_save(as_user(bob), "s1", 0, {"UMLS": "C1"})
    log = decisions.list_decisions("s1")
    assert [(d.user_id, d.actor, d.action, d.kb_values) for d in log] == [
        (alice.id, "auto_annotate", "auto", {"UMLS": "C1"}),
        (bob.id, "auto_annotate", "auto", {"UMLS": "C1"})]
    latest = decisions.latest_human_or_script("s1")
    assert {k: d.actor for k, d in latest.items()} == {
        (alice.id, 0): "auto_annotate", (bob.id, 0): "auto_annotate"}

    rev = as_user(world["rev"])
    body = rev.get("/api/sessions/s1/agreement").json()
    assert body["excluded_script_rows"] == 2
    assert body["by_kb"]["UMLS"] == []
    review = rev.get("/api/sessions/s1/review").json()
    assert review["rows"] == [] and review["counts"] == {
        "to_decide": 0, "agreed": 0, "decided": 0, "waiting": 3}
    # two users' identical modal picks are NOT published as an agreed value
    first = _final(rev, "s1")[0]
    assert (first["UMLS"], first["_needs_review"], first["_review_reason"]) == \
        ("", "1", "script_only")
    # the layer still holds the value (the modal's write happened)
    layer = pd.read_csv(world["root"] / "s1" / "annotations" / f"{alice.id}.csv",
                        dtype=str, keep_default_na=False)
    assert layer.at[0, "UMLS"] == "C1"


def test_human_save_still_logged_as_human(world, as_user):
    alice = world["alice"]
    _post(as_user(alice), "s1", 0, {"annotations": {"UMLS": "C1"}})
    _post(as_user(alice), "s1", 1, {"annotations": {"UMLS": "C2"}, "auto": False})
    assert [(d.actor, d.action) for d in decisions.list_decisions("s1")] == [
        ("human", "save"), ("human", "save")]


def test_human_confirming_a_modal_pick_counts(world, as_user):
    alice, bob = world["alice"], world["bob"]
    for u in (alice, bob):
        _modal_save(as_user(u), "s1", 0, {"UMLS": "C1"})
        _post(as_user(u), "s1", 0, {"annotations": {"UMLS": "C1"}})
    rev = as_user(world["rev"])
    assert rev.get("/api/sessions/s1/agreement").json()["excluded_script_rows"] == 0
    first = _final(rev, "s1")[0]
    assert (first["UMLS"], first["_needs_review"], first["_review_reason"]) == \
        ("", "1", "awaiting_review")
    _confirm(rev, "s1", 0, {"UMLS": "C1"})
    first = _final(rev, "s1")[0]
    assert (first["UMLS"], first["_basis"], first["_needs_review"]) == ("C1", "accepted", "")


def test_auto_flag_with_auth_off_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path, "s1")
    from api.main import app
    _modal_save(TestClient(app), "s1", 0, {"UMLS": "C1"})
    out = pd.read_csv(tmp_path / "s1" / "annotated.csv", dtype=str, keep_default_na=False)
    assert out.at[0, "UMLS"] == "C1"
    assert not (tmp_path / "helix.db").exists()


# ── I1: a decision holds the full row state ──────────────────────────────────

def test_correcting_one_kb_keeps_the_other_in_the_log(world, as_user):
    """The reviewer's probe: both save {UMLS:C1, MESH:D1}, then both correct UMLS only."""
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    for c in (ca, cb):
        _post(c, "s2", 0, {"annotations": {"UMLS": "C1", "MESH": "D1"}})
    for c in (ca, cb):
        _post(c, "s2", 0, {"annotations": {"UMLS": "C2"}})
    alice_log = decisions.list_decisions("s2", user_id=world["alice"].id)
    assert [(d.kb_values, d.previous) for d in alice_log] == [
        ({"UMLS": "C1", "MESH": "D1"}, {"UMLS": "", "MESH": ""}),
        ({"UMLS": "C2", "MESH": "D1"}, {"UMLS": "C1", "MESH": "D1"})]

    rev = as_user(world["rev"])
    (row,) = _review_rows(rev, "s2")
    assert row["status"] == "agreed"
    assert [a["kb_ids"] for a in row["answers"]] == [{"UMLS": ["C2"], "MESH": ["D1"]}] * 2
    first = _final(rev, "s2")[0]
    assert (first["UMLS"], first["MESH"], first["_needs_review"], first["_review_reason"]) == \
        ("", "", "1", "awaiting_review")
    _confirm(rev, "s2", 0, {"UMLS": "C2", "MESH": "D1"})
    first = _final(rev, "s2")[0]
    assert (first["UMLS"], first["MESH"], first["_needs_review"], first["_review_reason"]) == \
        ("C2", "D1", "", "")
    by_kb = rev.get("/api/sessions/s2/agreement").json()["by_kb"]
    assert [(p["n"], p["percent"]) for p in by_kb["MESH"]] == [(1, 100.0)]
    assert [(p["n"], p["percent"]) for p in by_kb["UMLS"]] == [(1, 100.0)]


def test_one_user_resaving_one_kb_is_not_a_false_disagreement(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    for c in (ca, cb):
        _post(c, "s2", 0, {"annotations": {"UMLS": "C1", "MESH": "D1"}})
    _post(ca, "s2", 0, {"annotations": {"UMLS": "C1"}})     # e.g. Document review
    rev = as_user(world["rev"])
    (row,) = _review_rows(rev, "s2")
    assert row["status"] == "agreed"
    _confirm(rev, "s2", 0, {"UMLS": "C1", "MESH": "D1"})
    first = _final(rev, "s2")[0]
    assert (first["UMLS"], first["MESH"], first["_needs_review"]) == ("C1", "D1", "")


def test_batch_logs_full_row_state(world, as_user):
    alice = world["alice"]
    ca = as_user(alice)
    _post(ca, "s2", 0, {"annotations": {"UMLS": "C1", "MESH": "D1"}})
    r = ca.post("/api/sessions/s2/rows/batch", json={"updates": [
        {"idx": 0, "annotations": {"UMLS": "C9"}, "llm_used": ""},
        {"idx": 1, "annotations": {"UMLS": "C5"}, "llm_used": ""}]})
    assert r.status_code == 200, r.text
    batch = [d for d in decisions.list_decisions("s2") if d.action == "batch"]
    assert [(d.row_idx, d.actor, d.kb_values, d.previous) for d in batch] == [
        (0, "auto_match", {"UMLS": "C9", "MESH": "D1"}, {"UMLS": "C1", "MESH": "D1"}),
        (1, "auto_match", {"UMLS": "C5", "MESH": ""}, {"UMLS": "", "MESH": ""})]
