"""Mode C: LLM pre-annotation with auto/spotcheck/review routing."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import eltool.session as session_mod
from eltool.llm_annotator import AnnotationDecision
from eltool.llm_reranker import LLMRerankError
from eltool.review import preannotate, review_reasons, route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

N = 6


@pytest.fixture(autouse=True)
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _make(tmp_path, name="rv", review=True, rate=0.0, cond="C2"):
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": [f"m{i}" for i in range(N)],
                  "document_id": ["d"] * N}).to_csv(inp, index=False)
    cfg = {"session_name": name, "annotator": "ann", "input_path": str(inp), "delimiter": ",",
           "label_column": "entity_text", "study_condition": cond,
           "kbs": [{"name": "UMLS", "endpoint_url": "http://x/query"}],
           "review_mode": review, "review_spotcheck_rate": rate}
    (sdir / "session.json").write_text(json.dumps(cfg))
    return name


def _cand(i):
    return {"id": i, "label": f"lbl-{i}", "score": 0.9}


class FakeAPI:
    def __init__(self, dense=("C1", "C2"), lexical=()):
        self.dense, self.lexical = dense, lexical

    def row(self, session, idx):
        return {"idx": idx, "label": f"m{idx}", "context_text": None}

    def candidates(self, session, label, kb, context):
        return {"dense_candidates": [_cand(i) for i in self.dense],
                "lexical_candidates": [_cand(i) for i in self.lexical]}


class FakeAnn:
    model, provider = "m", "openai"

    def __init__(self, script=None, fail_on=()):
        # script: per-call (entity_id, level); default sure C1
        self.script, self.fail_on, self.calls = script or {}, set(fail_on), 0

    def annotate(self, mention, context, candidates, *, suggested_id=None, graph_evidence=""):
        k = int(mention[1:])
        self.calls += 1
        if k in self.fail_on:
            raise LLMRerankError("boom")
        eid, level = self.script.get(k, ("C1", "sure"))
        return AnnotationDecision(eid, "name", level, 0.8, "why", "m", "openai", 5, False)


def _out(tmp_path, name="rv"):
    return pd.read_csv(tmp_path / name / "annotated.csv", dtype=str, keep_default_na=False)


def test_route_auto_requires_sure_and_top1_and_not_nil():
    rng = random.Random(1)
    assert route("sure", "C1", "C1", rng, 0.0) == "auto"
    assert route("fairly_sure", "C1", "C1", rng, 0.0) == "review"
    assert route("sure", "C2", "C1", rng, 0.0) == "review"
    assert route("sure", "NIL", "NIL", rng, 0.0) == "review"


def test_route_spotcheck_rate_is_reproducible():
    def run(seed):
        rng = random.Random(seed)
        return [route("sure", "C1", "C1", rng, 0.1) for _ in range(1000)]
    a = run(7)
    assert a == run(7)
    share = a.count("spotcheck") / 1000
    assert 0.07 <= share <= 0.13
    assert set(a) == {"auto", "spotcheck"}


def test_route_draws_only_for_eligible_rows():
    r1, r2 = random.Random(3), random.Random(3)
    route("unsure", "C1", "C1", r1, 0.1)
    route("sure", "C2", "C1", r1, 0.1)
    assert r1.random() == r2.random()


def test_review_reasons_order_and_content():
    assert review_reasons("sure", "C1", "C1") == []
    assert review_reasons("unsure", "C2", "C1") == [
        "the AI was not sure", "its pick differs from the top suggestion"]
    assert review_reasons("unsure", "NIL", "C1") == [
        "the AI was not sure", "the AI found no match"]


def test_preannotate_fills_only_auto_rows(sessions):
    name = _make(sessions)
    ann = FakeAnn({1: ("C2", "sure"), 2: ("C1", "unsure"), 3: ("NIL", "sure")})
    stats = preannotate(name, ann, FakeAPI())
    out = _out(sessions)
    assert stats["auto"] == 3 and stats["review"] == 3
    assert list(out["_route"]) == ["auto", "review", "review", "review", "auto", "auto"]
    assert out.at[0, "UMLS"] == "C1" and out.at[0, "annotator"] == "llm:m"
    assert out.at[0, "_method"] == "llm_auto" and out.at[0, "_condition"] == "C2"
    for i in (1, 2, 3):
        assert out.at[i, "UMLS"] == "" and out.at[i, "_method"] == ""
    assert out.at[1, "_llm_id"] == "C2" and out.at[1, "_llm_level"] == "sure"
    assert out.at[1, "_llm_confidence"] == "0.800" and out.at[1, "_llm_label"] == "lbl-C2"
    assert out.at[1, "_top1_id"] == "C1" and out.at[1, "_rationale"] == "why"


def test_preannotate_is_resumable(sessions):
    name = _make(sessions)
    ann = FakeAnn(fail_on={2})
    stats = preannotate(name, ann, FakeAPI())
    out = _out(sessions)
    assert stats["error"] == 1 and out.at[2, "_route"] == "" and out.at[2, "_llm_error"] == "boom"
    assert out.at[2, "UMLS"] == ""
    ann2 = FakeAnn()
    preannotate(name, ann2, FakeAPI())
    assert ann2.calls == 1                      # only the failed row is retried
    out = _out(sessions)
    assert out.at[2, "_route"] == "auto" and out.at[2, "_llm_error"] == ""


def test_no_candidates_row_goes_to_review_as_nil(sessions):
    name = _make(sessions)
    preannotate(name, FakeAnn(), FakeAPI(dense=()))
    out = _out(sessions)
    assert set(out["_route"]) == {"review"}
    assert out.at[0, "_llm_id"] == "NIL" and out.at[0, "_llm_level"] == "unsure"
    assert out.at[0, "UMLS"] == ""


def test_queue_lists_review_and_spotcheck_rows(client, sessions):
    name = _make(sessions, rate=1.0)
    ann = FakeAnn({1: ("C2", "sure"), 4: ("C1", "unsure")})
    preannotate(name, ann, FakeAPI())
    # rate 1.0: eligible rows 0,2,3,5 -> spotcheck; 1,4 -> review
    p = client.get(f"/api/sessions/{name}/progress").json()
    assert p["queue"] == [0, 1, 2, 3, 4, 5]
    assert p["resume_idx"] == 0 and p["done"] == 0 and p["total"] == N
    client.post(f"/api/sessions/{name}/rows/0", json={"annotations": {"UMLS": "C1"}, "top1_id": "C1"})
    assert client.get(f"/api/sessions/{name}/progress").json()["resume_idx"] == 1


def test_queue_skips_auto_rows_and_resume_is_len_when_done(client, sessions):
    name = _make(sessions)
    preannotate(name, FakeAnn({2: ("C2", "unsure")}), FakeAPI())
    p = client.get(f"/api/sessions/{name}/progress").json()
    assert p["queue"] == [2] and p["resume_idx"] == 2 and p["done"] == N - 1
    client.post(f"/api/sessions/{name}/rows/2", json={"annotations": {"UMLS": "C2"}, "top1_id": "C1"})
    p = client.get(f"/api/sessions/{name}/progress").json()
    assert p["resume_idx"] == N


def test_spotcheck_rows_hide_the_ai_pick(client, sessions):
    name = _make(sessions, rate=1.0)
    preannotate(name, FakeAnn({1: ("C2", "unsure")}), FakeAPI())
    assert client.get(f"/api/sessions/{name}/rows/0").json()["review"] == {"route": "spotcheck"}
    rv = client.get(f"/api/sessions/{name}/rows/1").json()["review"]
    assert rv == {"route": "review", "llm_id": "C2", "label": "lbl-C2", "level": "unsure",
                  "rationale": "why",
                  "reasons": ["the AI was not sure", "its pick differs from the top suggestion"]}


def test_auto_row_has_no_review_payload(client, sessions):
    name = _make(sessions)
    preannotate(name, FakeAnn(), FakeAPI())
    assert client.get(f"/api/sessions/{name}/rows/0").json()["review"] is None


def test_non_review_session_has_no_queue(client, sessions):
    name = _make(sessions, review=False)
    assert "queue" not in client.get(f"/api/sessions/{name}/progress").json()
    assert client.get(f"/api/sessions/{name}/rows/0").json()["review"] is None


def test_human_save_keeps_ai_columns_on_queue_rows(client, sessions):
    name = _make(sessions, rate=1.0)
    cfgp = sessions / name / "session.json"
    cfg = json.loads(cfgp.read_text())
    cfg["enable_llm_rerank"] = True
    cfgp.write_text(json.dumps(cfg))
    preannotate(name, FakeAnn({1: ("C2", "unsure")}), FakeAPI())
    before = _out(sessions)
    for i in (0, 1):    # 0 = spotcheck, 1 = review
        r = client.post(f"/api/sessions/{name}/rows/{i}",
                        json={"annotations": {"UMLS": "C1"}, "top1_id": "ZZ",
                              "llm_id": "", "user_interacted": True})
        assert r.status_code == 200
    out = _out(sessions)
    for i in (0, 1):
        for col in ("_llm_id", "_llm_confidence", "_llm_level", "_top1_id"):
            assert out.at[i, col] == before.at[i, col] != ""
        assert out.at[i, "_method"] != ""
