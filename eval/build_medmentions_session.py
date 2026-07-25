#!/usr/bin/env python3
"""Build a HELIX session input + context files from a MedMentions ST21pv
PubTator corpus.

PubTator layout per document:
    PMID|t|title
    PMID|a|abstract
    PMID<TAB>start<TAB>end<TAB>mention<TAB>SemType<TAB>UMLS:CUI
    (blank line)

Emits:
  - eval/out/medmentions-st21pv.input.csv  (document_id, entity_type, entity_text,
    entity_span, gold_cui)   -- gold_cui is the bare UMLS CUI (UMLS: prefix stripped)
  - eval/out/medmentions_ctx/<PMID>.txt    (title + ' ' + abstract, offset-aligned)
"""
from __future__ import annotations
import csv, re, sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else
           "/Users/lakshankarunathilake/Downloads/corpus_pubtator (1).txt")
OUT = Path(__file__).resolve().parent / "out"
CTX = OUT / "medmentions_ctx"
OUT.mkdir(parents=True, exist_ok=True)
CTX.mkdir(parents=True, exist_ok=True)

ann_re = re.compile(r"^(\d+)\t(\d+)\t(\d+)\t(.*?)\t([^\t]+)\t(.+)$")

titles: dict[str, str] = {}
abstracts: dict[str, str] = {}
rows: list[dict] = []

with SRC.open(encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        if "|t|" in line[:20]:
            pmid, _, txt = line.split("|", 2)
            titles[pmid] = txt
        elif "|a|" in line[:20]:
            pmid, _, txt = line.split("|", 2)
            abstracts[pmid] = txt
        else:
            m = ann_re.match(line)
            if not m:
                continue
            pmid, start, end, mention, stype, cui = m.groups()
            rows.append({
                "document_id": pmid,
                "entity_type": stype.strip(),
                "entity_text": mention.strip(),
                "entity_span": f"{start}-{end}",
                "gold_cui": cui.strip().replace("UMLS:", ""),
            })

# context files: title + ' ' + abstract (PubTator offset convention)
for pmid in titles:
    (CTX / f"{pmid}.txt").write_text(
        titles.get(pmid, "") + " " + abstracts.get(pmid, ""), encoding="utf-8")

out_csv = OUT / "medmentions-st21pv.input.csv"
with out_csv.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["document_id", "entity_type", "entity_text",
                                       "entity_span", "gold_cui"])
    w.writeheader()
    w.writerows(rows)

print(f"docs={len(titles)}  mentions={len(rows)}  unique_mentions={len({r['entity_text'] for r in rows})}")
print(f"wrote {out_csv}")
print(f"wrote {len(titles)} context files -> {CTX}")
