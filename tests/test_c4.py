"""Condition C4: the annotator sees the LLM's pick, read from a prewarmed cache.

The guarantee that matters is the cache key: prewarm (driving HELIX over HTTP,
so it sees serialised candidate dicts) must produce exactly the key the live
/candidates endpoint produces from KBClient Candidate objects. Otherwise every
human row would be a cache miss and C4 would silently degrade to C2.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool import llm_reranker
from eltool.kb_client import Candidate, KBClient
from eltool.llm_reranker import LLMReranker
from eval.helix_api import HelixAPI


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(llm_reranker, "_CACHE_DIR", tmp_path / "_llm_cache")
    import api.main as main

    monkeypatch.setattr(main, "_RERANKERS", {})
    return TestClient(main.app)


DENSE = [Candidate("C1", "lung", 0.91, definition="organ"),
         Candidate("C2", "lung structure", 0.88, aliases=["pulmo"]),
         Candidate("C3", "lungs", 0.70)]
LEXICAL = [Candidate("C3", "lungs", 0.95),       # outranks its dense copy
           Candidate("C4", "lung disease", 0.80)]


@pytest.fixture
def fake_kb(monkeypatch):
    def query(self, label):
        return list(LEXICAL if self.cfg.endpoint_url.endswith("/lexical") else DENSE)

    monkeypatch.setattr(KBClient, "query", query)


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def _request(self, user_text, schema, tool_name="select_entity"):
        calls.append(user_text)
        ids = schema["properties"]["entity_id"]["enum"]
        return {"entity_id": ids[1], "confidence": 0.8, "rationale": "fits", "ranking": ids}

    monkeypatch.setattr(LLMReranker, "_request", _request)
    return calls


def _make_session(tmp_path, name: str, **cfg_extra) -> None:
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung", "DM"], "document_id": ["d1", "d2"]}).to_csv(
        inp, index=False)
    ctx = tmp_path / "ctx"
    ctx.mkdir(exist_ok=True)
    (ctx / "d1.txt").write_text("The lung was inflamed.")
    (ctx / "d2.txt").write_text("DM type 2.")
    cfg = {
        "session_name": name, "annotator": "ann1", "input_path": str(inp),
        "delimiter": ",", "label_column": "entity_text",
        "context_folder": str(ctx), "doc_id_col": "document_id",
        "kbs": [{"name": "UMLS", "endpoint_url": "http://kb/query",
                 "enable_dense": True, "enable_lexical": True}],
        **cfg_extra,
    }
    (sdir / "session.json").write_text(json.dumps(cfg))


C4 = dict(study_condition="C4", enable_llm_rerank=True, llm_cache_only=True,
          llm_provider="openai", llm_model="m", llm_base_url="http://llm/v1",
          collect_feedback=True)


def _saved(tmp_path, name):
    return pd.read_csv(tmp_path / name / "annotated.csv", dtype=str, keep_default_na=False)


def test_c4_rows_record_what_the_human_was_shown(client, tmp_path):
    _make_session(tmp_path, "s-c4", **C4)
    r = client.post("/api/sessions/s-c4/rows/0", json={
        "annotations": {"UMLS": "C2"}, "top1_id": "C1", "user_interacted": False,
        "llm_id": "C2", "llm_confidence": 0.8})
    assert r.status_code == 200
    s = _saved(tmp_path, "s-c4")
    assert s.at[0, "_llm_id"] == "C2"
    assert s.at[0, "_llm_confidence"] == "0.8"
    # Frozen with provenance: a later re-save cannot rewrite what was shown.
    client.post("/api/sessions/s-c4/rows/0", json={
        "annotations": {"UMLS": "C3"}, "llm_id": "C9", "llm_confidence": 0.1})
    s = _saved(tmp_path, "s-c4")
    assert s.at[0, "_llm_id"] == "C2"
    assert s.at[0, "_llm_confidence"] == "0.8"
    # Nothing shown (cache miss) -> empty, not a fabricated value.
    client.post("/api/sessions/s-c4/rows/1", json={"annotations": {"UMLS": "C1"}})
    s = _saved(tmp_path, "s-c4")
    assert s.at[1, "_llm_id"] == "" and s.at[1, "_llm_confidence"] == ""


def test_non_llm_study_session_has_no_llm_columns(client, tmp_path):
    _make_session(tmp_path, "s-c2", study_condition="C2")
    client.post("/api/sessions/s-c2/rows/0", json={
        "annotations": {"UMLS": "C1"}, "llm_id": "C2", "llm_confidence": 0.8})
    cols = _saved(tmp_path, "s-c2").columns
    assert "_llm_id" not in cols and "_llm_confidence" not in cols


def test_ai_hint_only_accepted_in_llm_sessions(client, tmp_path):
    _make_session(tmp_path, "s-c2", study_condition="C2", collect_feedback=True)
    _make_session(tmp_path, "s-c4", **C4)
    for name in ("s-c2", "s-c4"):
        client.post(f"/api/sessions/{name}/rows/0", json={"annotations": {"UMLS": "C1"}})
    body = {"hint": "ai_suggestion", "confidence": "sure"}
    assert client.post("/api/sessions/s-c2/rows/0/feedback", json=body).status_code == 422
    assert client.post("/api/sessions/s-c4/rows/0/feedback", json=body).status_code == 200
    assert _saved(tmp_path, "s-c4").at[0, "_hint"] == "ai_suggestion"


def test_options_with_ai_flag_has_ten_hints(client):
    plain = client.get("/api/feedback/options").json()["hints"]
    ai = client.get("/api/feedback/options?ai=true").json()["hints"]
    assert len(plain) == 9 and len(ai) == 10
    assert ai[:9] == plain
    assert ai[-1] == {"code": "ai_suggestion",
                      "label": "The AI suggestion and its explanation"}


def test_c4_reflection_ranks_the_ai_hint_too(client, tmp_path):
    _make_session(tmp_path, "s-c4", **C4)
    codes = [h["code"] for h in client.get("/api/feedback/options?ai=true").json()["hints"]]
    assert client.put("/api/sessions/s-c4/reflection",
                      json={"ranking": codes, "comment": ""}).status_code == 200


def test_cache_only_session_returns_candidates_and_llm_error_on_miss(
        client, tmp_path, fake_kb, fake_llm):
    _make_session(tmp_path, "s-c4", **C4)
    r = client.post("/api/sessions/s-c4/candidates", json={
        "label": "lung", "kb_name": "UMLS", "enable_dense": True,
        "enable_lexical": True, "context": "The lung was inflamed."})
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body["dense_candidates"]] == ["C1", "C2", "C3"]
    assert "cache miss" in body["llm"]["error"]
    assert fake_llm == []          # the model was never called


class ClientHelix(HelixAPI):
    """HelixAPI over a FastAPI TestClient instead of the network."""

    def __init__(self, tc):
        super().__init__("")
        self.tc = tc

    def _get(self, url):
        r = self.tc.get(url)
        r.raise_for_status()
        return r.json()

    def _post(self, url, body):
        r = self.tc.post(url, json=body)
        r.raise_for_status()
        return r.json()


def test_prewarm_then_cache_only_api_hits(client, tmp_path, fake_kb, fake_llm):
    from eval.prewarm_llm import prewarm_session

    _make_session(tmp_path, "s-c4", **C4)
    api = ClientHelix(client)

    stats = prewarm_session("s-c4", api)
    assert stats == {"cached": 0, "computed": 2, "failed": 0}
    assert len(fake_llm) == 2
    # Idempotent: a second run is all cache hits.
    assert prewarm_session("s-c4", api) == {"cached": 2, "computed": 0, "failed": 0}

    # Exactly what the browser sends for row 0.
    row = client.get("/api/sessions/s-c4/rows/0").json()
    r = client.post("/api/sessions/s-c4/candidates", json={
        "label": row["label"], "kb_name": "UMLS", "enable_dense": True,
        "enable_lexical": True, "context": row["context_text"]})
    llm = r.json()["llm"]
    assert "error" not in llm, llm
    assert llm["cached"] is True
    assert llm["entity_id"] == "C1"          # ids[1] of fused order C3, C1, C2, C4
    assert len(fake_llm) == 2                # human session never called the model


def _kb_failing(monkeypatch, which: set[str]):
    """KB whose dense and/or lexical endpoint raises (the API swallows it -> [])."""
    def query(self, label):
        side = "lexical" if self.cfg.endpoint_url.endswith("/lexical") else "dense"
        if side in which:
            raise RuntimeError(f"{side} down")
        return list(LEXICAL if side == "lexical" else DENSE)

    monkeypatch.setattr(KBClient, "query", query)


def _cache_files(tmp_path):
    d = tmp_path / "_llm_cache"
    return list(d.glob("*.json")) if d.exists() else []


def test_prewarm_counts_empty_retrieval_as_failed_and_caches_nothing(
        client, tmp_path, monkeypatch, fake_llm):
    from eval.prewarm_llm import main, prewarm_session

    _kb_failing(monkeypatch, {"dense", "lexical"})
    _make_session(tmp_path, "s-c4", **C4)
    assert prewarm_session("s-c4", ClientHelix(client)) == \
        {"cached": 0, "computed": 0, "failed": 2}
    assert fake_llm == [] and _cache_files(tmp_path) == []


@pytest.mark.parametrize("down", ["dense", "lexical"])
def test_prewarm_one_sided_retrieval_failure_is_failed_not_cached(
        client, tmp_path, monkeypatch, fake_llm, down):
    from eval.prewarm_llm import prewarm_session

    _kb_failing(monkeypatch, {down})
    _make_session(tmp_path, "s-c4", **C4)
    assert prewarm_session("s-c4", ClientHelix(client)) == \
        {"cached": 0, "computed": 0, "failed": 2}
    assert fake_llm == [] and _cache_files(tmp_path) == []


def test_prewarm_cli_exits_nonzero_when_rows_fail(client, tmp_path, monkeypatch, fake_llm):
    import eval.prewarm_llm as P

    _kb_failing(monkeypatch, {"lexical"})
    _make_session(tmp_path, "s-c4", **C4)
    monkeypatch.setattr(P, "HelixAPI", lambda base: ClientHelix(client))
    assert P.main(["--prefix", "s-"]) == 1
