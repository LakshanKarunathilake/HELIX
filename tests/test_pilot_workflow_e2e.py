"""End to end: the pilot workflow through the real API, two annotators, two KBs.

Step 1 the Auto-annotate modal's saves (auto: true), step 2 Auto-match through
rows/batch, then human saves; the log, agreement, review flags and the final
export must treat only the human saves as decisions.
"""
from __future__ import annotations

import csv
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from eltool import decisions, userdb

LABELS = ["cold", "flu", "virus", "fever", "cough", "rash"]


@pytest.fixture
def pilot(auth_env, make_user, login_as):
    sdir = auth_env / "pilot"
    sdir.mkdir()
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": LABELS,
                  "document_id": ["d1", "d1", "d2", "d2", "d3", "d3"]}).to_csv(inp, index=False)
    (sdir / "session.json").write_text(json.dumps({
        "session_name": "pilot", "annotator": "x", "input_path": str(inp), "delimiter": ",",
        "label_column": "entity_text", "doc_id_col": "document_id",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"},
                {"name": "MESH", "endpoint_url": "http://127.0.0.1:8766/query"}]}))
    rev = make_user("rev@x.org", "reviewer", name="Rev")
    alice = make_user("alice@x.org", name="Alice")
    bob = make_user("bob@x.org", name="Bob")
    for u in (rev, alice, bob):
        userdb.add_member("pilot", u.id)
    from api.main import app

    def client(user):
        return login_as(TestClient(app), user)
    return {"rev": rev, "alice": alice, "bob": bob, "client": client}


def _ok(r):
    assert r.status_code == 200, r.text
    return r


def test_pilot_workflow_two_annotators_two_kbs(pilot):
    alice, bob, rev = pilot["alice"], pilot["bob"], pilot["rev"]
    ca, cb, cr = pilot["client"](alice), pilot["client"](bob), pilot["client"](rev)

    # Step 1: the modal's retrieval picks (what AutoAnnotateModal sends: the row's
    # current annotations plus the picks, with blanks where nothing was found).
    retrieval = {0: {"UMLS": "C0", "MESH": "D0"}, 1: {"UMLS": "C1", "MESH": "D1"},
                 2: {"UMLS": "C2", "MESH": "D2"}, 3: {"UMLS": "C3", "MESH": "D3"},
                 4: {"UMLS": "C4", "MESH": ""}}
    for c in (ca, cb):
        for idx, ann in retrieval.items():
            _ok(c.post(f"/api/sessions/pilot/rows/{idx}", json={"annotations": ann, "auto": True}))
    # Step 2: Auto-match (graph) on the first KB, one atomic batch per user
    for c in (ca, cb):
        _ok(c.post("/api/sessions/pilot/rows/batch", json={"updates": [
            {"idx": 0, "annotations": {"UMLS": "G0"}, "llm_used": ""},
            {"idx": 1, "annotations": {"UMLS": "G1"}, "llm_used": ""}]}))
    # Step 3: human saves
    _ok(ca.post("/api/sessions/pilot/rows/0", json={"annotations": {"UMLS": "G0", "MESH": "D0"}}))
    _ok(cb.post("/api/sessions/pilot/rows/0", json={"annotations": {"UMLS": "G0", "MESH": "D0"}}))
    _ok(ca.post("/api/sessions/pilot/rows/1", json={"annotations": {"UMLS": "G1", "MESH": "D1"}}))
    _ok(cb.post("/api/sessions/pilot/rows/1", json={"annotations": {"UMLS": "C1", "MESH": "D1"}}))
    # both correct only UMLS on row 2: MESH keeps D2 in each layer and in the log
    _ok(ca.post("/api/sessions/pilot/rows/2", json={"annotations": {"UMLS": "X2"}}))
    _ok(cb.post("/api/sessions/pilot/rows/2", json={"annotations": {"UMLS": "X2"}}))
    _ok(ca.post("/api/sessions/pilot/rows/3", json={"annotations": {"UMLS": "C3", "MESH": "D3"}}))

    # ── the log ──
    for user in (alice, bob):
        seq = [(d.row_idx, d.actor, d.action) for d in decisions.list_decisions("pilot", user.id)]
        assert seq[:5] == [(i, "auto_annotate", "auto") for i in range(5)]
        assert seq[5:7] == [(0, "auto_match", "batch"), (1, "auto_match", "batch")]
        assert {(actor, action) for _, actor, action in seq[7:]} == {("human", "save")}
    assert [d.row_idx for d in decisions.list_decisions("pilot", alice.id)][7:] == [0, 1, 2, 3]
    assert [d.row_idx for d in decisions.list_decisions("pilot", bob.id)][7:] == [0, 1, 2]
    row2 = [d for d in decisions.list_decisions("pilot", alice.id) if d.row_idx == 2]
    assert [(d.actor, d.kb_values, d.previous) for d in row2] == [
        ("auto_annotate", {"UMLS": "C2", "MESH": "D2"}, {"UMLS": "", "MESH": ""}),
        ("human", {"UMLS": "X2", "MESH": "D2"}, {"UMLS": "C2", "MESH": "D2"})]
    latest = decisions.latest_human_or_script("pilot")
    assert {k: d.actor for k, d in latest.items()} == {
        **{(alice.id, i): "human" for i in range(4)}, (alice.id, 4): "auto_annotate",
        **{(bob.id, i): "human" for i in range(3)},
        (bob.id, 3): "auto_annotate", (bob.id, 4): "auto_annotate"}

    # ── agreement: script rows excluded; shared human rows are 0, 1, 2 ──
    agreement = _ok(cr.get("/api/sessions/pilot/agreement")).json()
    assert agreement["excluded_script_rows"] == 3          # bob row 3, both on row 4
    a, b = sorted((alice.id, bob.id))
    (umls,) = agreement["by_kb"]["UMLS"]
    (mesh,) = agreement["by_kb"]["MESH"]
    assert (umls["a"], umls["b"], umls["n"]) == (a, b, 3)
    assert umls["percent"] == pytest.approx(200 / 3)
    assert (mesh["n"], mesh["percent"]) == (3, 100.0)

    # ── review flags ──
    rows = {r["idx"]: r for r in _ok(cr.get("/api/sessions/pilot/review")).json()["rows"]}
    assert sorted(rows) == [0, 1, 2, 3, 4]
    assert {i: r["disagree"] for i, r in rows.items()} == {
        0: False, 1: True, 2: False, 3: False, 4: False}
    assert all(d["script"] for d in rows[4]["decisions"])
    assert {d["user_id"]: d["script"] for d in rows[3]["decisions"]} == {
        alice.id: False, bob.id: True}

    # ── final export ──
    r = _ok(cr.get("/api/sessions/pilot/export?final=1"))
    final = list(csv.DictReader(io.StringIO(r.text)))
    assert [(x["entity_text"], x["UMLS"], x["MESH"], x["_needs_review"], x["_review_reason"])
            for x in final] == [
        ("cold", "G0", "D0", "", ""),
        ("flu", "", "", "1", "disagree"),
        ("virus", "X2", "D2", "", ""),
        ("fever", "", "", "1", "single_annotator"),
        ("cough", "", "", "1", "script_only"),
        ("rash", "", "", "1", "no_decisions")]
