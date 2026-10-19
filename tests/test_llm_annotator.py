import pytest

import eltool.llm_reranker as lr
from eltool.feedback import validate_reflection_ranking
from eltool.llm_annotator import (AI_HINT_CODES, AnnotationDecision, LLMAnnotator,
                                  PROTOCOL_VERSION)
from eltool.llm_reranker import LLMRerankError

seen: dict = {}
calls = {"n": 0}
reply: dict = {}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "_CACHE_DIR", tmp_path)
    seen.clear()
    calls["n"] = 0
    reply.clear()
    reply.update(entity_id="C1", hint="name_match", confidence_level="sure",
                 confidence=0.9, rationale="exact name")

    def fake(self, user_text, schema, tool_name="select_entity"):
        calls["n"] += 1
        seen.update(user_text=user_text, schema=schema, tool_name=tool_name)
        if tool_name == "record_reflection":
            return {"ranking": list(reversed(AI_HINT_CODES)), "comment": "ok"}
        return dict(reply)

    monkeypatch.setattr(LLMAnnotator, "_request", fake)


@pytest.fixture
def ann():
    return LLMAnnotator(model="m", provider="openai", api_key="x")


@pytest.fixture
def cands():
    return [{"id": i, "label": f"L{i}", "score": 0.5} for i in ("C1", "C2", "C3")]


@pytest.fixture
def decisions():
    return [AnnotationDecision("C1", "name_match", "sure", 0.9, "r", "m", "openai", 5, False)]


def test_defaults(ann):
    assert ann.temperature == 0
    assert PROTOCOL_VERSION == "helix-protocol-v1"
    assert LLMAnnotator.CACHE_NAMESPACE == PROTOCOL_VERSION


def test_schema_allows_nil_and_excludes_external_lookup(ann, cands):
    ann.annotate("lung", "", cands)
    s = seen["schema"]["properties"]
    assert s["entity_id"]["enum"] == ["C1", "C2", "C3", "NIL"]
    assert "external_lookup" not in s["hint"]["enum"] and len(s["hint"]["enum"]) == 8
    assert s["confidence_level"]["enum"] == ["sure", "fairly_sure", "unsure"]


def test_suggestion_and_graph_appear_only_when_given(ann, cands):
    ann.annotate("lung", "", cands)
    assert "Suggested by the tool" not in seen["user_text"] and "Graph links" not in seen["user_text"]
    ann.annotate("lung", "", cands, suggested_id="C1", graph_evidence="C1 —[isa]— C2")
    assert "Suggested by the tool: [1] C1" in seen["user_text"] and "C1 —[isa]— C2" in seen["user_text"]


def test_suggested_not_in_list_omitted(ann, cands):
    ann.annotate("lung", "", cands, suggested_id="ZZ")
    assert "Suggested by the tool" not in seen["user_text"]


def test_unknown_hint_is_rejected(ann, cands):
    reply.update(hint="vibes")
    with pytest.raises(LLMRerankError):
        ann.annotate("lung", "", cands)


def test_nil_answer_is_accepted(ann, cands):
    reply.update(entity_id="NIL")
    assert ann.annotate("lung", "", cands).entity_id == "NIL"


def test_different_condition_view_is_cached_separately(ann, cands):
    ann.annotate("lung", "", cands); ann.annotate("lung", "", cands, suggested_id="C1")
    assert calls["n"] == 2


def test_cache_hit(ann, cands):
    a = ann.annotate("lung", "", cands)
    b = ann.annotate("lung", "", cands)
    assert calls["n"] == 1 and not a.cached and b.cached and b.latency_ms == 0


def test_reflection_ranking_is_valid_for_the_api(ann, decisions):
    out = ann.reflect(decisions)
    validate_reflection_ranking(out["ranking"])
    assert out["ranking"][-1] == "external_lookup"
    assert seen["tool_name"] == "record_reflection"
    ann.reflect(decisions)
    assert calls["n"] == 2


def test_bad_reflection_rejected(ann, decisions, monkeypatch):
    monkeypatch.setattr(LLMAnnotator, "_request",
                        lambda self, u, s, t="x": {"ranking": ["guess"], "comment": ""})
    with pytest.raises(LLMRerankError):
        ann.reflect(decisions)
