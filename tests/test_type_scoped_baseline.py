"""Scoring logic for the type-scoped retrieval baseline (option A: filter after retrieval)."""
from __future__ import annotations

from eval.run_type_scoped_baseline import gold_ids, hit_rank, score_mention, summarize

POOL = [("C1", 0.99, {"T017"}), ("C2", 0.98, {"T047"}), ("C3", 0.97, {"T047"}),
        ("C4", 0.96, {"T121"})]


def ok_disease(tuis):
    return "T047" in tuis


def test_gold_ids_splits_composites_and_drops_nil():
    assert gold_ids("D1|D2") == {"D1", "D2"}
    assert gold_ids("-1") == set()
    assert gold_ids("") == set()


def test_hit_rank_is_one_based_and_none_when_absent():
    assert hit_rank(["C1", "C2"], {"C2"}) == 2
    assert hit_rank(["C1", "C2"], {"C9"}) is None


def test_score_mention_ranks_before_and_after_filtering():
    r = score_mention(POOL, {"C2"}, ok_disease, gold_tuis={"T047"})
    assert r["rank_all"] == 2
    assert r["rank_scoped"] == 1          # C1 is filtered out, so C2 moves up
    assert r["n_compatible"] == 2
    assert r["gold_compatible"] is True


def test_score_mention_gold_filtered_out_by_its_own_type():
    r = score_mention(POOL, {"C4"}, ok_disease, gold_tuis={"T121"})
    assert r["rank_all"] == 4
    assert r["rank_scoped"] is None
    assert r["gold_compatible"] is False


def test_score_mention_with_no_compatible_candidate():
    r = score_mention([("C1", 0.9, {"T017"})], {"C1"}, ok_disease, gold_tuis=None)
    assert r["n_compatible"] == 0
    assert r["rank_scoped"] is None
    assert r["rank_fallback"] == 1        # fallback keeps the unfiltered list
    assert r["gold_compatible"] is None   # unknown gold types are not judged


def test_summarize_reports_acc_at_1_and_k():
    recs = [{"rank_all": 1, "rank_scoped": 1, "rank_fallback": 1, "n_compatible": 3, "gold_compatible": True},
            {"rank_all": 2, "rank_scoped": 1, "rank_fallback": 1, "n_compatible": 3, "gold_compatible": True},
            {"rank_all": 12, "rank_scoped": None, "rank_fallback": None, "n_compatible": 3, "gold_compatible": False},
            {"rank_all": None, "rank_scoped": None, "rank_fallback": 2, "n_compatible": 0, "gold_compatible": None}]
    s = summarize(recs, k=10)
    assert s["n"] == 4
    assert s["acc1_all"] == 25.0 and s["acck_all"] == 50.0
    assert s["acc1_scoped"] == 50.0 and s["acck_scoped"] == 50.0
    assert s["acc1_fallback"] == 50.0 and s["acck_fallback"] == 75.0
    assert s["no_compatible"] == 1
    assert s["gold_incompatible"] == 1
    assert s["gold_beyond_k_all"] == 1     # rank 12 is in the pool but past the top 10


def test_composite_dictionary_ids_match_any_part():
    assert hit_rank(["601367|D002544", "D1"], {"D002544"}) == 1
    assert gold_ids("D002544|D003") == {"D002544", "D003"}
