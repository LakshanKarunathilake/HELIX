#!/usr/bin/env python3
"""Two-step annotation of the MedMentions session, written DIRECTLY to the
session files (bypassing the per-call CSV reload in the API, which is O(n) and
far too slow at 203k rows / 4,392 docs).

Step 1 retrieval: cached dense KB -> best id per mention -> write annotated.csv.
Step 2 graph:     per document, greedy connection-maximisation over the UMLS
                  Neo4j graph (eltool.graph_client) -> graph snapshot + stats.
Both scored by component-wise any-match. GRAPH_SAMPLE docs (0 = all).
"""
from __future__ import annotations
import csv, os, shutil, sys, time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eltool.graph_client import Neo4jGraphClient          # noqa: E402
from eltool.session import output_csv_path, output_snapshot_path  # noqa: E402

KB = "http://127.0.0.1:8765/query/dense"
INP = "eval/out/medmentions-st21pv.input.csv"
SESSION = "medmentions-st21pv"
TOP_N = 10
GRAPH_SAMPLE = int(os.environ.get("GRAPH_SAMPLE", "0"))   # 0 = all docs
COLS = ["document_id", "entity_type", "entity_text", "entity_span", "gold_cui",
        "annotator", "llm_used", "umls"]
S = requests.Session()


def idset(x): return {i.strip() for i in (x or "").split("|") if i.strip()}


def score(rows, preds):
    n = a = 0
    for r, p in zip(rows, preds):
        g = idset(r["gold_cui"])
        if not g:
            continue
        n += 1; a += 1 if idset(p) & g else 0
    return a / n * 100 if n else 0.0, n


def main():
    rows = list(csv.DictReader(open(INP, encoding="utf-8")))

    # ── Step 1: retrieval (client-cached by mention) ──────────────────────────
    cache: dict[str, list[str]] = {}
    preds: list[str] = []
    t0 = time.time()
    for i, r in enumerate(rows):
        lbl = r["entity_text"]
        if lbl not in cache:
            try:
                cache[lbl] = [c["id"] for c in S.post(KB, json={"label": lbl, "top_k": TOP_N}, timeout=60).json()["results"]]
            except Exception:
                cache[lbl] = []
        preds.append(cache[lbl][0] if cache[lbl] else "")
        if (i + 1) % 50000 == 0:
            print(f"  retrieval {i+1}/{len(rows)} uniq={len(cache)} {time.time()-t0:.0f}s", flush=True)
    ra, n = score(rows, preds)
    print(f"[STEP1] retrieval done: {len(cache)} unique, {time.time()-t0:.0f}s  Acc@1={ra:.2f}% (n={n})", flush=True)

    out = pd.DataFrame(rows)
    out["annotator"] = "Lakshan Karunathilake"; out["llm_used"] = ""; out["umls"] = preds
    out_path = str(output_csv_path(SESSION))
    out[COLS].to_csv(out_path, index=False)
    shutil.copy(out_path, str(output_snapshot_path(SESSION, "retrieval")))

    # ── Step 2: graph optimisation (direct, UMLS graph) ───────────────────────
    gc = Neo4jGraphClient()
    docs = defaultdict(list)
    for i, r in enumerate(rows):
        docs[r["document_id"]].append(i)
    items = list(docs.items())
    if GRAPH_SAMPLE:
        items = items[:GRAPH_SAMPLE]
    gpreds = list(preds)
    st = {"docs": 0, "nodes": 0, "edges": 0, "ms": 0.0, "changed": 0, "eb": 0, "ea": 0}
    t1 = time.time()
    for di, (doc_id, idxs) in enumerate(items):
        mc = [(i, cache[rows[i]["entity_text"]][:TOP_N]) for i in idxs if cache.get(rows[i]["entity_text"])]
        if not mc:
            continue
        try:
            res = gc.optimize_selection(mc)
        except Exception as e:
            print(f"  optimise err {doc_id}: {e}", flush=True); continue
        for sidx, cui in res["selections"].items():
            gpreds[int(sidx)] = cui
        st["docs"] += 1; st["nodes"] += res.get("nodes_queried", 0); st["edges"] += res.get("edges_found", 0)
        st["ms"] += res.get("query_ms", 0.0); st["changed"] += res.get("changed", 0)
        st["eb"] += res.get("edges_before", 0); st["ea"] += res.get("edges_after", 0)
        if (di + 1) % 500 == 0:
            print(f"  graph {di+1}/{len(items)} changed={st['changed']} {time.time()-t1:.0f}s", flush=True)
    gc.close()

    out["umls"] = gpreds
    out[COLS].to_csv(str(output_snapshot_path(SESSION, "graph")), index=False)
    out["umls"] = preds; out[COLS].to_csv(out_path, index=False)   # keep retrieval live (better)

    ga, _ = score(rows, gpreds)
    d = max(st["docs"], 1)
    print(f"[STEP2] graph done: {st['docs']} docs, {time.time()-t1:.0f}s  Acc@1={ga:.2f}%", flush=True)
    print(f"[GRAPH STATS] nodes/doc={st['nodes']/d:.1f} edges/doc={st['edges']/d:.1f} "
          f"query_ms/doc={st['ms']/d:.1f} total_nodes={st['nodes']} total_edges={st['edges']} "
          f"changed={st['changed']} connections {st['eb']}->{st['ea']}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
