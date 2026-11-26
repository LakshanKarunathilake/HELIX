"""Agreement and final-value rules (pure functions)."""
import pytest

from eltool.agreement import FinalRow, final_rows, pairwise_agreement

KBS = ["drug", "disease"]
BLANK = {"drug": "", "disease": ""}


def test_percent_agreement_counts_shared_rows_only():
    values = {
        1: {0: "a", 1: "b", 2: "c"},
        2: {1: "b", 2: "x", 3: "d"},
    }
    (pair,) = pairwise_agreement(values)
    assert (pair["a"], pair["b"]) == (1, 2)
    assert pair["n"] == 2  # rows 1 and 2 only
    assert pair["percent"] == 50.0


def test_kappa_perfect_and_chance():
    perfect = {1: {0: "a", 1: "a", 2: "b", 3: "b"}, 2: {0: "a", 1: "a", 2: "b", 3: "b"}}
    (p,) = pairwise_agreement(perfect)
    assert p["percent"] == 100.0
    assert p["kappa"] == pytest.approx(1.0)

    # p_o = 0.5, both marginals 50/50 so p_e = 0.5 -> kappa 0
    chance = {1: {0: "a", 1: "a", 2: "b", 3: "b"}, 2: {0: "a", 1: "b", 2: "a", 3: "b"}}
    (c,) = pairwise_agreement(chance)
    assert c["percent"] == 50.0
    assert c["kappa"] == pytest.approx(0.0)


def test_kappa_none_for_under_two_shared_rows():
    (one,) = pairwise_agreement({1: {0: "a"}, 2: {0: "a"}})
    assert one["n"] == 1 and one["percent"] == 100.0 and one["kappa"] is None
    (zero,) = pairwise_agreement({1: {0: "a"}, 2: {1: "a"}})
    assert zero["n"] == 0 and zero["percent"] is None and zero["kappa"] is None
    assert zero["overlap"] is None


def test_selection_order_is_agreement():
    (p,) = pairwise_agreement({1: {0: "C1|C2"}, 2: {0: "C2|C1"}})
    assert (p["percent"], p["overlap"]) == (100.0, 100.0)


def test_partial_overlap_is_reported():
    (p,) = pairwise_agreement({1: {0: "C1|C2", 1: "C3"}, 2: {0: "C1", 1: "C3"}})
    assert (p["n"], p["percent"], p["overlap"]) == (2, 50.0, 75.0)


def test_blank_values_are_not_shared_rows():
    (p,) = pairwise_agreement({1: {0: "", 1: "a"}, 2: {0: "", 1: "a"}})
    assert (p["n"], p["percent"]) == (1, 100.0)


def test_kappa_none_when_expected_agreement_is_one():
    (pair,) = pairwise_agreement({1: {0: "x", 1: "x"}, 2: {0: "x", 1: "x"}})
    assert pair["n"] == 2 and pair["percent"] == 100.0
    assert pair["kappa"] is None


def test_three_users_give_three_pairs():
    values = {3: {0: "a"}, 1: {0: "a"}, 2: {0: "b"}}
    pairs = pairwise_agreement(values)
    assert [(p["a"], p["b"]) for p in pairs] == [(1, 2), (1, 3), (2, 3)]
    assert [p["percent"] for p in pairs] == [0.0, 100.0, 0.0]


def test_final_prefers_adjudication():
    human = {
        1: {0: {"drug": "A", "disease": "X"}},
        2: {0: {"drug": "B", "disease": "X"}},
    }
    adj = {0: {"drug": "C"}}  # missing KB filled with ""
    out = final_rows(human, adj, 1, KBS)
    assert out[0] == FinalRow({"drug": "C", "disease": ""}, False, "")


def test_final_needs_two_matching_humans():
    human = {
        1: {0: {"drug": "A", "disease": "X"}},
        2: {0: {"drug": "A", "disease": "X"}},
        3: {0: {"drug": "A", "disease": "X"}},
    }
    out = final_rows(human, {}, 1, KBS)
    assert out[0] == FinalRow({"drug": "A", "disease": "X"}, False, "")


def test_final_disagree_in_one_kb_column_blocks_value():
    human = {
        1: {0: {"drug": "A", "disease": "X"}},
        2: {0: {"drug": "A", "disease": "Y"}},
    }
    out = final_rows(human, {}, 1, KBS)
    assert out[0] == FinalRow(BLANK, True, "disagree")


def test_final_single_human_is_flagged_single_annotator():
    human = {1: {0: {"drug": "A", "disease": "X"}}, 2: {}}
    out = final_rows(human, {}, 1, KBS)
    assert out[0] == FinalRow(BLANK, True, "single_annotator")


def test_final_script_only_row_is_flagged():
    out = final_rows({1: {}}, {}, 2, KBS, script_only_rows=frozenset({1}))
    assert out[1] == FinalRow(BLANK, True, "script_only")
    assert out[0] == FinalRow(BLANK, True, "no_decisions")


def test_final_untouched_row_is_no_decisions():
    out = final_rows({}, {}, 3, KBS)
    assert sorted(out) == [0, 1, 2]
    assert all(r == FinalRow(BLANK, True, "no_decisions") for r in out.values())
