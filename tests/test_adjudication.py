"""Row status, reviewer pre-ticks, row versions and export reasons."""
from __future__ import annotations

import pytest

from eltool.adjudication import STATUSES, export_reason, options, row_state, snapshot

KB = ["U"]


def _d(based_on, kb_values=None, id=1):
    return {"id": id, "kb_values": kb_values or {"U": "C1"}, "based_on": based_on}


def test_statuses():
    assert STATUSES == ("disagree", "single", "agreed", "changed", "decided", "waiting")


@pytest.mark.parametrize("raw,status", [
    ({}, "waiting"),
    ({1: {"U": ""}}, "waiting"),
    ({1: {"U": "C1"}}, "single"),
    ({1: {"U": "C1|C2"}, 2: {"U": "C2 | C1"}}, "agreed"),
    ({1: {"U": "C1"}, 2: {"U": "C1|C2"}}, "disagree"),
])
def test_status_without_decision(raw, status):
    assert row_state(raw, None, KB, []).status == status


def test_answers_are_normalised_and_blank_ones_dropped():
    s = row_state({1: {"U": "C2| C1|C2", "gone": "X"}, 2: {"U": " | "}}, None, KB, [])
    assert s.answers == {1: {"U": ("C1", "C2")}}


def test_snapshot_is_the_stored_form():
    assert snapshot({2: {"U": ("C1", "C2"), "M": ()}}) == {"2": {"U": "C1|C2", "M": ""}}


def test_decided_until_an_answer_changes_appears_or_disappears():
    d = _d({"1": {"U": "C1|C2"}, "2": {"U": "C1"}})
    same = {1: {"U": "C2|C1"}, 2: {"U": "C1"}}
    assert row_state(same, d, KB, []).status == "decided"
    for raw in ({1: {"U": "C3"}, 2: {"U": "C1"}},
                {**same, 3: {"U": "C1"}},
                {1: {"U": "C1|C2"}}, {}):
        assert row_state(raw, d, KB, []).status == "changed", raw


def test_based_on_is_normalised_over_the_current_kbs():
    # stored before KB "M" left the session; user 2 had only answered that KB
    d = _d({"1": {"U": "C2|C1", "M": "D1"}, "2": {"U": "", "M": "D2"}})
    raw = {1: {"U": "C1|C2", "M": "D1"}, 2: {"U": "", "M": "D2"}}
    assert row_state(raw, d, KB, []).status == "decided"


def test_legacy_decision_is_never_changed():
    assert row_state({1: {"U": "C9"}}, _d(None), KB, []).status == "decided"


def test_covering_decision_is_returned():
    d = _d(None)
    assert row_state({}, d, KB, []).decision == d


def test_decision_missing_a_current_kb_does_not_decide():
    s = row_state({1: {"U": "C1", "M": "D1"}}, _d(None), ["U", "M"], [])
    assert (s.status, s.decision) == ("single", None)


def test_decision_with_a_blank_kb_does_not_decide():
    s = row_state({}, _d(None, {"U": "C1", "M": " | "}), ["U", "M"], [])
    assert (s.status, s.decision) == ("waiting", None)


def test_bulk_needs_every_annotator_complete():
    raw = {1: {"U": "C1"}, 2: {"U": "C1"}}
    assert row_state(raw, None, KB, [1, 2]).bulk_eligible
    waiting = row_state(raw, None, KB, [1, 2, 3])
    assert (waiting.pending, waiting.bulk_eligible) == (1, False)
    blank_kb = {1: {"U": "C1", "M": ""}, 2: {"U": "C1", "M": ""}}
    s = row_state(blank_kb, None, ["U", "M"], [])
    assert (s.status, s.bulk_eligible) == ("agreed", False)


def test_an_incomplete_annotator_is_pending():
    raw = {1: {"U": "C1", "M": "D1"}, 2: {"U": "C1", "M": ""}}
    assert row_state(raw, None, ["U", "M"], {1, 2, 3}).pending == 2


def test_only_agreed_rows_are_bulk_eligible():
    raw = {1: {"U": "C1"}, 2: {"U": "C1"}}
    assert not row_state(raw, _d(None), KB, [1, 2]).bulk_eligible
    assert not row_state({1: {"U": "C1"}}, None, KB, [1]).bulk_eligible


def test_version_ignores_order_and_follows_answers_and_decision():
    v = lambda raw, d=None: row_state(raw, d, KB, []).version
    assert v({1: {"U": "C1|C2"}}) == v({1: {"U": "C2|C1"}})
    assert v({1: {"U": "C1"}}) != v({1: {"U": "C2"}})
    assert v({1: {"U": "C1"}}) != v({1: {"U": "C1"}}, _d({"1": {"U": "C1"}}))


def test_version_follows_the_kb_list_and_any_stored_decision():
    raw = {1: {"U": "C1"}}
    plain = row_state(raw, None, KB, []).version
    assert len(plain) == 16 and set(plain) <= set("0123456789abcdef")
    assert row_state(raw, None, ["U", "M"], []).version != plain
    # a decision that no longer covers the KBs still counts, by its id
    two = lambda d: row_state(raw, d, ["U", "M"], []).version
    assert len({two(None), two(_d(None, id=1)), two(_d(None, id=2))}) == 3


def test_only_unanimous_concepts_are_preticked():
    two = row_state({1: {"U": "C1|C2"}, 2: {"U": "C1"}}, None, KB, []).answers
    assert options(two, KB)["U"] == [
        {"id": "C1", "user_ids": [1, 2], "preticked": True},
        {"id": "C2", "user_ids": [1], "preticked": False}]
    one = row_state({1: {"U": "C1"}}, None, KB, []).answers
    assert options(one, KB)["U"][0]["preticked"] is False


def test_options_are_per_kb_and_sorted():
    answers = row_state({2: {"U": "C2"}, 1: {"U": "C1"}}, None, ["U", "M"], []).answers
    assert options(answers, ["U", "M"]) == {
        "U": [{"id": "C1", "user_ids": [1], "preticked": False},
              {"id": "C2", "user_ids": [2], "preticked": False}],
        "M": []}


def test_export_reasons():
    assert [export_reason(s, False) for s in
            ("decided", "disagree", "single", "agreed", "changed", "waiting")] == [
        "", "disagree", "single_annotator", "awaiting_review",
        "changed_since_review", "no_decisions"]
    assert export_reason("waiting", True) == "script_only"
