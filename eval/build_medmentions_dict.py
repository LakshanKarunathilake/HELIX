#!/usr/bin/env python3
"""Build the MedMentions ST21pv target dictionary from UMLS 2025AA.

ST21pv target = UMLS concepts whose semantic type is one of 21 TUIs, restricted to
the preferred source vocabularies (Mohan & Li, 2019). Output CSV (CUI, SemanticTypes,
Terms) is the input to create_sapbert_index.py.

Pass 1: MRSTY  -> CUIs with a TUI in the 21 ST21pv types (+ their TUIs).
Pass 2: MRCONSO -> English, preferred-vocab, non-suppressed atom strings per CUI.
"""
from __future__ import annotations
import csv, sys
from collections import defaultdict
from pathlib import Path

META = Path(sys.argv[1] if len(sys.argv) > 1 else
            "/Users/lakshankarunathilake/Documents/UMLS dataset/2025AA 2/META")
OUT = Path(__file__).resolve().parent / "out" / "medmentions-st21pv.dict.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

ST21PV_TUIS = {
    "T005", "T007", "T017", "T022", "T031", "T033", "T037", "T038", "T058",
    "T062", "T074", "T082", "T091", "T092", "T097", "T098", "T103", "T168",
    "T170", "T201", "T204",
}
ST21PV_SABS = {
    "CPT", "FMA", "GO", "HGNC", "HPO", "ICD10", "ICD10CM", "ICD9CM", "MDR",
    "MSH", "MTH", "NCBI", "NCI", "NDDF", "NDFRT", "OMIM", "RXNORM", "SNOMEDCT_US",
}
COUNT_ONLY = "--count" in sys.argv


def main():
    # Pass 1 — MRSTY: CUI -> set(TUI) restricted to the 21 types
    cui_tui: dict[str, set[str]] = defaultdict(set)
    with (META / "MRSTY.RRF").open(encoding="utf-8") as f:
        for line in f:
            p = line.split("|")
            if len(p) > 1 and p[1] in ST21PV_TUIS:
                cui_tui[p[0]].add(p[1])
    print(f"Pass 1: {len(cui_tui):,} CUIs in the 21 ST21pv semantic types", flush=True)
    if COUNT_ONLY:
        return

    # Pass 2 — MRCONSO: aliases for those CUIs, English + preferred vocab + not suppressed
    aliases: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    n = 0
    with (META / "MRCONSO.RRF").open(encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split("|")
            if len(p) < 17:
                continue
            cui, lat, sab, string, suppress = p[0], p[1], p[11], p[14], p[16]
            if lat != "ENG" or suppress in ("O", "E") or cui not in cui_tui or sab not in ST21PV_SABS:
                continue
            s = string.strip()
            key = s.lower()
            if s and key not in seen[cui]:
                seen[cui].add(key); aliases[cui].append(s)
            n += 1
            if n % 2_000_000 == 0:
                print(f"  MRCONSO scanned {n:,} matching atoms, {len(aliases):,} CUIs so far", flush=True)

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["CUI", "SemanticTypes", "Terms"])
        for cui, al in aliases.items():
            w.writerow([cui, "|".join(sorted(cui_tui[cui])), "|".join(al)])
    tot_al = sum(len(a) for a in aliases.values())
    print(f"Wrote {len(aliases):,} concepts / {tot_al:,} aliases -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
