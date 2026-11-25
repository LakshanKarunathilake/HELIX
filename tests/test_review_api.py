"""Reviewer endpoints: review, agreement, adjudication and the final export."""
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
    pd.DataFrame({"entity_text": ["cold", "=flu", "virus"],
                  "document_id": ["d1", "d1", "d2"]}).to_csv(inp, index=False)
    cfg = {
        "session_name": name,
        "annotator": "lakshan",
        "input_path": str(inp),
        "delimiter": ",",
        "label_column": "entity_text",
        "doc_id_col": "document_id",
        "kbs": [{"name": kb, "endpoint_url": "http://127.0.0.1:8765/query"} for kb in kbs],
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


@pytest.fixture
def world(auth_env, make_user):
    _make_session(auth_env, "s1")
    admin = make_user("root@x.org", "admin", name="Root")
    rev = make_user("rev@x.org", "reviewer", name="Rev")
    alice = make_user("alice@x.org", name="Alice")
    bob = make_user("bob@x.org", name="Bob")
    for u in (rev, alice, bob):
        userdb.add_member("s1", u.id)
    return {"root": auth_env, "admin": admin, "rev": rev, "alice": alice, "bob": bob}


@pytest.fixture
def as_user(login_as):
    from api.main import app

    def _as(user):
        return login_as(TestClient(app), user)
    return _as


def _save(client, value, idx=0, name="s1", **extra):
    r = client.post(f"/api/sessions/{name}/rows/{idx}",
                    json={"annotations": {"UMLS": value}, **extra})
    assert r.status_code == 200, r.text


def _script(user, idx, value, actor="auto_match", name="s1"):
    """Log a script-written decision the way auto-match / auto-annotate do."""
    with decisions.transaction() as tx:
        tx.add(name, idx, user.id, actor, "batch", {"UMLS": value}, {"UMLS": ""})


def _final_csv(client, name="s1"):
    r = client.get(f"/api/sessions/{name}/export?final=1")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(r.text)))


def test_review_lists_every_users_decision_with_script_tag(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(alice), "C1", llm_used="anthropic/claude")
    _script(bob, 0, "C2")
    _save(as_user(bob), "C3", idx=2)
    r = as_user(world["rev"]).get("/api/sessions/s1/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kb_names"] == ["UMLS"]
    assert [row["idx"] for row in body["rows"]] == [0, 2]      # row 1 has no decisions
    row0 = body["rows"][0]
    assert row0["label"] == "cold"
    assert row0["adjudication"] is None
    got = {d["user_id"]: d for d in row0["decisions"]}
    assert set(got) == {alice.id, bob.id}
    assert (got[alice.id]["user_name"], got[alice.id]["actor"], got[alice.id]["script"],
            got[alice.id]["kb_values"], got[alice.id]["llm_used"]) == \
        ("Alice", "human", False, {"UMLS": "C1"}, "anthropic/claude")
    assert (got[bob.id]["user_name"], got[bob.id]["actor"], got[bob.id]["script"],
            got[bob.id]["kb_values"]) == ("Bob", "auto_match", True, {"UMLS": "C2"})
    assert got[alice.id]["ts"]
    assert body["rows"][1]["label"] == "virus"


def test_disagree_ignores_script_rows(world, as_user):
    _save(as_user(world["alice"]), "C1")
    _script(world["bob"], 0, "C2")
    (row,) = as_user(world["rev"]).get("/api/sessions/s1/review").json()["rows"]
    assert row["disagree"] is False
    _save(as_user(world["bob"]), "C2")            # now a human decision that differs
    (row,) = as_user(world["rev"]).get("/api/sessions/s1/review").json()["rows"]
    assert row["disagree"] is True


def test_agreement_excludes_script_rows_and_reports_count(world, as_user):
    alice, bob = world["alice"], world["bob"]
    ca, cb = as_user(alice), as_user(bob)
    _save(ca, "C1", idx=0)
    _save(cb, "C1", idx=0)
    _save(ca, "C2", idx=1)
    _save(cb, "C9", idx=1)
    _save(ca, "C3", idx=2)
    _script(bob, 2, "C3")
    r = as_user(world["admin"]).get("/api/sessions/s1/agreement")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["excluded_script_rows"] == 1
    (pair,) = body["by_kb"]["UMLS"]
    assert (pair["a"], pair["b"], pair["n"], pair["percent"]) == \
        (min(alice.id, bob.id), max(alice.id, bob.id), 2, 50.0)


def test_script_row_counts_once_human_saves_it(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(bob), "C1")
    _script(alice, 0, "C1")
    body = as_user(world["rev"]).get("/api/sessions/s1/agreement").json()
    assert body["excluded_script_rows"] == 1
    assert body["by_kb"]["UMLS"] == []            # alice has no human decision yet
    _save(as_user(alice), "C1")                    # alice confirms the script value
    body = as_user(world["rev"]).get("/api/sessions/s1/agreement").json()
    assert body["excluded_script_rows"] == 0
    assert (body["by_kb"]["UMLS"][0]["n"], body["by_kb"]["UMLS"][0]["percent"]) == (1, 100.0)


def test_adjudication_is_append_only_latest_wins(world, as_user):
    rev, admin = world["rev"], world["admin"]
    _save(as_user(world["alice"]), "C1")
    r = as_user(rev).post("/api/sessions/s1/adjudications",
                          json={"row_idx": 0, "kb_values": {"UMLS": "C1"}, "note": "first"})
    assert r.status_code == 200, r.text
    assert (r.json()["kb_values"], r.json()["note"], r.json()["reviewer_id"]) == \
        ({"UMLS": "C1"}, "first", rev.id)
    r = as_user(admin).post("/api/sessions/s1/adjudications",
                            json={"row_idx": 0, "kb_values": {"UMLS": "C7"}})
    assert r.status_code == 200, r.text
    conn = userdb.connect()
    try:
        rows = conn.execute("SELECT reviewer_id, kb_values, note FROM adjudications "
                            "ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [(x["reviewer_id"], json.loads(x["kb_values"]), x["note"]) for x in rows] == [
        (rev.id, {"UMLS": "C1"}, "first"), (admin.id, {"UMLS": "C7"}, "")]
    assert decisions.current_adjudications("s1")[0]["kb_values"] == {"UMLS": "C7"}
    (row,) = as_user(rev).get("/api/sessions/s1/review").json()["rows"]
    adj = row["adjudication"]
    assert (adj["kb_values"], adj["note"], adj["reviewer_id"], adj["reviewer_name"]) == \
        ({"UMLS": "C7"}, "", admin.id, "Root")
    assert adj["ts"]
    # an adjudicated row with no decisions still shows in review
    as_user(rev).post("/api/sessions/s1/adjudications",
                      json={"row_idx": 1, "kb_values": {"UMLS": "C5"}})
    rows = as_user(rev).get("/api/sessions/s1/review").json()["rows"]
    assert [(x["idx"], x["decisions"]) for x in rows][1] == (1, [])
    from api.main import app
    paths = [(r.path, sorted(r.methods)) for r in app.routes if "adjudication" in r.path]
    assert paths == [("/api/sessions/{name}/adjudications", ["POST"])]


def test_adjudication_validates_row_and_kb_names(world, as_user):
    c = as_user(world["rev"])
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": 3, "kb_values": {"UMLS": "C1"}}).status_code == 404
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": -1, "kb_values": {"UMLS": "C1"}}).status_code == 404
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": 0, "kb_values": {"MeSH": "D1"}}).status_code == 400
    assert decisions.current_adjudications("s1") == {}


def test_final_export_blank_when_only_one_human(world, as_user):
    _save(as_user(world["alice"]), "C1")
    _script(world["bob"], 1, "C2")
    rows = _final_csv(as_user(world["rev"]))
    assert list(rows[0].keys()) == ["entity_text", "document_id", "UMLS",
                                    "_needs_review", "_review_reason"]
    assert [(r["UMLS"], r["_needs_review"], r["_review_reason"]) for r in rows] == [
        ("", "1", "single_annotator"), ("", "1", "script_only"), ("", "1", "no_decisions")]
    assert rows[1]["entity_text"] == "'=flu"       # formula injection neutralised


def test_final_export_value_when_two_humans_match(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    _save(ca, "C1", idx=0)
    _save(cb, "C1", idx=0)
    _save(ca, "C2", idx=2)
    _save(cb, "C3", idx=2)
    r = as_user(world["rev"]).get("/api/sessions/s1/export?final=1")
    assert 'filename="s1_final.csv"' in r.headers["content-disposition"]
    rows = _final_csv(as_user(world["rev"]))
    assert [(r["entity_text"], r["UMLS"], r["_needs_review"], r["_review_reason"])
            for r in rows] == [("cold", "C1", "", ""), ("'=flu", "", "1", "no_decisions"),
                               ("virus", "", "1", "disagree")]


def test_final_export_uses_adjudication(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    _save(ca, "C2", idx=2)
    _save(cb, "C3", idx=2)
    rev = as_user(world["rev"])
    assert rev.post("/api/sessions/s1/adjudications",
                    json={"row_idx": 2, "kb_values": {"UMLS": "C3"}}).status_code == 200
    rows = _final_csv(rev)
    assert (rows[2]["UMLS"], rows[2]["_needs_review"], rows[2]["_review_reason"]) == \
        ("C3", "", "")


def test_final_export_rejects_user_or_stage(world, as_user):
    c = as_user(world["admin"])
    assert c.get(f"/api/sessions/s1/export?final=1&user={world['alice'].id}").status_code == 400
    assert c.get("/api/sessions/s1/export?final=1&stage=graph").status_code == 400


def test_annotator_gets_403_on_review_agreement_adjudications_final_export(world, as_user):
    c = as_user(world["alice"])
    assert c.get("/api/sessions/s1/review").status_code == 403
    assert c.get("/api/sessions/s1/agreement").status_code == 403
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": 0, "kb_values": {"UMLS": "C1"}}).status_code == 403
    assert c.get("/api/sessions/s1/export?final=1").status_code == 403
    assert decisions.current_adjudications("s1") == {}


def test_non_member_reviewer_gets_404(world, as_user, make_user):
    outsider = make_user("out@x.org", "reviewer", name="Out")
    c = as_user(outsider)
    assert c.get("/api/sessions/s1/review").status_code == 404
    assert c.get("/api/sessions/s1/agreement").status_code == 404
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": 0, "kb_values": {"UMLS": "C1"}}).status_code == 404
    assert c.get("/api/sessions/s1/export?final=1").status_code == 404
    # an annotator who is not a member gets 404 too (membership is checked first)
    stranger = make_user("str@x.org", name="Str")
    assert as_user(stranger).get("/api/sessions/s1/review").status_code == 404


def test_removed_member_decisions_still_counted(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(alice), "C1")
    _save(as_user(bob), "C1")
    userdb.remove_member("s1", bob.id)
    rev = as_user(world["rev"])
    (row,) = rev.get("/api/sessions/s1/review").json()["rows"]
    assert {d["user_name"] for d in row["decisions"]} == {"Alice", "Bob"}
    assert rev.get("/api/sessions/s1/agreement").json()["by_kb"]["UMLS"][0]["n"] == 1
    assert _final_csv(rev)[0]["UMLS"] == "C1"


def test_review_routes_are_404_with_auth_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path, "s1")
    from api.main import app
    c = TestClient(app)
    assert c.get("/api/sessions/s1/review").status_code == 404
    assert c.get("/api/sessions/s1/agreement").status_code == 404
    assert c.post("/api/sessions/s1/adjudications",
                  json={"row_idx": 0, "kb_values": {"UMLS": "C1"}}).status_code == 404
    assert c.get("/api/sessions/s1/export?final=1").status_code == 404
    assert not (tmp_path / "helix.db").exists()
