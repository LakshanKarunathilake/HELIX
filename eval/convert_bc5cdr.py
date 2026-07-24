#!/usr/bin/env python3
"""
Convert a BC5CDR (BioSyn/SapBERT layout) split into a HELIX gold TSV.

Input: a dataset dir containing processed_<split>/*.concept files, each line:
    docid||start|end||type||mention||cui
(cui is a MeSH id; composite mentions are already pipe-joined, e.g. D1|D2).

Output TSV columns (what eval/score.py expects):
    document_id  entity_type  entity_text  entity_span  cui
`cui` is the gold, pipe-joined for composite mentions (any-match scoring).

Usage:
  python eval/convert_bc5cdr.py \
      --dataset /path/to/bc5cdr-chemical --split all --out eval/out
  # per split: --split dev|train|test ; use --raw for the un-normalised mentions
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _norm_cui(cui: str) -> str:
    # composite separators → '|' (BC5CDR mostly uses '|' already; be safe on +/,)
    return "|".join(p for p in cui.replace("+", "|").replace(",", "|").split("|") if p.strip())


def convert_split(dataset: Path, split: str, out_dir: Path, raw: bool) -> Path:
    sub = split if raw else f"processed_{split}"
    src_dir = dataset / sub
    if not src_dir.is_dir():
        raise SystemExit(f"Not found: {src_dir}")

    rows = []
    for cf in sorted(src_dir.glob("*.concept")):
        for line in cf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("||")
            if len(parts) < 5:
                continue
            docid, span, etype, mention, cui = parts[0], parts[1], parts[2], parts[3], parts[4]
            cui = _norm_cui(cui.strip())
            if not mention.strip() or not cui:
                continue
            rows.append({
                "document_id": docid.strip(),
                "entity_type": etype.strip(),
                "entity_text": mention.strip(),
                "entity_span": span.strip().replace("|", "-"),   # "0|16" -> "0-16"
                "cui": cui,
            })

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{dataset.name}.{split}.gold.tsv"
    with open(out_file, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["document_id", "entity_type", "entity_text", "entity_span", "cui"],
                           delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    multi = sum(1 for r in rows if "|" in r["cui"])
    print(f"  {split:5} → {out_file.name}  ({len(rows)} mentions, {multi} composite)")
    return out_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="BC5CDR dataset dir (contains processed_<split>/)")
    ap.add_argument("--split", default="all", choices=["train", "dev", "test", "all"])
    ap.add_argument("--out", default="eval/out", help="output dir for the gold TSVs")
    ap.add_argument("--raw", action="store_true", help="use raw <split>/ mentions instead of processed_<split>/")
    args = ap.parse_args()

    dataset = Path(args.dataset).expanduser().resolve()
    out_dir = Path(args.out)
    splits = ["train", "dev", "test"] if args.split == "all" else [args.split]
    print(f"Converting {dataset.name} ({'raw' if args.raw else 'processed'} mentions):")
    for s in splits:
        convert_split(dataset, s, out_dir, args.raw)


if __name__ == "__main__":
    main()
