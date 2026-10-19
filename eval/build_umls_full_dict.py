#!/usr/bin/env python3
"""Export a full-UMLS dictionary CSV for create_sapbert_index.py.

Why this exists: the index currently in use (`bioASQ_index_20260510`) was built
from a dictionary restricted to eight semantic groups. It holds 2,230,631 of
UMLS 2025AA's 3,454,294 concepts and reaches only 73.7% of MedMentions ST21pv
gold mentions — seven of the 21 ST21pv types sit at ~0%.

Do NOT filter by `MRSTY.TUI in {the 21 ST21pv types}`: ST21pv is a *roll-up*, so
the corpus labels a concept by an ancestor of its actual TUI (C0040300 is T024
Tissue but labelled T017 Anatomical Structure). A flat TUI filter reaches only
31.8% of gold — worse than the dictionary it would replace. Correct filtering
needs the semantic network's isa closure (NET/SRSTRE1.RRF), which is not part of
a META-only download; taking everything avoids the problem entirely.

Unfiltered output: 7,229,397 aliases over 3,274,027 concepts (English,
non-suppressed), which covers 99.04% of MedMentions gold. The residual ~1%
are CUIs retired or altered since MedMentions was annotated against 2017AA.

    python eval/build_umls_full_dict.py "<META dir>" --count
    python eval/build_umls_full_dict.py "<META dir>" --out eval/out/umls_full.dict.csv

Then, from sapbert/utils/NEL:
    ./create_sapbert_index.sh <csv> <output_dir> <index_name>
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

SEP = "||"


def dedupe_aliases(aliases: list[str]) -> list[str]:
    """Drop blanks and case-insensitive duplicates, keeping first appearance.

    The index embeds one vector per alias, so a duplicate costs build time and
    memory without adding recall.
    """
    seen: set[str] = set()
    out: list[str] = []
    for a in aliases:
        a = (a or "").strip()
        if not a:
            continue
        key = a.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def build_row(cui: str, types: list[tuple[str, str]], definition: str,
              aliases: list[str]) -> dict | None:
    """One dictionary row, or None when the concept has no usable alias."""
    terms = dedupe_aliases(aliases)
    if not terms:
        return None
    tys = SEP.join(f"{t}:{n}" for t, n in sorted(set(types)))
    return {
        "CUI": cui,
        "SemanticGroups": "",           # optional metadata; needs NLM SemGroups.txt
        "SemanticTypes": tys,
        "Definition": " ".join((definition or "").split()),
        "Terms": SEP.join(terms),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("meta", help="UMLS META directory")
    ap.add_argument("--out", default="eval/out/umls_full.dict.csv")
    ap.add_argument("--count", action="store_true", help="report sizes, write nothing")
    ap.add_argument("--lat", default="ENG")
    ap.add_argument("--no-definitions", action="store_true")
    args = ap.parse_args()

    meta = Path(args.meta)
    if not (meta / "MRCONSO.RRF").exists():
        sys.exit(f"MRCONSO.RRF not found in {meta}")

    types: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with (meta / "MRSTY.RRF").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("|")
            if len(p) > 3:
                types[p[0]].append((p[1], p[3]))
    print(f"MRSTY   : {len(types):,} concepts typed", file=sys.stderr)

    defs: dict[str, str] = {}
    if not args.no_definitions and (meta / "MRDEF.RRF").exists():
        with (meta / "MRDEF.RRF").open(encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.split("|")
                if len(p) > 5 and p[0] not in defs:
                    defs[p[0]] = p[5]
        print(f"MRDEF   : {len(defs):,} definitions", file=sys.stderr)

    aliases: dict[str, list[str]] = defaultdict(list)
    with (meta / "MRCONSO.RRF").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("|")
            if len(p) > 16 and p[1] == args.lat and p[16] == "N":
                aliases[p[0]].append(p[14])
    print(f"MRCONSO : {len(aliases):,} concepts with {args.lat} atoms", file=sys.stderr)

    rows, n_alias = 0, 0
    writer = None
    out_f = None
    if not args.count:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out_f = out.open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(
            out_f, fieldnames=["CUI", "SemanticGroups", "SemanticTypes", "Definition", "Terms"])
        writer.writeheader()

    for cui, strs in aliases.items():
        row = build_row(cui, types.get(cui, []), defs.get(cui, ""), strs)
        if row is None:
            continue
        rows += 1
        n_alias += row["Terms"].count(SEP) + 1
        if writer:
            writer.writerow(row)
    if out_f:
        out_f.close()

    print(f"\nconcepts: {rows:,}\naliases : {n_alias:,}  <- vectors to embed")
    print(f"est. index size: ~{n_alias / 5_349_120 * 20:.0f} GB "
          f"(the 5,349,120-alias index occupies 20 GB)")
    if args.count:
        print("(--count: nothing written)")
    else:
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
