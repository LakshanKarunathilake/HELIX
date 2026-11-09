"""Pure logic of the hint ceiling check: lexical match, graph counts, reranking, comparison."""
from __future__ import annotations

from eval.run_hint_analysis import choose, graph_counts, independent_others, mcnemar, norm

CANDS = [("C1", 0.95), ("C2", 0.94), ("C3", 0.80)]


def test_norm_ignores_case_and_punctuation():
    assert norm("Non-Hodgkin's  Lymphoma") == "non hodgkin s lymphoma"


def test_choose_without_hints_is_the_dense_top1():
    assert choose(CANDS) == "C1"
    assert choose([]) is None


def test_lexical_match_wins_over_dense_order():
    assert choose(CANDS, lex=[False, False, True]) == "C3"
    assert choose(CANDS, lex=[True, False, True]) == "C1"     # top-1 already matches: kept
    assert choose(CANDS, lex=[False, False, False]) == "C1"


def test_unique_lexical_rule_ignores_ambiguous_matches():
    assert choose(CANDS, lex=[False, True, True], lex_unique=True) == "C1"
    assert choose(CANDS, lex=[False, True, False], lex_unique=True) == "C2"


def test_graph_picks_most_connected_and_ties_keep_dense_order():
    assert choose(CANDS, graph=[0, 2, 1]) == "C2"
    assert choose(CANDS, graph=[1, 1, 0]) == "C1"
    assert choose(CANDS, graph=[0, 0, 0]) == "C1"


def test_graph_only_reorders_near_ties_when_delta_is_set():
    assert choose(CANDS, graph=[0, 0, 3], delta=0.05) == "C1"   # C3 is 0.15 behind
    assert choose(CANDS, graph=[0, 3, 0], delta=0.05) == "C2"


def test_graph_runs_among_lexical_matches():
    assert choose(CANDS, lex=[True, False, True], graph=[0, 5, 1]) == "C3"


def test_graph_counts_distinct_related_anchors_excluding_itself():
    related = {"C1": {"A1", "A2"}, "C2": {"A1"}}
    rel = lambda a, b: b in related.get(a, set())
    anchors = ["A1", "A1", "A2", "C1"]                # repeated anchors count once; C1 is itself
    assert graph_counts(["C1", "C2", "C3"], anchors, rel) == [2, 1, 0]


def test_mcnemar_counts_fixes_and_breaks():
    base = [True, True, False, False, False]
    rule = [True, False, True, True, False]
    out = mcnemar(base, rule)
    assert (out["fixes"], out["breaks"], out["net"]) == (2, 1, 1)
    assert 0 < out["p"] <= 1


def test_mentions_sharing_text_do_not_anchor_each_other():
    surfaces = [{"cystic fibrosis"}, {"cf", "cystic fibrosis"}, {"lung"}, {"cystic fibrosis"}]
    assert independent_others(0, [0, 1, 2, 3], surfaces) == [2]
    assert independent_others(2, [0, 1, 2, 3], surfaces) == [0, 1, 3]


def test_relation_fn_any_needs_an_actual_relation():
    from eval.run_hint_analysis import relation_fn
    kb = {"rel": {"C1": {"C3": {"RO"}}}, "cuis": {"A": {"C1"}, "B": {"C2"}, "C": {"C3"}}}
    assert not relation_fn(kb, "any")("A", "B")      # no MRREL row between them
    assert relation_fn(kb, "any")("A", "C")
    assert relation_fn(kb, "ro")("C", "A")
    assert not relation_fn(kb, "hier")("A", "C")
