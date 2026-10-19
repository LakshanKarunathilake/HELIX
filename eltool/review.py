"""Mode C: the LLM pre-annotates every row; the human works a review queue.

Routing (per row, from the AI's decision and dense rank-1):
  auto       sure, agrees with rank-1, not NIL -> accepted without the human
  spotcheck  a fixed random share of auto-eligible rows -> human WITHOUT the
             AI's answer, to measure auto-accept quality honestly
  review     everything else -> human WITH the AI's pick shown
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

import eltool.session as session_mod
from eltool.config import SessionConfig
from eltool.llm_reranker import LLMRerankError
from eltool.store import (
    REVIEW_COLS, ROUTE_COL, init_output, load_input, save_output, write_row,
)

DEFAULT_SEED = 20261016


def route(level: str, llm_id: str, top1_id: str, rng: random.Random,
          spotcheck_rate: float) -> str:
    eligible = level == "sure" and bool(llm_id) and llm_id != "NIL" and llm_id == top1_id
    if not eligible:
        return "review"
    # Draw only for auto-eligible rows so the sample is reproducible from the seed.
    return "spotcheck" if rng.random() < spotcheck_rate else "auto"


def review_reasons(level: str, llm_id: str, top1_id: str) -> list[str]:
    reasons = []
    if level != "sure":
        reasons.append("the AI was not sure")
    if llm_id != top1_id and llm_id != "NIL":
        reasons.append("its pick differs from the top suggestion")
    if llm_id == "NIL":
        reasons.append("the AI found no match")
    return reasons


def review_payload(out_df: pd.DataFrame, idx: int) -> dict | None:
    """What the annotator UI may show for this row; None for non-queue rows."""
    if ROUTE_COL not in out_df.columns:
        return None
    r = str(out_df.at[idx, ROUTE_COL] or "")
    if r == "spotcheck":
        return {"route": "spotcheck"}     # the AI's pick stays hidden
    if r != "review":
        return None
    cell = lambda c: str(out_df.at[idx, c] or "") if c in out_df.columns else ""  # noqa: E731
    level, llm_id, top1 = cell("_llm_level"), cell("_llm_id"), cell("_top1_id")
    return {"route": "review", "llm_id": llm_id, "label": cell("_llm_label"),
            "level": level, "rationale": cell("_rationale"),
            "reasons": review_reasons(level, llm_id, top1)}


def preannotate(session: str, annotator, api, seed: int = DEFAULT_SEED) -> Counter:
    # Imported lazily: eval/ is only on sys.path when run from the CLI or tests.
    from run_ai_annotator import _filled, _merged_candidates, decision_columns

    cfg = SessionConfig(**json.loads(session_mod.session_config_path(session).read_text()))
    kb = cfg.kbs[0].name
    cond = cfg.study_condition or ""
    out_path = session_mod.output_csv_path(session)
    inp, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    df = init_output(out_path, inp, [k.name for k in cfg.kbs], cfg.annotator,
                     provenance=True, feedback=True)
    added = False
    for c in REVIEW_COLS:
        if c not in df.columns:
            df[c] = ""
            added = True
    if added:
        save_output(out_path, df)

    rng = random.Random(seed)
    stats: Counter = Counter()
    for i in range(len(df)):
        if str(df.at[i, ROUTE_COL]).strip():
            continue
        try:
            row = api.row(session, i)
            context = row.get("context_text") or None
            resp = api.candidates(session, row["label"], kb, context)
            cands, dense_ids = _merged_candidates(resp)
            d = None
            if cands:
                d = annotator.annotate(row["label"], context, cands,
                                       suggested_id=dense_ids[0] if dense_ids else None)
        except (LLMRerankError, requests.RequestException) as e:
            df.at[i, "_llm_error"] = str(e)
            save_output(out_path, df)
            stats["error"] += 1
            continue
        top1 = dense_ids[0] if dense_ids else ""
        llm_id = d.entity_id if d else "NIL"
        level = d.confidence_level if d else "unsure"
        r = route(level, llm_id, top1, rng, cfg.review_spotcheck_rate)
        df.at[i, "_llm_id"] = llm_id
        df.at[i, "_llm_level"] = level
        df.at[i, "_llm_confidence"] = f"{d.confidence:.3f}" if d else ""
        df.at[i, "_llm_label"] = next((c.get("label", "") for c in cands
                                       if c["id"] == llm_id), "") if d else ""
        df.at[i, "_rationale"] = d.rationale if d else ""
        df.at[i, "_top1_id"] = top1
        df.at[i, "_llm_error"] = ""
        df.at[i, ROUTE_COL] = r
        if r == "auto":
            prov = decision_columns(d, dense_ids, cond, "llm_auto")
            prov["_llm_id"] = llm_id
            write_row(out_path, df, i, f"llm:{annotator.model}", {kb: llm_id},
                      llm_used=f"{annotator.provider}/{annotator.model}", provenance=prov)
        else:
            save_output(out_path, df)
        stats[r] += 1
    return stats
