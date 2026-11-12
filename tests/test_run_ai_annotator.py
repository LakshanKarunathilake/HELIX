"""Mode A runner: the LLM annotates a human study session's rows, seeing what a
human sees in that condition, and writes an ordinary HELIX session."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

import eltool.session as session_mod
from eltool.llm_annotator import AnnotationDecision
from eltool.llm_reranker import LLMRerankError
from eltool.session import reflection_path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import run_ai_annotator as R  # noqa: E402

SRC = "study2-1-c2"


@pytest.fixture(autouse=True)
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    return tmp_path


def _make_source(tmp_path, name=SRC, condition="C2"):
    sdir = tmp_path / name
    sdir.mkdir(parents=True)
    inp = sdir / "input.csv"
    pd.DataFrame({"entity_text": ["lung", "DM"], "document_id": ["d1", "d2"]}).to_csv(inp, index=False)
    cfg = {"session_name": name, "annotator": "human", "input_path": str(inp),
           "delimiter": ",", "label_column": "entity_text",
           "kbs": [{"name": "UMLS", "endpoint_url": "http://x/query"}],
           "study_condition": condition, "enable_llm_rerank": True}
    (sdir / "session.json").write_text(json.dumps(cfg))
    return name


def _saved(tmp_path, name):
    return pd.read_csv(tmp_path / name / "annotated.csv", dtype=str, keep_default_na=False)


def _cand(i):
    return {"id": i, "label": i, "score": 0.9, "matched_alias": "", "aliases": [],
            "semantic_groups": [], "semantic_types": [], "definition": ""}


class FakeAPI:
    def __init__(self, dense=("C1", "C2"), lexical=("C2", "C3")):
        self.dense, self.lexical = list(dense), list(lexical)
        self.graph_calls = []

    def row(self, session, idx):
        return {"idx": idx, "total": 2, "label": ["lung", "DM"][idx], "context_text": None}

    def candidates(self, session, label, kb, context):
        return {"dense_candidates": [_cand(i) for i in self.dense],
                "lexical_candidates": [_cand(i) for i in self.lexical]}

    def graph(self, session, ids):
        self.graph_calls.append(list(ids))
        return {"nodes": [{"id": "C1", "label": "Lung"}],
                "edges": [{"source": "C1", "target": "C2", "type": "isa"}], "enabled": True}


class FakeAnn:
    model, provider = "m", "openai"

    def __init__(self, fail_first=False):
        self.calls, self.seen, self.fail_first = 0, [], fail_first

    def annotate(self, mention, context, candidates, *, suggested_id=None, graph_evidence=""):
        self.calls += 1
        self.seen.append({"suggested": suggested_id, "graph": graph_evidence,
                          "ids": [c["id"] for c in candidates], "context": context})
        if self.fail_first and self.calls == 1:
            raise LLMRerankError("boom")
        return AnnotationDecision("C1", "definition", "sure", 0.9, "because", "m", "openai", 12, False)

    def reflect(self, decisions):
        return {"ranking": ["definition", "name", "alias", "type", "context", "graph",
                            "popularity", "other", "none"][:9], "comment": "ok"}


def test_writes_every_row_with_protocol_columns(sessions):
    _make_source(sessions)
    ann = FakeAnn()
    stats = R.run_session(SRC, ann, FakeAPI())
    out = _saved(sessions, "ai-m-" + SRC)
    assert stats["llm"] == 2
    assert list(out["annotator"]) == ["llm:m", "llm:m"]
    assert out.at[0, "UMLS"] == "C1"
    assert out.at[0, "_hint"] == "definition" and out.at[0, "_confidence"] == "sure"
    assert out.at[0, "_llm_confidence"] == "0.900" and out.at[0, "_rationale"] == "because"
    assert out.at[0, "_top1_id"] == "C1" and out.at[0, "_condition"] == "C2"
    assert out.at[0, "_selected_rank"] == "1" and out.at[0, "_panels"] == "none"
    # model sees dense then novel lexical ids
    assert ann.seen[0]["ids"] == ["C1", "C2", "C3"]


@pytest.mark.parametrize("cond,sugg,graph", [("C1", None, False), ("C2", "C1", False),
                                             ("C3", "C1", True), ("DEV", "C1", False),
                                             ("X9", "C1", False)])
def test_condition_controls_what_the_model_sees(sessions, cond, sugg, graph):
    name = _make_source(sessions, "src-" + cond.lower(), cond)
    ann, api = FakeAnn(), FakeAPI()
    R.run_session(name, ann, api)
    assert ann.seen[0]["suggested"] == sugg
    assert bool(ann.seen[0]["graph"]) is graph
    if graph:
        assert api.graph_calls[0] == ["C1", "C2", "C3"]
    else:
        assert api.graph_calls == []


def test_rerun_skips_rows_already_written(sessions):
    _make_source(sessions)
    ann = FakeAnn()
    R.run_session(SRC, ann, FakeAPI(), limit=1)
    R.run_session(SRC, ann, FakeAPI())
    assert ann.calls == 2


def test_model_error_is_recorded_and_run_continues(sessions):
    _make_source(sessions)
    name = "ai-m-" + SRC
    stats = R.run_session(SRC, FakeAnn(fail_first=True), FakeAPI())
    out = _saved(sessions, name)
    assert stats["error"] == 1
    assert "boom" in out.at[0, "_llm_error"] and out.at[0, "_method"] == "" and out.at[0, "UMLS"] == ""
    assert out.at[1, "_method"] == "llm"
    assert not reflection_path(name).exists()          # open row -> no reflection yet
    ok = FakeAnn()
    R.run_session(SRC, ok, FakeAPI())
    out = _saved(sessions, name)
    assert ok.calls == 1
    assert out.at[0, "_method"] == "llm" and out.at[0, "_llm_error"] == ""
    assert reflection_path(name).exists()


def test_no_candidates_writes_nil_without_calling_model(sessions):
    _make_source(sessions)
    ann = FakeAnn()
    R.run_session(SRC, ann, FakeAPI(dense=(), lexical=()))
    out = _saved(sessions, "ai-m-" + SRC)
    assert out.at[0, "UMLS"] == "NIL" and out.at[0, "_method"] == "llm_no_candidates"
    assert out.at[0, "_top1_id"] == "" and ann.calls == 0


def test_reflection_written_once_at_the_end(sessions):
    _make_source(sessions)
    name = "ai-m-" + SRC
    R.run_session(SRC, FakeAnn(), FakeAPI())
    rec = json.loads(reflection_path(name).read_text())
    assert rec["annotator"] == "llm:m" and rec["session_name"] == name
    assert rec["study_condition"] == "C2" and rec["comment"] == "ok" and len(rec["ranking"]) == 9
    reflection_path(name).write_text('{"keep": 1}')
    R.run_session(SRC, FakeAnn(), FakeAPI())
    assert json.loads(reflection_path(name).read_text()) == {"keep": 1}


def test_names_and_session_copy(sessions):
    assert R.ai_session_name("openai/gpt-oss-120b", "s1") == "ai-openai-gpt-oss-120b-s1"
    _make_source(sessions, condition="")
    n = R.make_ai_session(SRC, "openai/x")
    assert R.make_ai_session(SRC, "openai/x") == n
    cfg = json.loads((sessions / n / "session.json").read_text())
    assert cfg["annotator"] == "llm:openai/x" and cfg["collect_feedback"] is True
    assert cfg["enable_llm_rerank"] is False and cfg["study_condition"] == "DEV"
    assert Path(cfg["input_path"]).parent == sessions / n and Path(cfg["input_path"]).exists()


def test_one_save_carries_whole_row(sessions, monkeypatch):
    _make_source(sessions)
    import eltool.store as store
    real = store.save_output
    seen = []

    def spy(path, df):
        real(path, df)
        if Path(path).name == "annotated.csv" and df.at[0, "UMLS"]:
            seen.append((df.at[0, "_hint"], df.at[0, "_confidence"], df.at[0, "_rationale"]))

    monkeypatch.setattr(store, "save_output", spy)
    monkeypatch.setattr(R, "save_output", spy)
    R.run_session(SRC, FakeAnn(), FakeAPI(), limit=1)
    # first save that has the KB cell already has the feedback with it
    assert seen and seen[0] == ("definition", "sure", "because")


def test_http_error_is_recorded_and_run_continues(sessions):
    import requests
    _make_source(sessions)

    class Flaky(FakeAPI):
        def candidates(self, session, label, kb, context):
            if label == "lung":
                raise requests.ConnectionError("down")
            return super().candidates(session, label, kb, context)

    stats = R.run_session(SRC, FakeAnn(), Flaky())
    out = _saved(sessions, "ai-m-" + SRC)
    assert stats["error"] == 1 and "down" in out.at[0, "_llm_error"]
    assert out.at[0, "UMLS"] == "" and out.at[1, "_method"] == "llm"
    R.run_session(SRC, FakeAnn(), FakeAPI())
    assert _saved(sessions, "ai-m-" + SRC).at[0, "_llm_error"] == ""


# --- final-review fixes ------------------------------------------------------

class KBDown(FakeAPI):
    """/candidates swallowed a KB error: empty lists + retrieval_errors set."""
    def candidates(self, session, label, kb, context):
        if label == "lung":
            return {"dense_candidates": [], "lexical_candidates": [],
                    "retrieval_errors": {"dense": "kb unreachable", "lexical": None}}
        return super().candidates(session, label, kb, context)


def test_kb_error_is_a_row_error_not_nil_and_is_retried(sessions):
    _make_source(sessions)
    name = "ai-m-" + SRC
    ann = FakeAnn()
    stats = R.run_session(SRC, ann, KBDown())
    out = _saved(sessions, name)
    assert stats["error"] == 1 and stats["llm_no_candidates"] == 0
    assert "kb unreachable" in out.at[0, "_llm_error"]
    assert out.at[0, "UMLS"] == "" and out.at[0, "_method"] == ""
    assert not reflection_path(name).exists()
    R.run_session(SRC, ann, FakeAPI())
    out = _saved(sessions, name)
    assert out.at[0, "UMLS"] == "C1" and out.at[0, "_llm_error"] == ""


def test_no_errors_and_no_candidates_is_still_nil(sessions):
    _make_source(sessions)

    class Empty(FakeAPI):
        def candidates(self, session, label, kb, context):
            return {"dense_candidates": [], "lexical_candidates": [],
                    "retrieval_errors": {"dense": None, "lexical": None}}

    R.run_session(SRC, FakeAnn(), Empty())
    out = _saved(sessions, "ai-m-" + SRC)
    assert out.at[0, "UMLS"] == "NIL" and out.at[0, "_method"] == "llm_no_candidates"


@pytest.mark.parametrize("graph_resp,enable_graph", [
    ({"nodes": [], "edges": [], "enabled": True, "error": "neo4j down"}, True),
    ({"nodes": [], "edges": [], "enabled": False}, True),
])
def test_graph_failure_on_c3_is_a_row_error(sessions, graph_resp, enable_graph):
    name = _make_source(sessions, "src-c3", "C3")
    cfgp = sessions / name / "session.json"
    cfg = json.loads(cfgp.read_text())
    cfg["enable_graph"] = enable_graph
    cfgp.write_text(json.dumps(cfg))

    class GraphDown(FakeAPI):
        def graph(self, session, ids):
            return dict(graph_resp)

    ann = FakeAnn()
    stats = R.run_session(name, ann, GraphDown())
    out = _saved(sessions, "ai-m-" + name)
    assert stats["error"] == 2 and ann.calls == 0
    assert out.at[0, "UMLS"] == "" and "graph" in out.at[0, "_llm_error"]


class ReflectBoom(FakeAnn):
    def reflect(self, decisions):
        raise LLMRerankError("bad ranking")


def test_reflection_failure_warns_and_run_continues(sessions, capsys):
    _make_source(sessions)
    name = "ai-m-" + SRC
    stats = R.run_session(SRC, ReflectBoom(), FakeAPI())
    assert stats["llm"] == 2
    assert not reflection_path(name).exists()
    assert name in capsys.readouterr().out
    R.run_session(SRC, FakeAnn(), FakeAPI())          # a later run retries
    assert reflection_path(name).exists()


def test_cli_continues_to_next_session_after_reflection_failure(sessions, monkeypatch):
    _make_source(sessions, "study2-1-c2")
    _make_source(sessions, "study2-2-c2")
    monkeypatch.setattr(R, "LLMAnnotator", lambda **kw: ReflectBoom())
    monkeypatch.setattr(R, "HelixAPI", lambda base: FakeAPI())
    assert R.main(["--prefix", "study2", "--model", "m"]) == 0
    for src in ("study2-1-c2", "study2-2-c2"):
        out = _saved(sessions, "ai-m-" + src)
        assert list(out["UMLS"]) == ["C1", "C1"]
        assert not reflection_path("ai-m-" + src).exists()


def test_no_model_decisions_means_no_reflection(sessions):
    _make_source(sessions)
    name = "ai-m-" + SRC

    class Never(FakeAnn):
        def reflect(self, decisions):
            raise AssertionError("must not reflect without decisions")

    R.run_session(SRC, Never(), FakeAPI(dense=(), lexical=()))
    assert not reflection_path(name).exists()


def test_ai_twin_never_shows_gold(sessions):
    _make_source(sessions)
    cfgp = sessions / SRC / "session.json"
    cfg = json.loads(cfgp.read_text())
    cfg["debug_show_gold"] = True
    cfgp.write_text(json.dumps(cfg))
    n = R.make_ai_session(SRC, "m")
    assert json.loads((sessions / n / "session.json").read_text())["debug_show_gold"] is False
