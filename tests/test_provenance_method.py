"""How a decision was reached, derived from raw UI signals.

The frontend reports only facts it observes (did the annotator touch the UI,
did they type an id by hand, what did they end on, what was ranked first).
Turning those into the study's method label happens here so it is testable
and so the taxonomy lives in one place.

The distinction that matters for the automation-bias question is
auto_accept (countdown expired untouched) vs user_confirm (annotator engaged
and still chose the top candidate) vs user_override (annotator moved away).
"""

from __future__ import annotations

from eltool.provenance import classify_method


def test_untouched_countdown_is_auto_accept():
    assert (
        classify_method(selected="C1", top1_id="C1", user_interacted=False)
        == "auto_accept"
    )


def test_engaged_but_kept_top1_is_user_confirm():
    assert (
        classify_method(selected="C1", top1_id="C1", user_interacted=True)
        == "user_confirm"
    )


def test_moved_away_from_top1_is_user_override():
    assert (
        classify_method(selected="C2", top1_id="C1", user_interacted=True)
        == "user_override"
    )


def test_nil_is_recorded_as_nil():
    assert (
        classify_method(selected="NIL", top1_id="C1", user_interacted=True) == "nil"
    )


def test_hand_typed_id_is_manual_entry():
    assert (
        classify_method(
            selected="C9", top1_id="C1", user_interacted=True, manual_entry=True
        )
        == "manual_entry"
    )


def test_multi_select_containing_top1_is_still_an_override():
    """Pipe-joined multi-match is a different answer from the single top-1."""
    assert (
        classify_method(selected="C1|C2", top1_id="C1", user_interacted=True)
        == "user_override"
    )


def test_no_candidates_retrieved_is_auto_accept_of_nothing():
    """Empty top-1 with no interaction must not crash or claim an override."""
    assert (
        classify_method(selected="", top1_id="", user_interacted=False)
        == "auto_accept"
    )
