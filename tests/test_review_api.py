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
from eltool.adjudication import row_state

STALE_MSG = "The answers for this row changed since you opened it. Check it again."
WAITING_MSG = "No annotator has answered this row yet."


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


def _review(client, name="s1"):
    r = client.get(f"/api/sessions/{name}/review")
    assert r.status_code == 200, r.text
    return r.json()


def _row(client, idx, name="s1"):
    return next(r for r in _review(client, name)["rows"] if r["idx"] == idx)


def _decide(client, idx, values, note="", name="s1", version=None):
    version = version or _row(client, idx, name)["version"]
    return client.post(f"/api/sessions/{name}/adjudications", json={
        "row_idx": idx, "kb_values": values, "note": note, "version": version})


def _bulk(client, rows, name="s1"):
    r = client.post(f"/api/sessions/{name}/adjudications/bulk", json={
        "rows": [{"row_idx": idx, "version": version} for idx, version in rows]})
    assert r.status_code == 200, r.text
    return r.json()


def test_review_lists_every_annotators_answer_and_leaves_out_script_rows(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(bob), "C3", idx=0)
    _save(as_user(alice), "C2|C1")
    _script(bob, 1, "C2")
    _save(as_user(bob), "C3", idx=2)
    body = _review(as_user(world["rev"]))
    assert (body["kb_names"], body["total"], body["has_context"]) == (["UMLS"], 3, False)
    assert body["counts"] == {"to_decide": 2, "agreed": 0, "decided": 0, "waiting": 1}
    assert [row["idx"] for row in body["rows"]] == [0, 2]      # row 1 is script-only
    row0, row2 = body["rows"]
    assert set(row0) == {"idx", "label", "doc_id", "status", "version", "pending",
                         "bulk_eligible", "answers", "options", "decision"}
    assert (row0["label"], row0["doc_id"], row0["status"], row0["pending"],
            row0["bulk_eligible"], row0["decision"]) == \
        ("cold", "d1", "disagree", 0, False, None)
    assert row0["version"]
    # ordered by name, not by who saved first
    assert [(a["user_id"], a["user_name"], a["kb_ids"], a["complete"])
            for a in row0["answers"]] == [
        (alice.id, "Alice", {"UMLS": ["C1", "C2"]}, True),
        (bob.id, "Bob", {"UMLS": ["C3"]}, True)]
    assert all(a["ts"] for a in row0["answers"])
    assert row0["options"] == {"UMLS": [
        {"id": "C1", "user_ids": [alice.id], "preticked": False},
        {"id": "C2", "user_ids": [alice.id], "preticked": False},
        {"id": "C3", "user_ids": [bob.id], "preticked": False}]}
    assert (row2["label"], row2["doc_id"], row2["status"], row2["pending"]) == \
        ("virus", "d2", "single", 1)


def test_disagree_ignores_script_rows(world, as_user):
    _save(as_user(world["alice"]), "C1")
    _script(world["bob"], 0, "C2")
    row = _row(as_user(world["rev"]), 0)
    assert row["status"] == "single"
    assert [a["user_name"] for a in row["answers"]] == ["Alice"]
    _save(as_user(world["bob"]), "C2")            # now a human decision that differs
    assert _row(as_user(world["rev"]), 0)["status"] == "disagree"


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
    _save(as_user(world["bob"]), "C7")
    r = _decide(as_user(rev), 0, {"UMLS": "C1"}, note="first")
    assert r.status_code == 200, r.text
    assert (r.json()["kb_values"], r.json()["note"], r.json()["reviewer_id"]) == \
        ({"UMLS": "C1"}, "first", rev.id)
    r = _decide(as_user(admin), 0, {"UMLS": "C7"})
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
    (row,) = _review(as_user(rev))["rows"]
    adj = row["decision"]
    assert (adj["kb_values"], adj["note"], adj["reviewer_id"], adj["reviewer_name"]) == \
        ({"UMLS": "C7"}, "", admin.id, "Root")
    assert (adj["row_idx"], adj["kb_ids"], adj["basis"], adj["source_users"]) == \
        (0, {"UMLS": ["C7"]}, "accepted", [world["bob"].id])
    assert adj["ts"]
    # a row with no answers cannot be adjudicated
    waiting = row_state({}, None, ["UMLS"], []).version
    r = _decide(as_user(rev), 1, {"UMLS": "C5"}, note="n", version=waiting)
    assert (r.status_code, r.json()["detail"]) == (409, WAITING_MSG)
    assert _adjudication_count() == 2
    from api.main import app
    paths = sorted((r.path, sorted(r.methods)) for r in app.routes if "adjudication" in r.path)
    assert paths == [("/api/sessions/{name}/adjudications", ["POST"]),
                     ("/api/sessions/{name}/adjudications/bulk", ["POST"])]


def test_adjudication_validates_row_and_kb_names(world, as_user):
    c = as_user(world["rev"])
    for idx, kb_values, status in ((3, {"UMLS": "C1"}, 404), (-1, {"UMLS": "C1"}, 404),
                                   (0, {"MeSH": "D1"}, 400)):
        r = c.post("/api/sessions/s1/adjudications",
                   json={"row_idx": idx, "kb_values": kb_values, "version": "x"})
        assert r.status_code == status, (idx, kb_values, r.text)
    assert decisions.current_adjudications("s1") == {}


def test_final_export_blank_when_only_one_human(world, as_user):
    _save(as_user(world["alice"]), "C1")
    _script(world["bob"], 1, "C2")
    rows = _final_csv(as_user(world["rev"]))
    assert list(rows[0].keys()) == ["entity_text", "document_id", "UMLS", "_basis",
                                    "_decided_by", "_needs_review", "_review_reason"]
    assert [(r["UMLS"], r["_basis"], r["_decided_by"], r["_needs_review"],
             r["_review_reason"]) for r in rows] == [
        ("", "", "", "1", "single_annotator"), ("", "", "", "1", "script_only"),
        ("", "", "", "1", "no_decisions")]
    assert rows[1]["entity_text"] == "'=flu"       # formula injection neutralised


def test_final_export_value_only_after_a_reviewer_confirms_a_match(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    _save(ca, "C1", idx=0)
    _save(cb, "C1", idx=0)
    _save(ca, "C2", idx=2)
    _save(cb, "C3", idx=2)
    rev = as_user(world["rev"])
    r = rev.get("/api/sessions/s1/export?final=1")
    assert 'filename="s1_final.csv"' in r.headers["content-disposition"]

    def summary():
        return [(r["entity_text"], r["UMLS"], r["_needs_review"], r["_review_reason"])
                for r in _final_csv(rev)]
    assert summary() == [("cold", "", "1", "awaiting_review"),
                         ("'=flu", "", "1", "no_decisions"), ("virus", "", "1", "disagree")]
    assert _decide(rev, 0, {"UMLS": "C1"}).status_code == 200
    assert summary() == [("cold", "C1", "", ""), ("'=flu", "", "1", "no_decisions"),
                         ("virus", "", "1", "disagree")]


def test_final_export_uses_adjudication(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    _save(ca, "C2", idx=2)
    _save(cb, "C3", idx=2)
    rev = as_user(world["rev"])
    assert _decide(rev, 2, {"UMLS": "C3"}).status_code == 200
    rows = _final_csv(rev)
    assert (rows[2]["UMLS"], rows[2]["_needs_review"], rows[2]["_review_reason"]) == \
        ("C3", "", "")


def test_final_export_rejects_user_or_stage(world, as_user):
    c = as_user(world["admin"])
    assert c.get(f"/api/sessions/s1/export?final=1&user={world['alice'].id}").status_code == 400
    assert c.get("/api/sessions/s1/export?final=1&stage=graph").status_code == 400


REVIEW_GETS = ("review", "review/summary", "review/rows/0", "agreement", "export?final=1")
REVIEW_POSTS = (
    ("adjudications", {"row_idx": 0, "kb_values": {"UMLS": "C1"}, "version": "x"}),
    ("adjudications/bulk", {"rows": [{"row_idx": 0, "version": "x"}]}))


def _review_statuses(client):
    got = {path: client.get(f"/api/sessions/s1/{path}").status_code for path in REVIEW_GETS}
    got.update({path: client.post(f"/api/sessions/s1/{path}", json=body).status_code
                for path, body in REVIEW_POSTS})
    return got


def _all_statuses(status):
    return dict.fromkeys(REVIEW_GETS + tuple(p for p, _ in REVIEW_POSTS), status)


def test_annotator_gets_403_on_review_agreement_adjudications_final_export(world, as_user):
    assert _review_statuses(as_user(world["alice"])) == _all_statuses(403)
    assert decisions.current_adjudications("s1") == {}


def test_non_member_reviewer_gets_404(world, as_user, make_user):
    outsider = make_user("out@x.org", "reviewer", name="Out")
    assert _review_statuses(as_user(outsider)) == _all_statuses(404)
    # an annotator who is not a member gets 404 too (membership is checked first)
    stranger = make_user("str@x.org", name="Str")
    assert _review_statuses(as_user(stranger)) == _all_statuses(404)
    assert decisions.current_adjudications("s1") == {}


def test_removed_member_decisions_still_counted(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(alice), "C1")
    _save(as_user(bob), "C1")
    userdb.remove_member("s1", bob.id)
    rev = as_user(world["rev"])
    (row,) = _review(rev)["rows"]
    assert {a["user_name"] for a in row["answers"]} == {"Alice", "Bob"}
    assert (row["status"], row["pending"], row["bulk_eligible"]) == ("agreed", 0, True)
    assert rev.get("/api/sessions/s1/agreement").json()["by_kb"]["UMLS"][0]["n"] == 1
    assert _final_csv(rev)[0]["_review_reason"] == "awaiting_review"
    r = _decide(rev, 0, {"UMLS": "C1"})
    assert r.json()["source_users"] == sorted([alice.id, bob.id])
    assert _final_csv(rev)[0]["UMLS"] == "C1"


def test_only_active_annotator_members_are_pending(world, as_user):
    _save(as_user(world["alice"]), "C1")
    rev = as_user(world["rev"])
    assert _row(rev, 0)["pending"] == 1           # bob; not the reviewer
    userdb.set_active(world["bob"].id, False)
    assert _row(rev, 0)["pending"] == 0


@pytest.fixture
def two_kb(world):
    _make_session(world["root"], "s2", kbs=("UMLS", "MeSH"))
    for u in (world["rev"], world["alice"], world["bob"]):
        userdb.add_member("s2", u.id)
    return world


def _save_kbs(client, annotations, idx=0, name="s2"):
    r = client.post(f"/api/sessions/{name}/rows/{idx}", json={"annotations": annotations})
    assert r.status_code == 200, r.text


def _adjudication_count():
    conn = userdb.connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM adjudications").fetchone()[0]
    finally:
        conn.close()


def test_partial_or_empty_adjudication_is_rejected_and_not_stored(two_kb, as_user):
    c = as_user(two_kb["rev"])
    for kb_values, problem in (
            ({"UMLS": "C9"}, "missing KB: MeSH"),
            ({}, "missing KB: UMLS, MeSH"),
            ({"UMLS": "C9", "MeSH": "D1", "X": "x"}, "unknown KB: X")):
        r = c.post("/api/sessions/s2/adjudications",
                   json={"row_idx": 0, "kb_values": kb_values, "version": "x"})
        assert r.status_code == 400
        assert problem in r.json()["detail"]
    assert _adjudication_count() == 0


def test_full_two_kb_adjudication_shows_in_review_and_final_export(two_kb, as_user):
    ca, cb = as_user(two_kb["alice"]), as_user(two_kb["bob"])
    _save_kbs(ca, {"UMLS": "C1", "MeSH": "D1"})
    _save_kbs(cb, {"UMLS": "C1", "MeSH": "D1"})
    rev = as_user(two_kb["rev"])
    r = _decide(rev, 0, {"UMLS": "C9", "MeSH": "D9"}, note="both wrong", name="s2")
    assert r.status_code == 200, r.text
    (row,) = _review(rev, "s2")["rows"]
    assert row["decision"]["kb_values"] == {"UMLS": "C9", "MeSH": "D9"}
    rows = _final_csv(rev, "s2")
    assert (rows[0]["UMLS"], rows[0]["MeSH"], rows[0]["_basis"], rows[0]["_needs_review"],
            rows[0]["_review_reason"]) == ("C9", "D9", "override", "", "")


def test_final_export_two_kbs_agree_on_both(two_kb, as_user):
    ca, cb = as_user(two_kb["alice"]), as_user(two_kb["bob"])
    _save_kbs(ca, {"UMLS": "C1", "MeSH": "D1"})
    _save_kbs(cb, {"UMLS": "C1", "MeSH": "D1"})
    rev = as_user(two_kb["rev"])
    rows = _final_csv(rev, "s2")
    assert list(rows[0].keys()) == ["entity_text", "document_id", "UMLS", "MeSH", "_basis",
                                    "_decided_by", "_needs_review", "_review_reason"]
    assert (rows[0]["UMLS"], rows[0]["MeSH"], rows[0]["_needs_review"],
            rows[0]["_review_reason"]) == ("", "", "1", "awaiting_review")
    assert _decide(rev, 0, {"UMLS": "C1", "MeSH": "D1"}, name="s2").status_code == 200
    rows = _final_csv(rev, "s2")
    assert (rows[0]["UMLS"], rows[0]["MeSH"], rows[0]["_needs_review"],
            rows[0]["_review_reason"]) == ("C1", "D1", "", "")


def test_final_export_two_kbs_differ_on_one_kb_blanks_both(two_kb, as_user):
    ca, cb = as_user(two_kb["alice"]), as_user(two_kb["bob"])
    _save_kbs(ca, {"UMLS": "C1", "MeSH": "D1"})
    _save_kbs(cb, {"UMLS": "C1", "MeSH": "D2"})
    rows = _final_csv(as_user(two_kb["rev"]), "s2")
    assert (rows[0]["UMLS"], rows[0]["MeSH"], rows[0]["_needs_review"],
            rows[0]["_review_reason"]) == ("", "", "1", "disagree")


def test_review_label_blank_when_label_column_missing(world, as_user):
    _save(as_user(world["alice"]), "C1")
    pd.DataFrame({"other": ["a", "b", "c"]}).to_csv(world["root"] / "s1" / "input.csv",
                                                     index=False)
    r = as_user(world["rev"]).get("/api/sessions/s1/review")
    assert r.status_code == 200, r.text
    assert [row["label"] for row in r.json()["rows"]] == [""]


def test_review_routes_are_404_with_auth_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HELIX_AUTH", raising=False)
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    _make_session(tmp_path, "s1")
    from api.main import app
    assert _review_statuses(TestClient(app)) == _all_statuses(404)
    assert not (tmp_path / "helix.db").exists()


# ── answers as sets, basis and sources ───────────────────────────────────────

def test_selection_order_is_agreed(world, as_user):
    _save(as_user(world["alice"]), "C1|C2")
    _save(as_user(world["bob"]), "C2|C1")
    rev = as_user(world["rev"])
    row = _row(rev, 0)
    assert (row["status"], row["bulk_eligible"]) == ("agreed", True)
    assert [o["preticked"] for o in row["options"]["UMLS"]] == [True, True]
    assert rev.get("/api/sessions/s1/agreement").json()["by_kb"]["UMLS"][0]["percent"] == 100.0


def test_agreement_pairs_report_overlap(world, as_user):
    _save(as_user(world["alice"]), "C1|C2")
    _save(as_user(world["bob"]), "C1")
    (pair,) = as_user(world["rev"]).get("/api/sessions/s1/agreement").json()["by_kb"]["UMLS"]
    assert (pair["percent"], pair["overlap"]) == (0.0, 50.0)


def test_decide_records_basis_and_sources(world, as_user):
    alice, bob = world["alice"], world["bob"]
    _save(as_user(alice), "C1")
    _save(as_user(bob), "C2")
    rev = as_user(world["rev"])
    r = _decide(rev, 0, {"UMLS": "C2|C1"})
    assert r.status_code == 200, r.text
    assert (r.json()["basis"], r.json()["source_users"], r.json()["kb_values"]) == \
        ("combined", sorted([alice.id, bob.id]), {"UMLS": "C1|C2"})
    assert set(r.json()) == {"row_idx", "kb_values", "kb_ids", "note", "basis",
                             "source_users", "reviewer_id", "reviewer_name", "ts"}
    assert (r.json()["kb_ids"], r.json()["reviewer_name"]) == ({"UMLS": ["C1", "C2"]}, "Rev")
    assert decisions.current_adjudications("s1")[0]["based_on"] == {
        str(alice.id): {"UMLS": "C1"}, str(bob.id): {"UMLS": "C2"}}
    assert _row(rev, 0)["status"] == "decided"
    assert _decide(rev, 0, {"UMLS": "C2"}).json()["basis"] == "accepted"


def test_override_needs_a_note(world, as_user):
    _save(as_user(world["alice"]), "C1")
    rev = as_user(world["rev"])
    for note in ("", "   "):
        r = _decide(rev, 0, {"UMLS": "C9"}, note=note)
        assert (r.status_code, r.json()["detail"]) == (
            400, "Add a note: this answer is an override (it includes a concept no "
                 "annotator chose).")
    assert decisions.current_adjudications("s1") == {}
    assert _decide(rev, 0, {"UMLS": "C9"}, note="wrong sense").json()["basis"] == "override"


def test_browser_cannot_set_basis(world, as_user):
    alice = world["alice"]
    _save(as_user(alice), "C1")
    rev = as_user(world["rev"])
    body = {"row_idx": 0, "kb_values": {"UMLS": "C9"}, "version": _row(rev, 0)["version"],
            "basis": "accepted", "source_users": [alice.id]}
    r = rev.post("/api/sessions/s1/adjudications", json=body)
    assert r.status_code == 400 and "Add a note" in r.json()["detail"]
    assert _adjudication_count() == 0
    r = rev.post("/api/sessions/s1/adjudications", json={**body, "note": "mine"})
    assert (r.json()["basis"], r.json()["source_users"]) == ("override", [])


def test_nil_with_a_concept_and_pipe_only_values_are_rejected(two_kb, as_user):
    _save_kbs(as_user(two_kb["alice"]), {"UMLS": "C1", "MeSH": "D1"})
    rev = as_user(two_kb["rev"])
    r = _decide(rev, 0, {"UMLS": "NIL|C1", "MeSH": "D1"}, note="n", name="s2")
    assert (r.status_code, r.json()["detail"]) == (
        400, "NIL cannot be combined with a concept (UMLS).")
    r = _decide(rev, 0, {"UMLS": "NIL|C1", "MeSH": "D1|nil"}, note="n", name="s2")
    assert r.json()["detail"] == "NIL cannot be combined with a concept (UMLS, MeSH)."
    r = _decide(rev, 0, {"UMLS": "|", "MeSH": "D1"}, note="n", name="s2")
    assert (r.status_code, r.json()["detail"]) == (
        400, "Enter a value for every KB (blank: UMLS); use NIL for no match.")
    assert _adjudication_count() == 0


def test_checks_run_in_order(world, as_user):
    rev = as_user(world["rev"])
    # a bad row before bad values; bad values before a stale version; stale before waiting
    for idx, value, status, text in (
            (9, "|", 404, "Row out of range"),
            (0, "|", 400, "Enter a value"),
            (0, "NIL|C1", 400, "NIL cannot be combined"),
            (0, "C1", 409, STALE_MSG)):
        r = _decide(rev, idx, {"UMLS": value}, version="stale")
        assert r.status_code == status and text in r.json()["detail"], (idx, value, r.text)
    _save(as_user(world["alice"]), "C1")
    r = _decide(rev, 0, {"UMLS": "C9"}, version="stale")      # stale before override
    assert (r.status_code, r.json()["detail"]) == (409, STALE_MSG)
    assert _adjudication_count() == 0


# ── nothing is decided on an out-of-date screen ──────────────────────────────

def test_stale_screen_is_refused(world, as_user):
    alice = as_user(world["alice"])
    _save(alice, "C1")
    rev = as_user(world["rev"])
    version = _row(rev, 0)["version"]
    _save(alice, "C2")
    r = _decide(rev, 0, {"UMLS": "C1"}, note="n", version=version)
    assert (r.status_code, r.json()["detail"]) == (409, STALE_MSG)
    assert _adjudication_count() == 0
    # the same ids saved in another order leave the screen current
    _save(alice, "C2|C3")
    version = _row(rev, 0)["version"]
    _save(alice, "C3|C2")
    r = _decide(rev, 0, {"UMLS": "C2|C3"}, version=version)
    assert r.status_code == 200, r.text


def test_second_reviewer_with_old_screen_is_refused(world, as_user):
    _save(as_user(world["alice"]), "C1")
    rev, admin = as_user(world["rev"]), as_user(world["admin"])
    first, second = _row(rev, 0)["version"], _row(admin, 0)["version"]
    assert first == second
    assert _decide(rev, 0, {"UMLS": "C1"}, version=first).status_code == 200
    r = _decide(admin, 0, {"UMLS": "C1"}, version=second)
    assert (r.status_code, r.json()["detail"]) == (409, STALE_MSG)
    assert _adjudication_count() == 1


def test_waiting_row_cannot_be_decided(world, as_user):
    _script(world["bob"], 1, "C2")
    rev = as_user(world["rev"])
    body = _review(rev)
    assert body["rows"] == []
    assert body["counts"] == {"to_decide": 0, "agreed": 0, "decided": 0, "waiting": 3}
    version = row_state({}, None, ["UMLS"], []).version
    r = _decide(rev, 1, {"UMLS": "C2"}, note="n", version=version)
    assert (r.status_code, r.json()["detail"]) == (409, WAITING_MSG)
    assert _adjudication_count() == 0


def test_answer_change_marks_decided_row_changed(world, as_user):
    alice = as_user(world["alice"])
    _save(alice, "C1")
    rev = as_user(world["rev"])
    assert _decide(rev, 0, {"UMLS": "C1"}).status_code == 200
    _save(alice, "C2")
    body = _review(rev)
    (row,) = body["rows"]
    assert (row["status"], row["decision"]["kb_values"]) == ("changed", {"UMLS": "C1"})
    assert (body["counts"]["to_decide"], body["counts"]["decided"]) == (1, 0)
    first = _final_csv(rev)[0]
    assert (first["UMLS"], first["_basis"], first["_decided_by"], first["_needs_review"],
            first["_review_reason"]) == ("", "", "", "1", "changed_since_review")


def test_blanked_answer_leaves_a_decided_row_undecidable(world, as_user):
    alice = as_user(world["alice"])
    _save(alice, "C1")
    rev = as_user(world["rev"])
    assert _decide(rev, 0, {"UMLS": "C1"}).status_code == 200
    _save(alice, "")                              # the only annotator blanks the answer
    row = _row(rev, 0)
    assert (row["status"], row["answers"]) == ("changed", [])
    # with a note, so that only the no-answer check can refuse it
    r = _decide(rev, 0, {"UMLS": "C1"}, note="still right", version=row["version"])
    assert (r.status_code, r.json()["detail"]) == (409, WAITING_MSG)
    assert _adjudication_count() == 1
    first = _final_csv(rev)[0]
    assert (first["UMLS"], first["_needs_review"], first["_review_reason"]) == \
        ("", "1", "changed_since_review")


# ── bulk confirm ─────────────────────────────────────────────────────────────

def test_bulk_confirms_only_qualifying_rows(world, as_user, make_user):
    alice, bob = world["alice"], world["bob"]
    carol = make_user("carol@x.org", name="Carol")
    userdb.add_member("s1", carol.id)
    ca, cb, cc = as_user(alice), as_user(bob), as_user(carol)
    for c in (ca, cb, cc):
        _save(c, "C1", idx=0)
    _save(ca, "C1", idx=1)
    _save(cb, "C2", idx=1)
    _save(cc, "C1", idx=1)
    _save(ca, "C3", idx=2)
    _save(cb, "C3", idx=2)
    rev = as_user(world["rev"])
    assert _bulk(rev, [(0, "stale")]) == {
        "confirmed": [], "skipped": [{"row_idx": 0, "reason": "changed"}]}
    assert _adjudication_count() == 0
    rows = {r["idx"]: r for r in _review(rev)["rows"]}
    assert (rows[2]["status"], rows[2]["pending"], rows[2]["bulk_eligible"]) == \
        ("agreed", 1, False)
    ticked = [(i, rows[i]["version"]) for i in (0, 1, 2)]
    got = _bulk(rev, ticked + [(99, "x"), ticked[0]])      # a row sent twice counts once
    assert got == {"confirmed": [0], "skipped": [
        {"row_idx": 1, "reason": "not_agreed"}, {"row_idx": 2, "reason": "pending"},
        {"row_idx": 99, "reason": "out_of_range"}]}
    assert _adjudication_count() == 1
    adj = decisions.current_adjudications("s1")[0]
    users = sorted([alice.id, bob.id, carol.id])
    assert (adj["kb_values"], adj["basis"], adj["source_users"], adj["note"],
            adj["reviewer_id"]) == ({"UMLS": "C1"}, "bulk_agreed", users, "", world["rev"].id)
    assert adj["based_on"] == {str(u): {"UMLS": "C1"} for u in users}
    row0 = _row(rev, 0)
    assert (row0["status"], row0["decision"]["basis"]) == ("decided", "bulk_agreed")


def test_bulk_skips_a_row_changed_since_it_was_ticked(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    for c in (ca, cb):
        _save(c, "C1")
    rev = as_user(world["rev"])
    version = _row(rev, 0)["version"]
    for c in (ca, cb):
        _save(c, "C2")
    assert _bulk(rev, [(0, version)]) == {
        "confirmed": [], "skipped": [{"row_idx": 0, "reason": "changed"}]}
    assert _adjudication_count() == 0


# ── final export ─────────────────────────────────────────────────────────────

def test_final_export_only_decided_rows_have_values(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    for c in (ca, cb):
        _save(c, "C1", idx=0)
        _save(c, "C2", idx=2)
    rev = as_user(world["rev"])
    assert _decide(rev, 0, {"UMLS": "C1"}).status_code == 200
    rows = _final_csv(rev)
    assert list(rows[0].keys()) == ["entity_text", "document_id", "UMLS", "_basis",
                                    "_decided_by", "_needs_review", "_review_reason"]
    assert [(r["UMLS"], r["_basis"], r["_decided_by"], r["_needs_review"],
             r["_review_reason"]) for r in rows] == [
        ("C1", "accepted", "Rev", "", ""),
        ("", "", "", "1", "no_decisions"),
        ("", "", "", "1", "awaiting_review")]


def test_final_export_keeps_minus_one_and_guards_reviewer_name(world, as_user, make_user):
    cmd = make_user("cmd@x.org", "reviewer", name="=cmd")
    userdb.add_member("s1", cmd.id)
    _save(as_user(world["alice"]), "-1")
    c = as_user(cmd)
    assert _decide(c, 0, {"UMLS": "-1"}).status_code == 200
    first = _final_csv(c)[0]
    assert (first["UMLS"], first["_decided_by"]) == ("-1", "'=cmd")


def test_final_export_marks_earlier_decisions_legacy(world, as_user):
    decisions.add_adjudication("s1", 0, world["rev"].id, {"UMLS": "C1"}, "old")
    rev = as_user(world["rev"])
    row = _row(rev, 0)
    assert (row["status"], row["decision"]["basis"], row["decision"]["source_users"]) == \
        ("decided", "legacy", [])
    first = _final_csv(rev)[0]
    assert (first["UMLS"], first["_basis"], first["_decided_by"]) == ("C1", "legacy", "Rev")


def test_final_export_of_an_exported_file_has_no_duplicate_columns(world, as_user):
    pd.DataFrame({"entity_text": ["cold"], "UMLS": ["old"], "_basis": ["override"],
                  "_decided_by": ["x"], "_needs_review": [""], "_review_reason": [""]}
                 ).to_csv(world["root"] / "s1" / "input.csv", index=False)
    r = as_user(world["rev"]).get("/api/sessions/s1/export?final=1")
    header, row = list(csv.reader(io.StringIO(r.text)))
    assert header == ["entity_text", "UMLS", "_basis", "_decided_by", "_needs_review",
                      "_review_reason"]
    assert row == ["cold", "", "", "", "1", "no_decisions"]


# ── reading rows ─────────────────────────────────────────────────────────────

def test_row_context_is_readable_and_writes_nothing(world, as_user):
    sdir = world["root"] / "s1"
    ctx = world["root"] / "ctx"
    ctx.mkdir()
    (ctx / "d1.txt").write_text("A cold day.")
    rev = as_user(world["rev"])
    assert _review(rev)["has_context"] is False
    r = rev.get("/api/sessions/s1/review/rows/0")
    assert r.json() == {"idx": 0, "label": "cold", "doc_id": "d1", "info": {},
                        "context_text": None, "entity_span": None}
    cfg = json.loads((sdir / "session.json").read_text())
    cfg.update(context_folder=str(ctx), info_columns=["document_id"], span_col="document_id")
    (sdir / "session.json").write_text(json.dumps(cfg))
    assert _review(rev)["has_context"] is True
    r = rev.get("/api/sessions/s1/review/rows/0")
    assert r.status_code == 200, r.text
    assert r.json() == {"idx": 0, "label": "cold", "doc_id": "d1",
                        "info": {"document_id": "d1"}, "context_text": "A cold day.",
                        "entity_span": "d1"}
    assert rev.get("/api/sessions/s1/review/rows/2").json()["context_text"] is None
    for idx in (3, -1):
        r = rev.get(f"/api/sessions/s1/review/rows/{idx}")
        assert (r.status_code, r.json()["detail"]) == (404, "Row out of range")
    assert not (sdir / "annotations" / f"{world['rev'].id}.csv").exists()
    # without a doc_id column there is no document id and no context
    del cfg["doc_id_col"]
    (sdir / "session.json").write_text(json.dumps(cfg))
    assert _review(rev)["has_context"] is False
    r = rev.get("/api/sessions/s1/review/rows/0").json()
    assert (r["doc_id"], r["context_text"]) == ("", None)
    _save(as_user(world["alice"]), "C1")
    assert _row(rev, 0)["doc_id"] == ""


def test_summary_counts(world, as_user):
    ca, cb = as_user(world["alice"]), as_user(world["bob"])
    _save(ca, "C1", idx=0)
    _save(cb, "C2", idx=0)
    _save(ca, "C3", idx=2)
    _save(cb, "C3", idx=2)
    rev = as_user(world["rev"])
    r = rev.get("/api/sessions/s1/review/summary")
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 3, "to_decide": 1, "agreed": 1, "decided": 0, "waiting": 1}
    assert _decide(rev, 0, {"UMLS": "C1"}).status_code == 200
    assert rev.get("/api/sessions/s1/review/summary").json() == {
        "total": 3, "to_decide": 0, "agreed": 1, "decided": 1, "waiting": 1}
