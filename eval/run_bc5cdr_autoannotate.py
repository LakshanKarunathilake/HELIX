#!/usr/bin/env python3
"""Headless two-step auto-annotation for the BC5CDR-chemical HELIX sessions.

Mirrors the Auto-annotate modal, server-side, over the running API:
  Step 1 (retrieval): per mention, fetch candidates and take the best (type-aware
                      tie-break within score_tie_threshold), batch-write.
  Step 2 (graph):     per document, run MeSH graph connection-maximisation and
                      batch-write the refined selections.
Each step writes a per-step snapshot (retrieval / graph) for export.

Run:  python eval/run_bc5cdr_autoannotate.py            # dev, train, test
      python eval/run_bc5cdr_autoannotate.py dev        # one split
"""
from __future__ import annotations
import csv, sys, time
from pathlib import Path

import requests

API = "http://127.0.0.1:8000/api/sessions"
KB = "mesh"
TIE = 0.02
ENTITY_TYPE_COL = "entity_type"
OUT = Path(__file__).resolve().parent / "out"
S = requests.Session()


def pick_best(dense, lexical, entity_type):
    pool = {}
    for c in (dense or []) + (lexical or []):
        cid = c.get("id")
        if not cid:
            continue
        if cid not in pool or (c.get("score") or 0) > (pool[cid].get("score") or 0):
            pool[cid] = c
    if not pool:
        return None
    ranked = sorted(pool.values(), key=lambda c: c.get("score") or 0, reverse=True)
    top = ranked[0].get("score") or 0
    tied = [c for c in ranked if top - (c.get("score") or 0) <= TIE]
    if entity_type:
        for c in tied:
            groups = [g.strip() for g in str(c.get("semantic_groups") or "").replace(",", "|").split("|")]
            if entity_type in groups:
                return c
    return ranked[0]


def flush(name, updates):
    if not updates:
        return
    r = S.post(f"{API}/{name}/rows/batch", json={"updates": updates}, timeout=300)
    r.raise_for_status()
    updates.clear()


def step1_retrieval(split: str):
    name = f"bc5cdr-chem-{split}"
    rows = list(csv.DictReader((OUT / f"bc5cdr-chemical.{split}.input.csv").open(encoding="utf-8")))
    cache: dict[str, str | None] = {}
    updates, done, miss = [], 0, 0
    t0 = time.time()
    for idx, row in enumerate(rows):
        label = row["entity_text"]
        et = row.get(ENTITY_TYPE_COL, "")
        key = f"{label}\t{et}"
        if key in cache:
            best_id = cache[key]
        else:
            try:
                resp = S.post(f"{API}/{name}/candidates",
                              json={"label": label, "kb_name": KB, "enable_dense": True, "enable_lexical": False},
                              timeout=60).json()
                best = pick_best(resp.get("dense_candidates"), resp.get("lexical_candidates"), et)
                best_id = best["id"] if best else None
            except Exception as e:
                best_id = None
                print(f"  [{split}] cand err idx={idx} {label!r}: {e}", flush=True)
            cache[key] = best_id
        if best_id:
            updates.append({"idx": idx, "annotations": {KB: best_id}, "llm_used": ""})
            done += 1
        else:
            miss += 1
        if len(updates) >= 200:
            flush(name, updates)
            print(f"  [{split}] step1 {idx+1}/{len(rows)} done={done} miss={miss} uniq={len(cache)} {time.time()-t0:.0f}s", flush=True)
    flush(name, updates)
    S.post(f"{API}/{name}/snapshot", params={"stage": "retrieval"}, timeout=120)
    print(f"[{split}] STEP1 complete: {done} annotated, {miss} misses, {len(cache)} unique, {time.time()-t0:.0f}s", flush=True)


def step2_graph(split: str):
    name = f"bc5cdr-chem-{split}"
    docs = S.get(f"{API}/{name}/documents", timeout=60).json().get("documents", [])
    updates, refined, eb, ea, errs = [], 0, 0, 0, 0
    t0 = time.time()
    for i, d in enumerate(docs):
        try:
            r = S.post(f"{API}/{name}/graph/optimize", json={"idx": d["first_idx"], "kb_name": KB}, timeout=120).json()
            if r.get("error"):
                errs += 1
                continue
            for sidx, cui in (r.get("selections") or {}).items():
                updates.append({"idx": int(sidx), "annotations": {KB: cui}, "llm_used": ""})
            eb += r.get("edges_before", 0); ea += r.get("edges_after", 0); refined += r.get("changed", 0)
        except Exception as e:
            errs += 1
            print(f"  [{split}] optimize err doc={d.get('doc_id')}: {e}", flush=True)
        if len(updates) >= 200:
            flush(name, updates)
        if (i + 1) % 100 == 0:
            print(f"  [{split}] step2 {i+1}/{len(docs)} refined={refined} conn {eb}->{ea} errs={errs} {time.time()-t0:.0f}s", flush=True)
    flush(name, updates)
    S.post(f"{API}/{name}/snapshot", params={"stage": "graph"}, timeout=120)
    print(f"[{split}] STEP2 complete: {refined} refined, connections {eb}->{ea}, {errs} errs, {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    splits = sys.argv[1:] or ["dev", "train", "test"]
    for sp in splits:
        print(f"===== {sp} =====", flush=True)
        step1_retrieval(sp)
        step2_graph(sp)
        prog = S.get(f"{API}/bc5cdr-chem-{sp}/progress", timeout=30).json()
        print(f"[{sp}] progress: {prog}", flush=True)
    print("ALL DONE", flush=True)
