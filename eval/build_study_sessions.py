#!/usr/bin/env python3
"""Build the pilot-study sessions (paper Sec. 4.6).

Six document-disjoint blocks of roughly equal size are annotated in ABC-CBA
condition order, so each arm gets one early and one late slot and a single
annotator's learning/fatigue drift does not load onto one condition:

    C1  manual         auto_select_secs=0, enable_graph=False
    C2  retrieval      auto_select_secs=5, enable_graph=False
    C3  graph-assisted auto_select_secs=5, enable_graph=True
    C4  AI-assisted    C2 + the LLM's pick, read from a cache filled in advance
                       by eval/prewarm_llm.py (opt in with --conditions C1,C2,C3,C4)

Each generated session carries ``study_condition``, which switches on
per-decision provenance logging (eltool/store.py) and stamps every row with
its arm. Sessions without it are untouched.

Usage:
    python eval/build_study_sessions.py --dry-run
    python eval/build_study_sessions.py
    python eval/build_study_sessions.py --prefix study3 --conditions C1,C2,C3,C4 \
        --llm-model openai/gpt-oss-120b --llm-base-url http://127.0.0.1:18000/v1
    python eval/prewarm_llm.py --prefix study3
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SESSION = ROOT / "sessions" / "medmentions-st21pv"



def condition_order(conditions: list[str]) -> list[str]:
    """ABC...CBA: every arm gets one early and one late slot."""
    return list(conditions) + list(reversed(conditions))


# ABC-CBA: balanced across early and late slots.
CONDITION_ORDER = condition_order(["C1", "C2", "C3"])

# MedMentions ST21pv annotates mentions with bare UMLS TUIs ("T103"), which are
# opaque to a human annotator. These are the 21 ST21pv semantic types. Five of
# them (T017, T033, T058, T062, T201) were confirmed against the names the KB
# returns in `semantic_types`; the rest are absent from the index entirely,
# which is the coverage gap reported in Sec. 4.3.
ST21PV_TYPE_NAMES = {
    "T005": "Virus",
    "T007": "Bacterium",
    "T017": "Anatomical Structure",
    "T022": "Body System",
    "T031": "Body Substance",
    "T033": "Finding",
    "T037": "Injury or Poisoning",
    "T038": "Biologic Function",
    "T058": "Health Care Activity",
    "T062": "Research Activity",
    "T074": "Medical Device",
    "T082": "Spatial Concept",
    "T091": "Biomedical Occupation or Discipline",
    "T092": "Organization",
    "T097": "Professional or Occupational Group",
    "T098": "Population Group",
    "T103": "Chemical",
    "T168": "Food",
    "T170": "Intellectual Product",
    "T201": "Clinical Attribute",
    "T204": "Eukaryote",
}

# Documents already opened while gold_cui was visible on screen — unusable.
SEEN_DOCUMENTS = {"27243664", "27892411"}

GOLD_COL = "gold_cui"
UID_COL = "mention_uid"
GOLD_FILE = ROOT / "eval" / "out" / "study_gold.csv"


def write_gold(new: pd.DataFrame) -> None:
    """Merge this run's gold into GOLD_FILE (new rows win on mention_uid), so
    gold for sessions built by earlier runs (other prefixes, dev) is kept."""
    GOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    if GOLD_FILE.exists():
        old = pd.read_csv(GOLD_FILE, dtype=str, keep_default_na=False)
        old = old[~old[UID_COL].isin(set(new[UID_COL]))]
        new = pd.concat([old, new], ignore_index=True).fillna("")
    new.to_csv(GOLD_FILE, index=False)


def split_gold(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate the annotator's view from the answers.

    Returns (session_input, gold), linked by a generated ``mention_uid``.
    A uid is required because (document_id, entity_span) collides 97 times in
    MedMentions ST21pv — adding entity_text does not resolve it either.
    """
    out = df.reset_index(drop=True).copy()
    out[UID_COL] = [f"m{i:06d}" for i in range(len(out))]
    gold = out[[UID_COL, GOLD_COL]].copy()
    session = out.drop(columns=[GOLD_COL])
    return session, gold

CONDITION_SETTINGS = {
    "C1": {"auto_select_secs": 0, "enable_graph": False, "enable_auto_select": False},
    "C2": {"auto_select_secs": 5, "enable_graph": False, "enable_auto_select": True},
    "C3": {"auto_select_secs": 5, "enable_graph": True, "enable_auto_select": True},
    "C4": {"auto_select_secs": 5, "enable_graph": False, "enable_auto_select": True,
           "enable_llm_rerank": True, "llm_cache_only": True},
}


def study_session_config(
    base_cfg: dict,
    name: str,
    input_path: str,
    condition: str,
    feedback: bool,
    llm: dict | None = None,
) -> dict:
    """session.json for one study block. ``llm`` (llm_model/llm_base_url/
    llm_provider) applies to C4 only — the other arms never show the LLM."""
    cfg = dict(base_cfg)
    cfg.update(
        session_name=name,
        input_path=str(input_path),
        study_condition=condition,
        collect_feedback=feedback,
        # The study measures unaided human judgement over retrieved candidates.
        # Set BEFORE the condition settings so C4 can turn the LLM back on.
        enable_llm_rerank=False,
        llm_cache_only=False,
        # Gold is not in the session at all (see split_gold); entity_type
        # is displayed by its readable name rather than the raw TUI.
        info_columns=["document_id", "entity_type_name", "entity_span"],
        # Disabled so the suggestion the annotator accepts or overrides is
        # exactly dense rank-1 — the same quantity as the paper's Acc@1 and
        # as `_top1_id`. (The type-aware tie-break never fired here anyway:
        # it compares a TUI against the KB's group labels, which never match.)
        entity_type_col=None,
    )
    cfg.update(CONDITION_SETTINGS[condition])
    if cfg["enable_llm_rerank"] and llm:
        cfg.update({k: v for k, v in llm.items() if v is not None})
    return cfg


def select_blocks(
    df: pd.DataFrame,
    n_blocks: int,
    target_size: int,
    seed: int = 20260906,
) -> list[list[str]]:
    """Partition documents into ``n_blocks`` disjoint blocks of >= target_size
    mentions each. Returns the document ids per block.

    Documents are atomic — splitting one across conditions would leak its
    context — so each block is filled with the largest document that still fits
    the remaining gap. That keeps blocks close to target_size, which matters
    because a block that is twice the size of another confounds the condition
    with how much work it was.
    """
    counts = df.groupby("document_id").size().to_dict()
    pool = sorted(counts)
    random.Random(seed).shuffle(pool)

    blocks: list[list[str]] = []
    for _ in range(n_blocks):
        current: list[str] = []
        size = 0
        while size < target_size and pool:
            gap = target_size - size
            fits = [d for d in pool if counts[d] <= gap]
            pick = max(fits, key=lambda d: counts[d]) if fits else min(pool, key=lambda d: counts[d])
            current.append(pick)
            size += counts[pick]
            pool.remove(pick)
        if size < target_size:
            raise ValueError(
                f"corpus exhausted: filled {len(blocks)} of {n_blocks} blocks "
                f"of {target_size} mentions"
            )
        blocks.append(current)
    return blocks


def extra_blocks(
    df: pd.DataFrame,
    used_docs: set[str],
    sizes: list[int],
    seed: int = 20260906,
) -> list[list[str]]:
    """Blocks of >= each entry of ``sizes`` mentions, drawn only from documents
    not in ``used_docs`` and disjoint from one another. Leaves ``select_blocks``
    (and so the study blocks) untouched."""
    remaining = df[~df["document_id"].isin(used_docs)]
    out: list[list[str]] = []
    for i, size in enumerate(sizes):
        block = select_blocks(remaining, 1, size, seed=seed)[0]
        out.append(block)
        remaining = remaining[~remaining["document_id"].isin(block)]
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=None,
                    help="number of blocks (default: two per condition)")
    ap.add_argument("--conditions", default="C1,C2,C3",
                    help="comma-separated arms, run in ABC..CBA order (e.g. C1,C2,C3,C4)")
    ap.add_argument("--llm-model", default=None, help="C4: model shown to the annotator")
    ap.add_argument("--llm-base-url", default=None, help="C4: openai-compatible endpoint")
    ap.add_argument("--llm-provider", default="openai", help="C4: llm_provider")
    ap.add_argument("--size", type=int, default=20, help="mentions per block")
    ap.add_argument("--prefix", default="study")
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--feedback", action="store_true",
                    help="prompt for hint + confidence after every decision (Annotation Guide Sec. 8)")
    ap.add_argument("--debug", action="store_true",
                    help="also build a debug-gold session with the answers VISIBLE")
    ap.add_argument("--dev-size", type=int, default=0,
                    help="also build a dev-llm session of ~N mentions for the LLM bake-off")
    args = ap.parse_args(argv)
    conds = [c.strip().upper() for c in args.conditions.split(",") if c.strip()]
    unknown = [c for c in conds if c not in CONDITION_SETTINGS]
    if unknown:
        ap.error(f"unknown condition(s): {', '.join(unknown)}")
    if "C4" in conds and not (args.llm_model and args.llm_base_url):
        ap.error("C4 needs --llm-model and --llm-base-url (the model the annotator is shown)")
    order = condition_order(conds)
    if args.blocks is None:
        args.blocks = len(order)
    llm = {"llm_model": args.llm_model, "llm_base_url": args.llm_base_url,
           "llm_provider": args.llm_provider}

    base_cfg = json.loads((SOURCE_SESSION / "session.json").read_text())
    df = pd.read_csv(base_cfg["input_path"], dtype=str, keep_default_na=False)

    # A readable type for the annotator; the raw TUI stays for scoring.
    df["entity_type_name"] = df["entity_type"].map(ST21PV_TYPE_NAMES).fillna(df["entity_type"])

    df = df[~df["document_id"].isin(SEEN_DOCUMENTS)]

    # Answers leave the session here and live only in GOLD_FILE from now on.
    session_all, gold_all = split_gold(df)
    blocks = select_blocks(session_all, args.blocks, args.size, seed=args.seed)
    used_uids: list[str] = []

    for slot, (docs, condition) in enumerate(zip(blocks, order), start=1):
        name = f"{args.prefix}-{slot}-{condition.lower()}"
        block_df = session_all[session_all["document_id"].isin(docs)].reset_index(drop=True)
        print(f"{name:20s} {condition}  {len(docs):2d} docs  {len(block_df):3d} mentions")
        if args.dry_run:
            continue

        sdir = ROOT / "sessions" / name
        sdir.mkdir(parents=True, exist_ok=True)
        inp = sdir / f"input_{name}.csv"
        block_df.to_csv(inp, index=False)

        cfg = study_session_config(base_cfg, name, inp, condition, args.feedback, llm)
        (sdir / "session.json").write_text(json.dumps(cfg, indent=2))
        used_uids.extend(block_df[UID_COL].tolist())

    # Optional debug session: gold VISIBLE, on documents no study block uses, so
    # inspecting retrieval failures can never contaminate the study.
    if args.debug and not args.dry_run:
        used_docs = {d for b in blocks for d in b}
        spare = [d for d in session_all["document_id"].unique() if d not in used_docs]
        dbg_docs = select_blocks(
            session_all[session_all["document_id"].isin(spare)], 1, args.size, seed=args.seed + 1
        )[0]
        dbg_df = session_all[session_all["document_id"].isin(dbg_docs)].reset_index(drop=True)
        sdir = ROOT / "sessions" / "debug-gold"
        sdir.mkdir(parents=True, exist_ok=True)
        inp = sdir / "input_debug-gold.csv"
        dbg_df.to_csv(inp, index=False)
        cfg = dict(base_cfg)
        cfg.update(
            session_name="debug-gold", input_path=str(inp),
            study_condition="",              # NOT study data
            debug_show_gold=True,
            enable_llm_rerank=False, entity_type_col=None,
            info_columns=["document_id", "entity_type_name", "entity_span"],
            auto_select_secs=0, enable_auto_select=False, enable_graph=True,
        )
        (sdir / "session.json").write_text(json.dumps(cfg, indent=2))
        used_uids.extend(dbg_df[UID_COL].tolist())
        print(f"\ndebug-gold          DEBUG  {len(dbg_docs)} docs  {len(dbg_df)} mentions "
              f"(gold VISIBLE — not study data)")

    # Dev set for the LLM bake-off: disjoint from every study block, from the
    # debug-gold documents (computed as the debug branch would, even without
    # --debug) and from SEEN_DOCUMENTS (already dropped from df).
    if args.dev_size > 0:
        study_docs = {d for b in blocks for d in b}
        dbg_docs = extra_blocks(session_all, study_docs, [args.size], seed=args.seed + 1)[0]
        dev_docs = extra_blocks(
            session_all, study_docs | set(dbg_docs), [args.dev_size], seed=args.seed + 2
        )[0]
        dev_df = session_all[session_all["document_id"].isin(dev_docs)].reset_index(drop=True)
        print(f"\ndev-llm             DEV    {len(dev_docs):2d} docs  {len(dev_df):3d} mentions")
        if not args.dry_run:
            sdir = ROOT / "sessions" / "dev-llm"
            sdir.mkdir(parents=True, exist_ok=True)
            inp = sdir / "input_dev-llm.csv"
            dev_df.to_csv(inp, index=False)
            cfg = dict(base_cfg)
            cfg.update(
                session_name="dev-llm", input_path=str(inp),
                study_condition="", collect_feedback=False,
                enable_llm_rerank=False, entity_type_col=None,
                info_columns=["document_id", "entity_type_name", "entity_span"],
                **CONDITION_SETTINGS["C2"],
            )
            (sdir / "session.json").write_text(json.dumps(cfg, indent=2))
            used_uids.extend(dev_df[UID_COL].tolist())

    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        write_gold(gold_all[gold_all[UID_COL].isin(used_uids)])
        total = sum(len(session_all[session_all["document_id"].isin(b)]) for b in blocks)
        print(f"\n{args.blocks} sessions written · {total} mentions total")
        print(f"gold (kept OUT of sessions): {GOLD_FILE}")
        print(f"context folder: {base_cfg.get('context_folder')}")
        print("KB endpoint   :", base_cfg["kbs"][0]["endpoint_url"])


if __name__ == "__main__":
    main()
