"""Per-decision feedback (Annotation Guide Sec. 8): hint + confidence after each
save, the passive panel log, and the end-of-session reflection."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.feedback import HINTS, format_panels


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    from api.main import app

    return TestClient(app)


def _make_session(tmp_path, name: str, **cfg_extra) -> None:
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung", "DM"], "document_id": ["d1", "d2"]}).to_csv(
        inp, index=False
    )
    cfg = {
        "session_name": name,
        "annotator": "ann1",
        "input_path": str(inp),
        "delimiter": ",",
        "label_column": "entity_text",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}],
        **cfg_extra,
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


def _saved(tmp_path, name):
    return pd.read_csv(tmp_path / name / "annotated.csv", dtype=str, keep_default_na=False)


def test_format_panels_drops_unknown_and_records_none():
    assert format_panels(["graph", "definition", "graph", "bogus"]) == "definition;graph"
    assert format_panels([]) == "none"
    assert format_panels(None) == "none"


def test_feedback_round_trip(client, tmp_path):
    _make_session(tmp_path, "fb", study_condition="C3", collect_feedback=True)
    client.post("/api/sessions/fb/rows/0",
                json={"annotations": {"UMLS": "C1"}, "top1_id": "C1",
                      "panels": ["graph", "lookup"]})

    assert client.get("/api/sessions/fb/rows/0").json()["feedback"] is None

    r = client.post("/api/sessions/fb/rows/0/feedback",
                    json={"hint": "graph_links", "confidence": "fairly_sure"})
    assert r.status_code == 200

    saved = _saved(tmp_path, "fb")
    assert saved.at[0, "_panels"] == "graph;lookup"
    assert saved.at[0, "_hint"] == "graph_links"
    assert saved.at[0, "_confidence"] == "fairly_sure"
    assert saved.at[0, "_method"] == "auto_accept"   # provenance still written alongside
    assert client.get("/api/sessions/fb/rows/0").json()["feedback"] == {
        "hint": "graph_links", "confidence": "fairly_sure"}


def test_panels_are_frozen_on_first_save(client, tmp_path):
    """Prev/Next re-saves must not overwrite what was opened for the decision."""
    _make_session(tmp_path, "fb", collect_feedback=True)
    client.post("/api/sessions/fb/rows/0", json={"annotations": {"UMLS": "C1"}, "panels": []})
    client.post("/api/sessions/fb/rows/0",
                json={"annotations": {"UMLS": "C2"}, "panels": ["definition"]})
    assert _saved(tmp_path, "fb").at[0, "_panels"] == "none"


def test_feedback_requires_a_saved_annotation(client, tmp_path):
    _make_session(tmp_path, "fb", collect_feedback=True)
    r = client.post("/api/sessions/fb/rows/1/feedback",
                    json={"hint": "guess", "confidence": "unsure"})
    assert r.status_code == 409


def test_feedback_rejects_unknown_codes(client, tmp_path):
    _make_session(tmp_path, "fb", collect_feedback=True)
    client.post("/api/sessions/fb/rows/0", json={"annotations": {"UMLS": "C1"}})
    assert client.post("/api/sessions/fb/rows/0/feedback",
                       json={"hint": "vibes", "confidence": "sure"}).status_code == 422
    assert client.post("/api/sessions/fb/rows/0/feedback",
                       json={"hint": "guess", "confidence": "meh"}).status_code == 422


def test_feedback_refused_when_session_does_not_collect_it(client, tmp_path):
    _make_session(tmp_path, "plain")
    client.post("/api/sessions/plain/rows/0", json={"annotations": {"UMLS": "C1"}})
    r = client.post("/api/sessions/plain/rows/0/feedback",
                    json={"hint": "guess", "confidence": "sure"})
    assert r.status_code == 400
    assert "_hint" not in _saved(tmp_path, "plain").columns


def test_reflection_round_trip(client, tmp_path):
    _make_session(tmp_path, "fb", study_condition="C2", collect_feedback=True)
    assert client.get("/api/sessions/fb/reflection").json() is None

    ranking = [c for c, _ in reversed(HINTS)]
    r = client.put("/api/sessions/fb/reflection",
                   json={"ranking": ranking, "comment": "  definitions helped  "})
    assert r.status_code == 200

    got = client.get("/api/sessions/fb/reflection").json()
    assert got["ranking"] == ranking
    assert got["comment"] == "definitions helped"
    assert got["study_condition"] == "C2"
    assert got["annotator"] == "ann1"


def test_reflection_ranking_must_cover_every_hint(client, tmp_path):
    _make_session(tmp_path, "fb", collect_feedback=True)
    r = client.put("/api/sessions/fb/reflection",
                   json={"ranking": ["guess", "definition"], "comment": ""})
    assert r.status_code == 422


def test_options_endpoint_lists_nine_hints_and_three_confidences(client):
    o = client.get("/api/feedback/options").json()
    assert len(o["hints"]) == 9
    assert [c["code"] for c in o["confidence"]] == ["sure", "fairly_sure", "unsure"]


def test_feedback_is_off_by_default_and_rows_carry_no_feedback(client, tmp_path):
    _make_session(tmp_path, "plain")
    assert client.get("/api/sessions/plain").json()["collect_feedback"] is False
    client.post("/api/sessions/plain/rows/0",
                json={"annotations": {"UMLS": "C1"}, "panels": ["graph"]})
    assert client.get("/api/sessions/plain/rows/0").json()["feedback"] is None
    saved = _saved(tmp_path, "plain")
    assert not {"_panels", "_hint", "_confidence"} & set(saved.columns)


def test_feedback_can_be_switched_on_for_an_existing_session(client, tmp_path):
    """The Configure page edits a live session; enabling it mid-way must work."""
    _make_session(tmp_path, "later")
    client.post("/api/sessions/later/rows/0", json={"annotations": {"UMLS": "C1"}})

    cfg = client.get("/api/sessions/later").json()
    assert client.put("/api/sessions/later", json={**cfg, "collect_feedback": True}).status_code == 200

    client.post("/api/sessions/later/rows/1", json={"annotations": {"UMLS": "C2"}, "panels": []})
    assert client.post("/api/sessions/later/rows/1/feedback",
                       json={"hint": "guess", "confidence": "unsure"}).status_code == 200
    saved = _saved(tmp_path, "later")
    assert saved.at[1, "_hint"] == "guess"
    assert saved.at[0, "_hint"] == ""      # rows saved before stay blank
