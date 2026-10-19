#!/usr/bin/env python3
"""Mode A: let a local LLM annotate every row of a human study session.

The model sees what a human sees in that session's condition (C1: candidates
only; C2/DEV: plus the top-1 suggestion; C3: plus graph evidence) and the result
is written as an ordinary HELIX session `ai-<model>-<source>` with the same
provenance and feedback columns humans produce.

Run:  python eval/run_ai_annotator.py --prefix study2 --model openai/gpt-oss-120b \
        --base-url http://127.0.0.1:18000/v1 --api-key-file ~/.helix_llm_key
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests  # noqa: E402
import eltool.session as session_mod  # noqa: E402  (module attr read at call time)
from eltool.config import SessionConfig  # noqa: E402
from eltool.llm_annotator import AnnotationDecision, LLMAnnotator  # noqa: E402
from eltool.llm_evidence import render_graph_evidence  # noqa: E402
from eltool.llm_reranker import LLMRerankError  # noqa: E402
from eltool.store import (  # noqa: E402
    AI_COLS, init_output, load_input, save_output, write_row,
)
from helix_api import HelixAPI  # noqa: E402


def ai_session_name(model: str, source: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"ai-{slug}-{source}"


def make_ai_session(source: str, model: str) -> str:
    """Create (idempotently) the AI twin of a source session; return its name."""
    name = ai_session_name(model, source)
    sdir = session_mod.session_dir(name)
    cfg_path = session_mod.session_config_path(name)
    if cfg_path.exists():
        return name
    src_cfg = json.loads(session_mod.session_config_path(source).read_text())
    sdir.mkdir(parents=True, exist_ok=True)
    src_input = Path(src_cfg["input_path"])
    dest = sdir / src_input.name
    shutil.copy2(src_input, dest)
    src_cfg.update({
        "session_name": name,
        "annotator": f"llm:{model}",
        "input_path": str(dest),
        "collect_feedback": True,
        "enable_llm_rerank": False,
        "study_condition": src_cfg.get("study_condition") or "DEV",
    })
    src_cfg.pop("llm_api_key", None)
    cfg_path.write_text(json.dumps(src_cfg, indent=2))
    return name


def decision_columns(d: AnnotationDecision | None, dense_ids: list[str],
                     condition: str, method: str) -> dict[str, str]:
    rank = ""
    if d is not None and d.entity_id in dense_ids:
        rank = str(dense_ids.index(d.entity_id) + 1)
    return {
        "_method": method,
        "_elapsed_ms": str(d.latency_ms) if d else "0",
        "_selected_rank": rank,
        "_top1_id": dense_ids[0] if dense_ids else "",
        "_condition": condition,
        "_revisions": "0",
        "_debug_gold_shown": "0",
        "_panels": "none",
        "_hint": d.hint if d else "",
        "_confidence": d.confidence_level if d else "",
        "_llm_confidence": f"{d.confidence:.3f}" if d else "",
        "_rationale": d.rationale if d else "",
    }


def _merged_candidates(resp: dict) -> tuple[list[dict], list[str]]:
    dense = list(resp.get("dense_candidates") or [])
    seen = {c["id"] for c in dense}
    merged = list(dense)
    for c in resp.get("lexical_candidates") or []:
        if c["id"] not in seen:
            seen.add(c["id"])
            merged.append(c)
    return merged, [c["id"] for c in dense]


def _filled(df, kb: str, i: int) -> bool:
    return bool(str(df.at[i, kb]).strip())


def run_session(source: str, annotator: LLMAnnotator, api: HelixAPI,
                limit: int | None = None) -> Counter:
    name = make_ai_session(source, annotator.model)
    cfg = SessionConfig(**json.loads(session_mod.session_config_path(name).read_text()))
    kb = cfg.kbs[0].name
    cond = cfg.study_condition or "DEV"
    out_path = session_mod.output_csv_path(name)
    inp, _ = load_input(Path(cfg.input_path), cfg.delimiter)
    df = init_output(out_path, inp, [k.name for k in cfg.kbs], cfg.annotator,
                     provenance=True, feedback=True)
    added = False
    for c in AI_COLS:
        if c not in df.columns:
            df[c] = ""
            added = True
    if added:
        save_output(out_path, df)

    stats: Counter = Counter()
    n = len(df) if limit is None else min(limit, len(df))
    for i in range(n):
        if _filled(df, kb, i):
            continue
        try:
            row = api.row(source, i)
            context = row.get("context_text") or None
            resp = api.candidates(source, row["label"], kb, context)
            cands, dense_ids = _merged_candidates(resp)
            d = None
            if cands:
                suggested = None if cond == "C1" else (dense_ids[0] if dense_ids else None)
                evidence = ""
                if cond == "C3":
                    ids = [c["id"] for c in cands]
                    evidence = render_graph_evidence(api.graph(source, ids), ids)
                d = annotator.annotate(row["label"], context, cands,
                                       suggested_id=suggested, graph_evidence=evidence)
        except (LLMRerankError, requests.RequestException) as e:
            df.at[i, "_llm_error"] = str(e)
            save_output(out_path, df)
            stats["error"] += 1
            continue
        method = "llm" if d else "llm_no_candidates"
        prov = decision_columns(d, dense_ids, cond, method)
        # Fill the whole row on the dataframe first so write_row's single save
        # carries KB + feedback + AI columns together (crash-safe resume).
        for col in ("_llm_confidence", "_rationale", "_hint", "_confidence"):
            df.at[i, col] = prov[col]
        df.at[i, "_llm_error"] = ""
        write_row(out_path, df, i, cfg.annotator, {kb: d.entity_id if d else "NIL"},
                  llm_used=f"{annotator.provider}/{annotator.model}" if d else "",
                  provenance=prov)
        stats[method] += 1

    _maybe_reflect(name, cfg, df, kb, annotator)
    return stats


def _maybe_reflect(name: str, cfg: SessionConfig, df, kb: str, annotator) -> bool:
    path = session_mod.reflection_path(name)
    if path.exists() or not all(_filled(df, kb, i) for i in range(len(df))):
        return False
    decisions = [
        AnnotationDecision(df.at[i, kb], df.at[i, "_hint"], df.at[i, "_confidence"], 0.0,
                           "", annotator.model, annotator.provider, 0, False)
        for i in range(len(df)) if df.at[i, "_method"] == "llm"
    ]
    ref = annotator.reflect(decisions)
    record = {
        "session_name": name,
        "annotator": cfg.annotator,
        "study_condition": cfg.study_condition,
        "ranking": ref["ranking"],
        "comment": str(ref.get("comment", "")).strip(),
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(record, indent=2))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", required=True, help="source session folder prefix")
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--helix", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)

    key = None
    if a.api_key_file:
        key = Path(a.api_key_file).expanduser().read_text().strip()
    annotator = LLMAnnotator(model=a.model, provider=a.provider, base_url=a.base_url, api_key=key)
    api = HelixAPI(a.helix)

    root = session_mod.SESSIONS_DIR
    sources = sorted(p.name for p in root.glob(a.prefix + "*")
                     if p.is_dir() and (p / "session.json").exists()
                     and not p.name.startswith("ai-"))
    reflected = 0
    for src in sources:
        name = ai_session_name(a.model, src)
        had = session_mod.reflection_path(name).exists()
        stats = run_session(src, annotator, api, limit=a.limit)
        out, _ = load_input(session_mod.output_csv_path(name), ",")
        kb = json.loads(session_mod.session_config_path(name).read_text())["kbs"][0]["name"]
        done = int((out[kb].astype(str).str.len() > 0).sum())
        print(f"{name}  {done}/{len(out)}  {stats}")
        failed = out[out["_llm_error"].astype(str).str.len() > 0]
        if len(failed):
            print(f"{len(failed)} rows failed ({failed.iloc[0]['_llm_error']}) — rerun to retry them")
        if not had and session_mod.reflection_path(name).exists():
            reflected += 1
    print(f"reflection written for {reflected} sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
