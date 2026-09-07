"""Provenance logging for the pilot study (Sec. 4.6).

The study needs, per mention, how the annotation was arrived at: whether the
annotator accepted the auto-selected pick or overrode it, how long they took,
and which rank they ended on. These tests pin that behaviour down.

Crucially, provenance is *opt-in*: the existing BC5CDR / MedMentions sessions
that Table 3 is computed from must keep their exact column layout.
"""

from __future__ import annotations

import pandas as pd
import pytest

from eltool.store import (
    ANNOTATOR_COL,
    LLM_COL,
    PROVENANCE_COLS,
    init_output,
    output_columns,
    write_row,
)


@pytest.fixture
def input_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entity_text": ["lung", "DM"],
            "document_id": ["d1", "d2"],
        }
    )


def test_output_columns_unchanged_when_provenance_disabled():
    """Regression guard: existing sessions must not gain columns."""
    cols = output_columns(["entity_text", "document_id"], ["UMLS"])

    assert cols == ["entity_text", "document_id", ANNOTATOR_COL, LLM_COL, "UMLS"]


def test_output_columns_appends_provenance_when_enabled():
    cols = output_columns(["entity_text"], ["UMLS"], provenance=True)

    assert cols[-len(PROVENANCE_COLS) :] == PROVENANCE_COLS
    assert cols[:3] == ["entity_text", ANNOTATOR_COL, LLM_COL]


def test_write_row_records_provenance(tmp_path, input_df):
    out = tmp_path / "annotated.csv"
    df = init_output(out, input_df, ["UMLS"], "lakshan", provenance=True)

    write_row(
        out,
        df,
        0,
        "lakshan",
        {"UMLS": "C0024109"},
        provenance={
            "_method": "user_override",
            "_elapsed_ms": "4210",
            "_selected_rank": "3",
            "_top1_id": "C0225730",
            "_condition": "C3",
        },
    )

    saved = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert saved.at[0, "_method"] == "user_override"
    assert saved.at[0, "_elapsed_ms"] == "4210"
    assert saved.at[0, "_selected_rank"] == "3"
    assert saved.at[0, "_top1_id"] == "C0225730"
    assert saved.at[0, "_condition"] == "C3"


def test_write_row_without_provenance_leaves_earlier_values_intact(tmp_path, input_df):
    """A later write that carries no provenance must not blank an earlier one."""
    out = tmp_path / "annotated.csv"
    df = init_output(out, input_df, ["UMLS"], "lakshan", provenance=True)

    df = write_row(
        out, df, 0, "lakshan", {"UMLS": "C1"}, provenance={"_method": "auto_accept"}
    )
    write_row(out, df, 0, "lakshan", {"UMLS": "C2"})

    saved = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert saved.at[0, "UMLS"] == "C2"
    assert saved.at[0, "_method"] == "auto_accept"


def test_init_output_does_not_add_provenance_to_existing_session(tmp_path, input_df):
    """Opening a pre-study session must not rewrite its schema."""
    out = tmp_path / "annotated.csv"
    init_output(out, input_df, ["UMLS"], "lakshan")

    reopened = init_output(out, input_df, ["UMLS"], "lakshan")

    assert not [c for c in reopened.columns if c in PROVENANCE_COLS]
