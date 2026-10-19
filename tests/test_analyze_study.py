"""Scoring the pilot study.

The headline measure is *paired*: for each condition, the retrieval baseline and
the human's final answer are computed over the same mentions, so document
difficulty cancels. Comparing raw per-condition accuracy would instead be
swamped by which documents landed in which block (per-document Acc@1 sd is
~18 points, and each condition has only two documents).
"""

from __future__ import annotations

import pandas as pd
import pytest

from eval.analyze_study import summarise


def row(uid, cond, gold, pred, top1, method="user_confirm", ms="5000", rank="1"):
    return {
        "mention_uid": uid, "_condition": cond, "gold_cui": gold, "umls": pred,
        "_top1_id": top1, "_method": method, "_elapsed_ms": ms,
        "_selected_rank": rank, "_revisions": "0",
    }


def test_baseline_comes_from_the_retrieved_top1_not_the_human_answer():
    df = pd.DataFrame([
        row("m1", "C2", gold="C1", pred="C1", top1="C1"),   # baseline right
        row("m2", "C2", gold="C2", pred="C2", top1="C9"),   # baseline wrong
    ])

    s = summarise(df, kb_col="umls")["C2"]

    assert s["baseline_acc1"] == pytest.approx(50.0)


def test_human_accuracy_is_measured_on_the_same_mentions():
    df = pd.DataFrame([
        row("m1", "C2", gold="C1", pred="C1", top1="C1"),
        row("m2", "C2", gold="C2", pred="C2", top1="C9"),   # human fixed it
    ])

    s = summarise(df, kb_col="umls")["C2"]

    assert s["human_acc1"] == pytest.approx(100.0)
    assert s["delta"] == pytest.approx(50.0)


def test_a_human_who_breaks_a_correct_suggestion_shows_a_negative_delta():
    """This is the automation-bias question R3 raised — it must be visible."""
    df = pd.DataFrame([
        row("m1", "C3", gold="C1", pred="C9", top1="C1"),   # was right, made wrong
        row("m2", "C3", gold="C2", pred="C2", top1="C2"),
    ])

    s = summarise(df, kb_col="umls")["C3"]

    assert s["baseline_acc1"] == pytest.approx(100.0)
    assert s["human_acc1"] == pytest.approx(50.0)
    assert s["delta"] == pytest.approx(-50.0)


def test_multi_valued_gold_matches_on_any_component():
    df = pd.DataFrame([row("m1", "C1", gold="C1|C2", pred="C2", top1="C2")])

    assert summarise(df, kb_col="umls")["C1"]["human_acc1"] == pytest.approx(100.0)


def test_composite_ids_match_on_any_component():
    """KB dictionary SourceIDs can be composite, e.g. '601367|D002544'."""
    df = pd.DataFrame([row("m1", "C1", gold="D002544", pred="601367|D002544", top1="X")])

    assert summarise(df, kb_col="umls")["C1"]["human_acc1"] == pytest.approx(100.0)


def test_unannotated_rows_are_excluded():
    df = pd.DataFrame([
        row("m1", "C1", gold="C1", pred="C1", top1="C1"),
        row("m2", "C1", gold="C2", pred="", top1="C2", method="", ms="", rank=""),
    ])

    assert summarise(df, kb_col="umls")["C1"]["n"] == 1


def test_method_mix_and_timing_are_reported():
    df = pd.DataFrame([
        row("m1", "C2", gold="C1", pred="C1", top1="C1", method="auto_accept", ms="4000"),
        row("m2", "C2", gold="C2", pred="C7", top1="C2", method="user_override", ms="12000"),
    ])

    s = summarise(df, kb_col="umls")["C2"]

    assert s["methods"]["auto_accept"] == 1
    assert s["methods"]["user_override"] == 1
    assert s["median_secs"] == pytest.approx(8.0)


def test_contaminated_rows_are_excluded_from_the_headline_numbers():
    """A row annotated with gold on screen cannot count toward accuracy."""
    clean = row("m1", "C2", gold="C1", pred="C1", top1="C9")
    dirty = row("m2", "C2", gold="C2", pred="C2", top1="C9")
    dirty["_debug_gold_shown"] = "1"
    clean["_debug_gold_shown"] = "0"

    s = summarise(pd.DataFrame([clean, dirty]), kb_col="umls")["C2"]

    assert s["n"] == 1
    assert s["excluded_debug"] == 1


def test_missing_debug_column_is_treated_as_clean():
    """Sessions predating the flag have no such column."""
    s = summarise(pd.DataFrame([row("m1", "C1", gold="C1", pred="C1", top1="C1")]),
                  kb_col="umls")["C1"]

    assert s["n"] == 1 and s["excluded_debug"] == 0


def test_feedback_breakdown_reports_accuracy_per_hint_and_confidence():
    df = pd.DataFrame([
        {**row("m1", "C3", gold="C1", pred="C1", top1="C1"),
         "_hint": "definition", "_confidence": "sure", "_panels": "definition;graph"},
        {**row("m2", "C3", gold="C2", pred="C9", top1="C9"),
         "_hint": "guess", "_confidence": "unsure", "_panels": "none"},
        {**row("m3", "C3", gold="C3", pred="C3", top1="C9"),
         "_hint": "definition", "_confidence": "unsure", "_panels": "graph"},
        {**row("m4", "C3", gold="C4", pred="C4", top1="C4"),
         "_hint": "", "_confidence": "", "_panels": ""},
    ])

    s = summarise(df, kb_col="umls")["C3"]

    assert s["feedback_missing"] == 1
    assert s["by_hint"]["definition"] == {"n": 2, "human_acc1": pytest.approx(100.0)}
    assert s["by_hint"]["guess"]["human_acc1"] == pytest.approx(0.0)
    assert s["by_confidence"]["unsure"] == {"n": 2, "human_acc1": pytest.approx(50.0)}
    assert s["panels_opened"] == {"definition": 1, "graph": 2}


def test_sessions_without_feedback_have_no_feedback_keys():
    df = pd.DataFrame([row("m1", "C2", gold="C1", pred="C1", top1="C1")])
    assert "by_hint" not in summarise(df, kb_col="umls")["C2"]
