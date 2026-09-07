"""Block selection for the pilot study.

The study's validity rests on the blocks being disjoint at the *document*
level: a document seen in an earlier condition would leak context (and the
annotator's memory of its concepts) into a later one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from eval.build_study_sessions import CONDITION_ORDER, select_blocks


@pytest.fixture
def corpus() -> pd.DataFrame:
    rows = []
    for doc in range(30):
        for m in range(20):
            rows.append({"document_id": f"d{doc}", "entity_text": f"m{m}"})
    return pd.DataFrame(rows)


def test_blocks_are_document_disjoint(corpus):
    blocks = select_blocks(corpus, n_blocks=6, target_size=20)

    seen: set[str] = set()
    for b in blocks:
        assert not (seen & set(b)), "a document appears in two blocks"
        seen |= set(b)


def test_returns_the_requested_number_of_blocks(corpus):
    assert len(select_blocks(corpus, n_blocks=6, target_size=20)) == 6


def test_each_block_reaches_the_target_size(corpus):
    blocks = select_blocks(corpus, n_blocks=6, target_size=20)
    counts = corpus.groupby("document_id").size()

    for b in blocks:
        assert sum(counts[d] for d in b) >= 20


@pytest.fixture
def varied_corpus() -> pd.DataFrame:
    """Document sizes spread like real MedMentions (mean ~46, wide spread)."""
    rows = []
    for doc in range(200):
        for m in range(10 + (doc * 7) % 70):
            rows.append({"document_id": f"d{doc}", "entity_text": f"m{m}"})
    return pd.DataFrame(rows)


def test_blocks_are_comparable_in_size(varied_corpus):
    """Unequal blocks confound the condition with how much work it was."""
    blocks = select_blocks(varied_corpus, n_blocks=6, target_size=40)
    counts = varied_corpus.groupby("document_id").size()
    sizes = [sum(counts[d] for d in b) for b in blocks]

    assert max(sizes) <= 1.5 * min(sizes), f"blocks too uneven: {sizes}"


def test_selection_is_deterministic(corpus):
    assert select_blocks(corpus, 6, 20, seed=7) == select_blocks(corpus, 6, 20, seed=7)


def test_raises_when_the_corpus_cannot_fill_the_blocks():
    tiny = pd.DataFrame({"document_id": ["d0"] * 5, "entity_text": list("abcde")})

    with pytest.raises(ValueError):
        select_blocks(tiny, n_blocks=6, target_size=20)


def test_condition_order_is_balanced_across_early_and_late_slots():
    """ABC-CBA: each arm gets one early and one late slot."""
    assert len(CONDITION_ORDER) == 6
    first_half, second_half = CONDITION_ORDER[:3], CONDITION_ORDER[3:]

    assert sorted(first_half) == sorted(second_half)
    assert second_half == first_half[::-1]
