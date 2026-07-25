#!/usr/bin/env python3
"""Measure the Neo4j edge-query cost per document for each corpus/graph, for the
graph-cost figure and latency sentence. Reports nodes/doc, edges/doc, and
query_ms (mean/p95) per dataset, plus binned (nodes -> mean query_ms) points.
"""
from __future__ import annotations
import csv, sys, time
from collections import defaultdict
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eltool.graph_client import Neo4jGraphClient, GraphSchema  # noqa: E402

S = requests.Session()
SAMPLE = 250
DATASETS = {
    "MedMentions": dict(inp="eval/out/medmentions-st21pv.input.csv",
                        kb="http://127.0.0.1:8765/query/dense", db="umls", id_prop="cui"),
    "BC5CDR-chem": dict(inp="eval/out/bc5cdr-chemical.dev.input.csv",
                        kb="http://127.0.0.1:8773/query/dense", db="mesh", id_prop="mesh"),
    "BC5CDR-dis": dict(inp="eval/out/bc5cdr-disease.dev.input.csv",
                       kb="http://127.0.0.1:8772/query/dense", db="mesh", id_prop="mesh"),
}


def pct(x, p):
    x = sorted(x); return x[min(len(x) - 1, int(round(p / 100 * (len(x) - 1))))]


def run(name, cfg):
    rows = list(csv.DictReader(open(cfg["inp"], encoding="utf-8")))
    docs = defaultdict(list)
    for i, r in enumerate(rows):
        docs[r["document_id"]].append(i)
    gc = Neo4jGraphClient(database=cfg["db"], schema=GraphSchema(id_prop=cfg["id_prop"], name_prop="name"))
    cache = {}
    nodes, edges, qms = [], [], []
    for di, (doc, idxs) in enumerate(list(docs.items())[:SAMPLE]):
        uni = set()
        for i in idxs:
            lbl = rows[i]["entity_text"]
            if lbl not in cache:
                try:
                    cache[lbl] = [c["id"] for c in S.post(cfg["kb"], json={"label": lbl, "top_k": 10}, timeout=60).json()["results"]]
                except Exception:
                    cache[lbl] = []
            uni.update(cache[lbl])
        uni = sorted(uni)
        if not uni:
            continue
        t = time.perf_counter()
        ep = gc._edges_among(uni)
        dt = (time.perf_counter() - t) * 1000
        # skip the first (cold) query in the aggregate
        if di > 0:
            nodes.append(len(uni)); edges.append(len(ep)); qms.append(dt)
    gc.close()
    import statistics as st
    print(f"[{name}] docs={len(qms)}  nodes/doc mean={st.mean(nodes):.0f} p95={pct(nodes,95)}  "
          f"edges/doc mean={st.mean(edges):.0f}  query_ms mean={st.mean(qms):.1f} p95={pct(qms,95):.1f}", flush=True)
    # binned nodes -> mean query_ms
    binw = 20; bins = defaultdict(list)
    for n, q in zip(nodes, qms):
        bins[(n // binw) * binw].append(q)
    pts = [(b + binw // 2, st.mean(v)) for b, v in sorted(bins.items()) if len(v) >= 3]
    print(f"[{name}] scatter (node-bin, mean_ms): " + " ".join(f"({x},{y:.1f})" for x, y in pts), flush=True)
    return pts


if __name__ == "__main__":
    for name, cfg in DATASETS.items():
        run(name, cfg)
    print("ALL DONE")
