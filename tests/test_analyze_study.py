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

from eval.analyze_study import cohen_kappa, compare_human_ai, summarise, summarise_review


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


# --- Human vs AI ----------------------------------------------------------

def arow(uid, cond, gold, pred, top1="X", **extra):
    r = row(uid, cond, gold, pred, top1)
    r.update(extra)
    return r


def _pair():
    human = pd.DataFrame([
        arow("m1", "C2", "G1", "G1", "G1", _hint="a", _confidence="sure"),
        arow("m2", "C2", "G2", "G2", "G9", _hint="b", _confidence="unsure"),
        arow("m3", "C2", "G3", "G9", "G3", _hint="a", _confidence="sure"),
        arow("m4", "C2", "G4", "G4", "G4", _hint="a", _confidence="sure"),
    ])
    ai = pd.DataFrame([
        arow("m1", "C2", "G1", "G1", "G1", _hint="a", _confidence="sure"),
        arow("m2", "C2", "G2", "G9", "G9", _hint="b", _confidence="unsure"),
        arow("m3", "C2", "G3", "G3", "G3", _hint="b", _confidence="sure"),
        arow("m4", "C2", "G4", "G4", "G4", _hint="a", _confidence="fairly_sure"),
    ])
    return human, ai


def test_kappa_identical_is_one():
    assert cohen_kappa(["x", "y", "x"], ["x", "y", "x"]) == pytest.approx(1.0)


def test_kappa_known_value():
    assert cohen_kappa(["x", "x", "y", "y"], ["x", "y", "y", "y"]) == pytest.approx(0.5)


def test_compare_counts_ai_only_and_human_only_right():
    human, ai = _pair()

    s = compare_human_ai(human, ai, "umls")["C2"]

    assert s["n"] == 4
    assert s["human_acc1"] == pytest.approx(75.0)
    assert s["ai_acc1"] == pytest.approx(75.0)
    assert s["ai_only_right"] == 1       # m3
    assert s["human_only_right"] == 1    # m2
    assert s["agreement"] == pytest.approx(50.0)   # m1, m4
    assert s["retrieval_acc1"] == pytest.approx(75.0)
    assert s["ai_missing"] == 0
    assert s["hint_kappa"] is not None
    assert s["human_by_confidence"]["sure"] == {"n": 3, "acc1": pytest.approx(200 / 3)}
    assert s["ai_by_confidence"]["fairly_sure"] == {"n": 1, "acc1": pytest.approx(100.0)}


def test_compare_joins_on_mention_uid_not_row_order():
    human, ai = _pair()
    base = compare_human_ai(human, ai, "umls")
    shuffled = ai.iloc[[3, 1, 0, 2]].reset_index(drop=True)

    assert compare_human_ai(human, shuffled, "umls") == base


def test_ai_rows_still_failing_are_excluded_and_counted():
    human, ai = _pair()
    ai.loc[ai["mention_uid"] == "m2", ["umls", "_llm_error"]] = ["", "HTTP 500"]
    ai = ai[ai["mention_uid"] != "m4"]                  # not yet run

    s = compare_human_ai(human, ai, "umls")["C2"]

    assert s["n"] == 2
    assert s["ai_missing"] == 2
    assert s["human_only_right"] == 0


# --- Mode C: review queue ---------------------------------------------------

def rrow(uid, route, gold, pred, llm_id, method="user_confirm", ms="10000"):
    """A review-session row. Auto rows were written by the LLM, not a human."""
    if route == "auto":
        method, ms = "llm_auto", ""
    return {"mention_uid": uid, "_route": route, "gold_cui": gold, "umls": pred,
            "_llm_id": llm_id, "_method": method, "_elapsed_ms": ms}


def _review_df():
    return pd.DataFrame([
        rrow("m1", "auto", "G1", "G1", "G1"),                      # auto right
        rrow("m2", "auto", "G2", "G9", "G9"),                      # auto wrong
        rrow("m3", "auto", "G3", "G3", "G3"),                      # auto right
        rrow("m4", "auto", "G4", "G4", "G4"),                      # auto right
        rrow("m5", "review", "G5", "G5", "G8", ms="20000"),        # human fixed it
        rrow("m6", "review", "G6", "G0", "G0", ms="40000"),        # human kept a wrong pick
        rrow("m7", "spotcheck", "G7", "G7", "G7", ms="10000"),     # agrees with the AI
        rrow("m8", "spotcheck", "G8", "G8", "G5", ms="30000"),     # disagrees
        rrow("m9", "review", "G9", "", "G9", method="", ms=""),    # not yet reviewed
    ])


def test_review_summary_counts_routes():
    s = summarise_review(_review_df(), "umls")

    assert (s["n_auto"], s["n_review"], s["n_spotcheck"]) == (4, 3, 2)
    assert s["review_acc1"] == pytest.approx(50.0)           # m5 right, m6 wrong; m9 pending
    assert s["spotcheck_agreement"] == pytest.approx(50.0)   # m7 agrees, m8 does not


def test_auto_accuracy_uses_gold():
    s = summarise_review(_review_df(), "umls")

    assert s["auto_acc1"] == pytest.approx(75.0)


def test_minutes_saved_uses_median_human_time():
    s = summarise_review(_review_df(), "umls")

    # Human-saved queue rows took 20, 40, 10, 30 s -> median 25 s, total 100 s.
    assert s["human_minutes"] == pytest.approx(100 / 60)
    assert s["est_minutes_saved"] == pytest.approx(4 * 25 / 60)


def test_auto_rows_a_human_resaved_do_not_count_as_auto():
    df = _review_df()
    # m1 was auto-accepted (right); a human later re-saved it via Jump-to (wrong).
    df.loc[df["mention_uid"] == "m1", ["umls", "_method", "_elapsed_ms"]] = ["G0", "user_override", "5000"]

    s = summarise_review(df, "umls")

    assert s["n_auto"] == 4                           # routing is unchanged
    assert s["auto_acc1"] == pytest.approx(200 / 3)   # m2 wrong, m3, m4 right; m1 excluded


def test_review_summary_tolerates_missing_optional_columns():
    df = _review_df().drop(columns=["_method", "_elapsed_ms", "_llm_id"])

    s = summarise_review(df, "umls")

    assert (s["n_auto"], s["n_review"], s["n_spotcheck"]) == (4, 3, 2)
    assert s["spotcheck_agreement"] == pytest.approx(0.0)
    assert s["human_minutes"] == pytest.approx(0.0)
    assert s["est_minutes_saved"] is None
