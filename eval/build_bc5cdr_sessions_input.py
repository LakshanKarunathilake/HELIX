#!/usr/bin/env python3
"""Build HELIX session-input CSVs from the BC5CDR-chemical raw splits.

Each `<split>/<docid>.concept` line is `docid||start|end||type||mention||cui`
(composite gold CUIs are joined with `+`, `,` or `|`). We emit one row per
mention with the columns a HELIX session needs:

    document_id, entity_type, entity_text, entity_span, gold_cui

- `entity_text`   -> label_column (the mention to link)
- `document_id`   -> doc_id_col   (matches `<docid>.txt` for context + graph doc-level)
- `entity_span`   -> span_col     ("start-end", for context highlighting)
- `entity_type`   -> entity_type_col
- `gold_cui`      -> reference (pipe-joined MeSH ids), kept as an info column

Reads the RAW split (original-case mentions + `<docid>.txt` context files).
"""
from __future__ import annotations
import csv, re, sys
from pathlib import Path

DATASET = Path(sys.argv[1] if len(sys.argv) > 1 else
               "/Users/lakshankarunathilake/PycharmProjects/sapbert/evaluation/datasets/bc5cdr-chemical")
OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def norm_cui(raw: str) -> str:
    parts = re.split(r"[+,|]", raw.strip())
    return "|".join(p.strip() for p in parts if p.strip())


def build(split: str) -> tuple[Path, int, int]:
    src = DATASET / split
    rows: list[dict] = []
    docs = sorted(src.glob("*.concept"))
    for cf in docs:
        for line in cf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or "||" not in line:
                continue
            f = line.split("||")
            if len(f) < 5:
                continue
            docid, span, etype, mention, cui = f[0], f[1], f[2], f[3], f[4]
            rows.append({
                "document_id": docid.strip(),
                "entity_type": etype.strip(),
                "entity_text": mention.strip(),
                "entity_span": span.strip().replace("|", "-"),
                "gold_cui": norm_cui(cui),
            })
    out = OUT_DIR / f"{DATASET.name}.{split}.input.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["document_id", "entity_type", "entity_text", "entity_span", "gold_cui"])
        w.writeheader()
        w.writerows(rows)
    return out, len(rows), len(docs)


if __name__ == "__main__":
    for split in ("dev", "train", "test"):
        out, n, d = build(split)
        print(f"{split:5} -> {out.name}: {n} mentions / {d} docs")
