"""Gold answers must never enter the annotation session.

Hiding gold via `info_columns` is blind-by-configuration — one visit to the
Configure page from being displayed again. Keeping it in a separate file is
blind-by-construction. These tests pin that split, and the join key that puts
the two back together at scoring time.
"""

from __future__ import annotations

import pandas as pd
import pytest

from eval.build_study_sessions import GOLD_COL, UID_COL, split_gold


@pytest.fixture
def mentions() -> pd.DataFrame:
    # (document_id, entity_span) is NOT unique in MedMentions — 97 collisions
    # in the real corpus — so the fixture reproduces one.
    return pd.DataFrame(
        {
            "document_id": ["d1", "d1", "d1"],
            "entity_span": ["0-5", "0-5", "10-14"],
            "entity_text": ["lung", "lung", "DM"],
            "gold_cui": ["C1", "C2", "C3|C4"],
        }
    )


def test_session_input_carries_no_gold(mentions):
    session, _ = split_gold(mentions)

    assert GOLD_COL not in session.columns


def test_gold_file_keeps_the_answers(mentions):
    _, gold = split_gold(mentions)

    assert list(gold[GOLD_COL]) == ["C1", "C2", "C3|C4"]


def test_both_sides_share_a_unique_join_key(mentions):
    session, gold = split_gold(mentions)

    assert UID_COL in session.columns and UID_COL in gold.columns
    assert session[UID_COL].is_unique
    assert list(session[UID_COL]) == list(gold[UID_COL])


def test_join_recovers_the_right_gold_despite_duplicate_spans(mentions):
    """The two 'lung' rows share (document_id, entity_span) but differ in gold."""
    session, gold = split_gold(mentions)

    merged = session.merge(gold, on=UID_COL, how="left")

    assert len(merged) == 3                      # no row duplication from the join
    assert list(merged[GOLD_COL]) == ["C1", "C2", "C3|C4"]
