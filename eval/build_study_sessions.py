#!/usr/bin/env python3
"""Build the pilot-study sessions (paper Sec. 4.6).

Six document-disjoint blocks of roughly equal size are annotated in ABC-CBA
condition order, so each arm gets one early and one late slot and a single
annotator's learning/fatigue drift does not load onto one condition:

    C1  manual         auto_select_secs=0, enable_graph=False
    C2  retrieval      auto_select_secs=5, enable_graph=False
    C3  graph-assisted auto_select_secs=5, enable_graph=True

Each generated session carries ``study_condition``, which switches on
per-decision provenance logging (eltool/store.py) and stamps every row with
its arm. Sessions without it are untouched.

Usage:
    python eval/build_study_sessions.py --dry-run
    python eval/build_study_sessions.py
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

# ABC-CBA: balanced across early and late slots.
CONDITION_ORDER = ["C1", "C2", "C3", "C3", "C2", "C1"]

CONDITION_SETTINGS = {
    "C1": {"auto_select_secs": 0, "enable_graph": False, "enable_auto_select": False},
    "C2": {"auto_select_secs": 5, "enable_graph": False, "enable_auto_select": True},
    "C3": {"auto_select_secs": 5, "enable_graph": True, "enable_auto_select": True},
}


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=6)
    ap.add_argument("--size", type=int, default=20, help="mentions per block")
    ap.add_argument("--prefix", default="study")
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base_cfg = json.loads((SOURCE_SESSION / "session.json").read_text())
    df = pd.read_csv(base_cfg["input_path"], dtype=str, keep_default_na=False)

    blocks = select_blocks(df, args.blocks, args.size, seed=args.seed)

    for slot, (docs, condition) in enumerate(zip(blocks, CONDITION_ORDER), start=1):
        name = f"{args.prefix}-{slot}-{condition.lower()}"
        block_df = df[df["document_id"].isin(docs)].reset_index(drop=True)
        print(f"{name:20s} {condition}  {len(docs):2d} docs  {len(block_df):3d} mentions")
        if args.dry_run:
            continue

        sdir = ROOT / "sessions" / name
        sdir.mkdir(parents=True, exist_ok=True)
        inp = sdir / f"input_{name}.csv"
        block_df.to_csv(inp, index=False)

        cfg = dict(base_cfg)
        cfg.update(
            session_name=name,
            input_path=str(inp),
            study_condition=condition,
            # The study measures unaided human judgement over retrieved candidates.
            enable_llm_rerank=False,
            **CONDITION_SETTINGS[condition],
        )
        (sdir / "session.json").write_text(json.dumps(cfg, indent=2))

    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        total = sum(len(df[df["document_id"].isin(b)]) for b in blocks)
        print(f"\n{args.blocks} sessions written · {total} mentions total")
        print(f"context folder: {base_cfg.get('context_folder')}")
        print("KB endpoint   :", base_cfg["kbs"][0]["endpoint_url"])


if __name__ == "__main__":
    main()
