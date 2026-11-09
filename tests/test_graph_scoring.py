import math

from eval.run_graph_scoring import (Rel, auroc, candidate_links, err_auroc, gated_pick,
                                    graph_score)


def test_graph_score_count_hub_aa():
    links = [({"RO"}, 0.0), ({"PAR"}, 6.0)]
    assert graph_score(links, 2.0, "all", "count") == 2.0
    assert graph_score(links, 2.0, "ro", "count") == 1.0
    assert graph_score(links, 2.0, "no-hier", "count") == 1.0          # PAR dropped
    assert math.isclose(graph_score(links, 2.0, "all", "hub"), 2 / math.log2(4))
    assert math.isclose(graph_score(links, 2.0, "all", "aa"), (1 / 1 + 1 / 3) / 2)
    assert graph_score([], 2.0, "all", "aa") == 0.0


def test_structural_only_links_do_not_count_without_struct():
    assert graph_score([({"AQ"}, 0.0)], 0.0, "all", "count") == 1.0
    assert graph_score([({"AQ"}, 0.0)], 0.0, "no-struct", "count") == 0.0


def test_hub_weighting_prefers_the_specific_candidate():
    hub = graph_score([({"RO"}, 0.0), ({"RO"}, 0.0)], 10_000.0, "all", "hub")
    specific = graph_score([({"RO"}, 0.0)], 2.0, "all", "hub")
    assert specific > hub


def test_gated_pick_only_switches_within_delta_and_on_strict_gain():
    cands = [("A", 0.90), ("B", 0.89), ("C", 0.70)]
    assert gated_pick(cands, [0, 1, 5], 0.02) == "B"      # C is too far behind
    assert gated_pick(cands, [1, 1, 0], 0.02) == "A"      # a tie keeps dense order
    assert gated_pick(cands, [0, 1, 5], 0.0) == "A"


def test_candidate_links_skips_itself_and_composites():
    kb = {"rel": {"C1": {"C2": {"RO"}}}, "cuis": {"D1": {"C1"}, "D2": {"C2"}, "D1|X": {"C1"}},
          "deg": {"C1": 3, "C2": 5}}
    rel = Rel(kb)
    links = candidate_links([("D1", 0.9)], {"D2", "D1", "D1|X"}, rel)
    assert links == [[({"RO"}, 5.0)]]


def test_auroc():
    assert auroc([3, 4], [1, 2]) == 1.0
    assert auroc([1], [1]) == 0.5
    assert err_auroc([0.9, 0.8, 0.1], [True, True, False]) == 1.0


def test_combo_pick_graph_first_then_names_on_exact_ties():
    from eval.run_graph_scoring import combo_pick
    cands = [("A", 0.9), ("B", 0.9), ("C", 0.89)]
    assert combo_pick(cands, [0, 0, 0], [1, 3, 9], 0.02) == "B"   # exact tie -> more names; C not tied
    assert combo_pick(cands, [0, 0, 1], [1, 3, 9], 0.02) == "C"   # graph wins within delta
    assert combo_pick(cands, [0, 0, 0], [1, 1, 9], 0.0) == "A"
