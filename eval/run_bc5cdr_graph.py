#!/usr/bin/env python3
"""Run the MeSH graph-correction step for the BC5CDR sessions and score it.

For each session: per document, call /graph/optimize (connection-maximisation over
the MeSH graph using that session's per-split KB), batch-write the refined picks,
snapshot as `graph`, then score retrieval vs graph against gold. Finally restore
the live output to the (better) retrieval snapshot — both stay exportable by stage.
"""
from __future__ import annotations
import csv, shutil, sys, time
from pathlib import Path
import requests

API = "http://127.0.0.1:8000/api/sessions"
S = requests.Session()
SESSIONS = sys.argv[1:] or [
    "bc5cdr-chem-dev", "bc5cdr-chem-train", "bc5cdr-chem-test",
    "bc5cdr-dis-dev", "bc5cdr-dis-train", "bc5cdr-dis-test",
]


def _ids(raw: str) -> set[str]:
    # ids may be composite (e.g. 'OMIM|MeSH' dictionary SourceIDs) — split so
    # matching is any-component, else composite predictions score as false misses.
    return {p.strip() for p in (raw or "").split("|") if p.strip()}


def acc(path: Path) -> tuple[int, int, float]:
    n = cor = 0
    for row in csv.DictReader(path.open(encoding="utf-8")):
        gold = _ids(row.get("gold_cui"))
        if not gold:
            continue
        n += 1
        cor += 1 if (_ids(row.get("mesh")) & gold) else 0
    return cor, n, (cor / n * 100 if n else 0.0)


def run(name: str) -> tuple[float, float, int]:
    d = Path("sessions") / name
    docs = S.get(f"{API}/{name}/documents", timeout=60).json().get("documents", [])
    ups, refined, eb, ea, errs = [], 0, 0, 0, 0
    t0 = time.time()
    for i, doc in enumerate(docs):
        try:
            r = S.post(f"{API}/{name}/graph/optimize", json={"idx": doc["first_idx"], "kb_name": "mesh"}, timeout=120).json()
            if r.get("error"):
                errs += 1
                continue
            for sidx, cui in (r.get("selections") or {}).items():
                ups.append({"idx": int(sidx), "annotations": {"mesh": cui}, "llm_used": ""})
            eb += r.get("edges_before", 0); ea += r.get("edges_after", 0); refined += r.get("changed", 0)
        except Exception as e:
            errs += 1
            print(f"  [{name}] optimize err doc={doc.get('doc_id')}: {e}", flush=True)
        if len(ups) >= 200:
            S.post(f"{API}/{name}/rows/batch", json={"updates": ups}, timeout=300); ups = []
        if (i + 1) % 100 == 0:
            print(f"  [{name}] {i+1}/{len(docs)} refined={refined} conn {eb}->{ea} errs={errs} {time.time()-t0:.0f}s", flush=True)
    if ups:
        S.post(f"{API}/{name}/rows/batch", json={"updates": ups}, timeout=300)
    S.post(f"{API}/{name}/snapshot", params={"stage": "graph"}, timeout=120)
    _, _, ra = acc(d / "annotated.retrieval.csv")
    _, _, ga = acc(d / "annotated.graph.csv")
    # keep the better retrieval version live; graph stays available as a snapshot
    shutil.copy(d / "annotated.retrieval.csv", d / "annotated.csv")
    print(f"[{name}] retrieval {ra:.2f}% -> graph {ga:.2f}%  ({ga-ra:+.2f}); refined={refined} conn {eb}->{ea} {time.time()-t0:.0f}s", flush=True)
    return ra, ga, refined


if __name__ == "__main__":
    rows = []
    for name in SESSIONS:
        print(f"===== {name} =====", flush=True)
        ra, ga, refined = run(name)
        rows.append((name, ra, ga, refined))
    print("\n=== SUMMARY: retrieval vs graph Acc@1 ===", flush=True)
    print(f"{'session':22} {'retrieval':>10} {'graph':>8} {'Δ':>8} {'refined':>8}")
    for name, ra, ga, refined in rows:
        print(f"{name:22} {ra:9.2f}% {ga:7.2f}% {ga-ra:+7.2f} {refined:8d}", flush=True)
    print("ALL DONE", flush=True)
