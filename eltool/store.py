from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

ANNOTATOR_COL = "annotator"
LLM_COL = "llm_used"   # records which LLM (provider/model) produced an accepted annotation

# Per-decision provenance for the pilot study. Opt-in per session: sessions
# annotated before the study keep their original column layout untouched.
PROVENANCE_COLS = ["_method", "_elapsed_ms", "_selected_rank", "_top1_id", "_condition",
                   "_revisions", "_debug_gold_shown"]

# Written once, on a row's first save, and immutable thereafter: Prev/Next
# navigation re-saves a row, and the first encounter is the decision we measure.
FROZEN_PROVENANCE_COLS = ["_method", "_elapsed_ms", "_selected_rank", "_top1_id",
                          "_condition", "_debug_gold_shown"]
REVISION_COL = "_revisions"

# Per-decision feedback (Annotation Guide Sec. 8). Opt-in per session via
# `collect_feedback`. `_panels` is the passive log, frozen on first save like
# provenance; `_hint`/`_confidence` are written by the post-save prompt.
PANELS_COL = "_panels"
HINT_COL = "_hint"
CONFIDENCE_COL = "_confidence"
FEEDBACK_COLS = [PANELS_COL, HINT_COL, CONFIDENCE_COL]


def detect_delimiter(path: Path) -> str:
    sample = Path(path).read_text(errors="replace")[:4096]
    if not sample.strip():
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        ext = Path(path).suffix.lower()
        return "\t" if ext == ".tsv" else ","


def load_input(path: Path, delimiter: str | None = None) -> tuple[pd.DataFrame, str]:
    delim = delimiter or detect_delimiter(path)
    df = pd.read_csv(path, sep=delim, dtype=str, keep_default_na=False)
    return df, delim


def output_columns(
    input_cols: list[str],
    kb_names: list[str],
    provenance: bool = False,
    feedback: bool = False,
) -> list[str]:
    cols = list(input_cols)
    if ANNOTATOR_COL not in cols:
        cols.append(ANNOTATOR_COL)
    if LLM_COL not in cols:
        cols.append(LLM_COL)
    for kb in kb_names:
        if kb not in cols:
            cols.append(kb)
    extra = (PROVENANCE_COLS if provenance else []) + (FEEDBACK_COLS if feedback else [])
    for c in extra:
        if c not in cols:
            cols.append(c)
    return cols


def init_output(
    out_path: Path,
    input_df: pd.DataFrame,
    kb_names: list[str],
    annotator: str,
    provenance: bool = False,
    feedback: bool = False,
) -> pd.DataFrame:
    """Create the output dataframe (or load existing). Output is reconciled
    against the current input columns + KB names."""
    cols = output_columns(list(input_df.columns), kb_names,
                          provenance=provenance, feedback=feedback)

    if Path(out_path).exists():
        existing = pd.read_csv(out_path, dtype=str, keep_default_na=False)
        for c in cols:
            if c not in existing.columns:
                existing[c] = ""
        # Preserve any extra columns at the end, but reorder known ones first.
        ordered = cols + [c for c in existing.columns if c not in cols]
        existing = existing[ordered]
        if len(existing) < len(input_df):
            extra = input_df.iloc[len(existing):].copy()
            for c in cols:
                if c not in extra.columns:
                    extra[c] = ""
            extra = extra[ordered]
            existing = pd.concat([existing, extra], ignore_index=True)
        # Refresh annotator on blank rows only
        existing.loc[existing[ANNOTATOR_COL] == "", ANNOTATOR_COL] = ""
        save_output(out_path, existing)
        return existing

    df = input_df.copy()
    df[ANNOTATOR_COL] = ""
    df[LLM_COL] = ""
    for kb in kb_names:
        df[kb] = ""
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]
    save_output(out_path, df)
    return df


def save_output(out_path: Path, df: pd.DataFrame) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)


def write_row(
    out_path: Path,
    df: pd.DataFrame,
    row_idx: int,
    annotator: str,
    kb_values: dict[str, str],
    llm_used: str | None = None,
    provenance: dict[str, str] | None = None,
    panels: str | None = None,
) -> pd.DataFrame:
    if panels is not None and PANELS_COL in df.columns:
        if not str(df.at[row_idx, PANELS_COL] or "").strip():   # first save only
            df.at[row_idx, PANELS_COL] = panels
    df.at[row_idx, ANNOTATOR_COL] = annotator
    if llm_used is not None:            # "" clears it (human override); None leaves it untouched
        df.at[row_idx, LLM_COL] = llm_used
    for kb, val in kb_values.items():
        df.at[row_idx, kb] = val
    if provenance and REVISION_COL in df.columns:
        first_save = not str(df.at[row_idx, "_method"] or "").strip()
        if first_save:
            for col, val in provenance.items():
                if col in df.columns:   # ignore unknown keys
                    df.at[row_idx, col] = val
            df.at[row_idx, REVISION_COL] = "0"
        else:
            prior = str(df.at[row_idx, REVISION_COL] or "0").strip() or "0"
            df.at[row_idx, REVISION_COL] = str(int(prior) + 1)
    save_output(out_path, df)
    return df


def write_feedback(
    out_path: Path,
    df: pd.DataFrame,
    row_idx: int,
    hint: str,
    confidence: str,
) -> pd.DataFrame:
    """Record (or correct) the hint and confidence for an already-saved row."""
    df.at[row_idx, HINT_COL] = hint
    df.at[row_idx, CONFIDENCE_COL] = confidence
    save_output(out_path, df)
    return df


def find_resume_index(df: pd.DataFrame, kb_names: list[str]) -> int:
    """Return index of the first row that does not have all KB columns filled."""
    if not kb_names:
        return 0
    mask = pd.Series(True, index=df.index)
    for kb in kb_names:
        mask &= df[kb].astype(str).str.len() > 0
    unfilled = (~mask).to_numpy().nonzero()[0]
    if len(unfilled) == 0:
        return len(df)
    return int(unfilled[0])
