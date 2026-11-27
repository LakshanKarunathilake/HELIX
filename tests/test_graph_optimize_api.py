"""Document Auto-match (/graph/optimize) must never overwrite saved annotations.

Regression: the endpoint passed no ``fixed`` pins, so every mention came back
with the retrieval/graph pick and the UI wrote it over the annotator's saved
correction. Saved rows must anchor the graph but never move (same rule as
/graph/optimize-all).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.graph_client import GraphSchema, Neo4jGraphClient

# mention text -> ranked (id, score) candidates
CANDS = {
    "cold": [("C_TEMP", 1.0), ("C_DISEASE", 1.0)],     # exact tie: graph decides
    "flu": [("C_FLU", 1.0)],
    "virus": [("C_VIRUS", 1.0)],
}
EDGES = [("C_DISEASE", "C_VIRUS"), ("C_DISEASE", "C_HUMAN_PICK")]


def _graph_client():
    c = Neo4jGraphClient.__new__(Neo4jGraphClient)
    c.schema = GraphSchema()
    c._edges_among = lambda ids: [(a, b) for a, b in EDGES if a in ids and b in ids]
    return c


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    import api.main as main

    monkeypatch.setattr(main, "_retrieve_fused", lambda kb_cfg, label: [
        SimpleNamespace(id=cid, score=sc) for cid, sc in CANDS.get(label, [])])
    monkeypatch.setattr(main, "resolve_graph_client", lambda cfg, pool: _graph_client())
    monkeypatch.setattr(main.metrics, "record_graph", lambda *a, **k: None)
    return TestClient(main.app)


def _make_session(tmp_path, name="doc-review"):
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({
        "entity_text": ["cold", "flu", "virus"],
        "document_id": ["d1", "d1", "d1"],
    }).to_csv(inp, index=False)
    cfg = {
        "session_name": name,
        "annotator": "lakshan",
        "input_path": str(inp),
        "delimiter": ",",
        "label_column": "entity_text",
        "doc_id_col": "document_id",
        "enable_graph": True,
        "kbs": [{"name": "UMLS", "endpoint_url": "http://127.0.0.1:8765/query"}],
    }
    (sdir / "session.json").write_text(json.dumps(cfg))
    return name


def test_saved_correction_is_not_returned_or_moved(client, tmp_path):
    name = _make_session(tmp_path)
    # the annotator corrected "flu" away from the retrieval top-1
    assert client.post(f"/api/sessions/{name}/rows/1",
                       json={"annotations": {"UMLS": "C_HUMAN_PICK"}}).status_code == 200

    r = client.post(f"/api/sessions/{name}/graph/optimize",
                    json={"idx": 0, "kb_name": "UMLS"}).json()

    assert "1" not in r["selections"]          # saved row: never sent back to overwrite
    assert r["selections"]["0"] == "C_DISEASE"  # the tie is still broken by the graph
    assert r["kept_saved"] == 1


def test_saved_correction_anchors_the_graph(client, tmp_path):
    name = _make_session(tmp_path)
    # with "virus" saved as something unlinked, only the human's "flu" pick links to C_DISEASE
    client.post(f"/api/sessions/{name}/rows/2", json={"annotations": {"UMLS": "C_OTHER"}})
    client.post(f"/api/sessions/{name}/rows/1", json={"annotations": {"UMLS": "C_HUMAN_PICK"}})

    r = client.post(f"/api/sessions/{name}/graph/optimize",
                    json={"idx": 0, "kb_name": "UMLS"}).json()

    assert r["selections"] == {"0": "C_DISEASE"}


def test_saved_nil_is_left_alone(client, tmp_path):
    name = _make_session(tmp_path)
    client.post(f"/api/sessions/{name}/rows/1", json={"annotations": {"UMLS": "NIL"}})

    r = client.post(f"/api/sessions/{name}/graph/optimize",
                    json={"idx": 0, "kb_name": "UMLS"}).json()

    assert "1" not in r["selections"]


def test_fully_annotated_document_changes_nothing(client, tmp_path):
    name = _make_session(tmp_path)
    for i, cid in enumerate(["C_TEMP", "C_FLU", "C_VIRUS"]):
        client.post(f"/api/sessions/{name}/rows/{i}", json={"annotations": {"UMLS": cid}})

    r = client.post(f"/api/sessions/{name}/graph/optimize",
                    json={"idx": 0, "kb_name": "UMLS"}).json()

    assert r["selections"] == {}
    assert r["kept_saved"] == 3
