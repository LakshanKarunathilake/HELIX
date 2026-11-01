"""Block selection for the pilot study.

The study's validity rests on the blocks being disjoint at the *document*
level: a document seen in an earlier condition would leak context (and the
annotator's memory of its concepts) into a later one.
"""

from __future__ import annotations

import pandas as pd
import pytest

from eval.build_study_sessions import (
    CONDITION_ORDER, condition_order, extra_blocks, select_blocks, study_session_config,
)


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


def test_extra_blocks_avoid_used_documents(corpus):
    used = {f"d{i}" for i in range(0, 30, 2)}
    blocks = extra_blocks(corpus, used, [40, 60], seed=7)
    flat = [d for b in blocks for d in b]
    assert not (set(flat) & used)
    assert len(flat) == len(set(flat))


def test_extra_blocks_hit_requested_sizes(corpus):
    blocks = extra_blocks(corpus, set(), [40, 200], seed=7)
    counts = corpus.groupby("document_id").size()
    sizes = [sum(counts[d] for d in b) for b in blocks]
    assert sizes[0] >= 40 and sizes[1] >= 200


def test_adding_extra_blocks_does_not_change_study_blocks(varied_corpus):
    before = select_blocks(varied_corpus, 6, 40, seed=11)
    used = {d for b in before for d in b}
    extra_blocks(varied_corpus, used, [40, 200], seed=13)
    assert select_blocks(varied_corpus, 6, 40, seed=11) == before


def test_condition_order_is_abcd_dcba():
    assert condition_order(["C1", "C2", "C3", "C4"]) == \
        ["C1", "C2", "C3", "C4", "C4", "C3", "C2", "C1"]


def test_default_conditions_keep_todays_order():
    assert condition_order(["C1", "C2", "C3"]) == CONDITION_ORDER == \
        ["C1", "C2", "C3", "C3", "C2", "C1"]


BASE = {"session_name": "src", "annotator": "a", "enable_llm_rerank": True,
        "llm_provider": "anthropic", "llm_model": "claude-haiku-4-5"}
LLM = {"llm_model": "openai/gpt-oss-120b", "llm_base_url": "http://127.0.0.1:18000/v1",
       "llm_provider": "openai"}


def test_c4_turns_on_cache_only_llm_with_the_given_model():
    cfg = study_session_config(BASE, "s-7-c4", "/x.csv", "C4", feedback=True, llm=LLM)
    assert cfg["enable_llm_rerank"] is True and cfg["llm_cache_only"] is True
    assert cfg["auto_select_secs"] == 5 and cfg["enable_auto_select"] is True
    assert cfg["enable_graph"] is False
    assert (cfg["llm_model"], cfg["llm_base_url"], cfg["llm_provider"]) == \
        ("openai/gpt-oss-120b", "http://127.0.0.1:18000/v1", "openai")
    assert cfg["study_condition"] == "C4" and cfg["collect_feedback"] is True


@pytest.mark.parametrize("cond", ["C1", "C2", "C3"])
def test_other_conditions_never_show_the_llm(cond):
    cfg = study_session_config(BASE, f"s-{cond}", "/x.csv", cond, feedback=False, llm=LLM)
    assert cfg["enable_llm_rerank"] is False
    assert cfg["llm_model"] == "claude-haiku-4-5"      # untouched outside C4


@pytest.mark.parametrize("extra", [
    [],
    ["--llm-model", "openai/gpt-oss-120b"],
    ["--llm-base-url", "http://127.0.0.1:18000/v1"],
])
def test_c4_without_model_and_endpoint_is_rejected(extra, capsys):
    from eval.build_study_sessions import main

    with pytest.raises(SystemExit) as e:
        main(["--dry-run", "--conditions", "C1,C2,C3,C4", *extra])
    assert e.value.code == 2
    assert "C4 needs --llm-model and --llm-base-url" in capsys.readouterr().err


def test_gold_file_is_merged_not_overwritten(tmp_path, monkeypatch):
    import eval.build_study_sessions as B
    gold = tmp_path / "out" / "study_gold.csv"
    gold.parent.mkdir()
    pd.DataFrame({"mention_uid": ["a", "b"], "gold_cui": ["C1", "C2"]}).to_csv(gold, index=False)
    monkeypatch.setattr(B, "GOLD_FILE", gold)

    B.write_gold(pd.DataFrame({"mention_uid": ["b", "c"], "gold_cui": ["C9", "C3"]}))

    got = pd.read_csv(gold, dtype=str, keep_default_na=False)
    assert dict(zip(got["mention_uid"], got["gold_cui"])) == {"a": "C1", "b": "C9", "c": "C3"}
    assert len(got) == 3


def test_gold_file_written_fresh_when_absent(tmp_path, monkeypatch):
    import eval.build_study_sessions as B
    gold = tmp_path / "new" / "study_gold.csv"
    monkeypatch.setattr(B, "GOLD_FILE", gold)
    B.write_gold(pd.DataFrame({"mention_uid": ["a"], "gold_cui": ["C1"]}))
    assert pd.read_csv(gold, dtype=str)["mention_uid"].tolist() == ["a"]
