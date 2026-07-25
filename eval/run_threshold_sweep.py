#!/usr/bin/env python3
"""Sweep the confidence-gated graph optimiser's pin threshold on MedMentions and
BC5CDR, and report Acc@1 at each threshold. Efficient: candidates (with scores)
and Neo4j edges are fetched ONCE per document; the gate is then re-run in memory
for every threshold.
"""
from __future__ import annotations
import csv, sys, time
from collections import defaultdict
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eltool.graph_client import Neo4jGraphClient, GraphSchema  # noqa: E402

S = requests.Session()
TOP_N = 10
MARGIN = 0.10                       # fixed switch margin
DOC_SAMPLE = 400
PINS = [("retrieval", None), (0.95, MARGIN), (0.90, MARGIN), (0.85, MARGIN),
        (0.80, MARGIN), (0.70, MARGIN), (0.50, MARGIN), ("ungated", 10.0)]

DATASETS = {
    "MedMentions": dict(inp="eval/out/medmentions-st21pv.input.csv",
                        kb="http://127.0.0.1:8765/query/dense", db="umls", id_prop="cui"),
    "BC5CDR-chem": dict(inp="eval/out/bc5cdr-chemical.dev.input.csv",
                        kb="http://127.0.0.1:8773/query/dense", db="mesh", id_prop="mesh"),
    "BC5CDR-dis": dict(inp="eval/out/bc5cdr-disease.dev.input.csv",
                       kb="http://127.0.0.1:8772/query/dense", db="mesh", id_prop="mesh"),
}


def idset(x): return {i.strip() for i in (x or "").split("|") if i.strip()}


def gate(mention_cands, adj, pin, margin, iters=2):
    """Confidence-gated, confidence-ordered selection. pin=None -> retrieval top-1.
    Returns {idx: cui}."""
    order = [i for i, _ in mention_cands]
    cm = dict(mention_cands)
    sel = {i: cm[i][0][0] for i in order}                       # retrieval baseline
    if pin is None:
        return sel
    conf = {i: cm[i][0][1] for i in order}
    pinned = {i for i in order if conf[i] >= pin}
    free = sorted((i for i in order if i not in pinned), key=lambda i: conf[i], reverse=True)
    for _ in range(max(1, iters)):
        changed = False
        for i in free:
            top = cm[i][0][1]; rank = {c: k for k, (c, _) in enumerate(cm[i])}
            near = [c for c, s in cm[i] if top - s <= margin]
            others = {sel[j] for j in sel if j != i}
            best = max(near, key=lambda c: (len(adj.get(c, set()) & others), c == sel[i], -rank[c]))
            if best != sel[i]:
                sel[i] = best; changed = True
        if not changed:
            break
    return sel


def run(name, cfg):
    rows = list(csv.DictReader(open(cfg["inp"], encoding="utf-8")))
    docs = defaultdict(list)
    for i, r in enumerate(rows):
        docs[r["document_id"]].append(i)
    items = list(docs.items())[:DOC_SAMPLE]
    gc = Neo4jGraphClient(database=cfg["db"],
                          schema=GraphSchema(id_prop=cfg["id_prop"], name_prop="name"))
    cache: dict[str, list] = {}
    prepared = []                                  # [(mention_cands, adj)]
    t0 = time.time()
    for d, (doc, idxs) in enumerate(items):
        mc = []
        for i in idxs:
            lbl = rows[i]["entity_text"]
            if lbl not in cache:
                try:
                    res = S.post(cfg["kb"], json={"label": lbl, "top_k": TOP_N}, timeout=60).json()["results"]
                    cache[lbl] = [(c["id"], float(c.get("score") or 0.0)) for c in res]
                except Exception:
                    cache[lbl] = []
            if cache[lbl]:
                mc.append((i, cache[lbl]))
        if not mc:
            continue
        universe = sorted({c for _, cs in mc for c, _ in cs})
        adj = defaultdict(set)
        for a, b in gc._edges_among(universe):
            adj[a].add(b); adj[b].add(a)
        prepared.append((mc, adj))
        if (d + 1) % 100 == 0:
            print(f"  [{name}] prepared {d+1}/{len(items)} docs, uniq={len(cache)} {time.time()-t0:.0f}s", flush=True)
    gc.close()

    gold = {i: idset(rows[i]["gold_cui"]) for i in range(len(rows))}
    # retrieval Recall@10 from the same sample (ceiling)
    r10n = r10c = 0
    for mc, _ in prepared:
        for i, cands in mc:
            g = gold[i]
            if not g:
                continue
            r10n += 1
            r10c += 1 if any(idset(c) & g for c, _ in cands) else 0
    recall10 = r10c / r10n * 100 if r10n else 0.0
    results = {"_r10": recall10}
    for pin, margin in PINS:
        n = cor = changed = pinned_tot = 0
        if pin == "retrieval":
            pv, mv = None, MARGIN                       # no graph = retrieval top-1
        elif pin == "ungated":
            pv, mv = 1.01, 10.0                         # nothing pinned, no margin = full edge-max
        else:
            pv, mv = pin, (margin or MARGIN)            # protect mentions scoring >= pin
        for mc, adj in prepared:
            base = {i: cm[0][0] for i, cm in mc}
            sel = gate(mc, adj, pv, mv)
            for i, _ in mc:
                g = gold[i]
                if not g:
                    continue
                n += 1; cor += 1 if idset(sel[i]) & g else 0
                changed += 1 if sel[i] != base[i] else 0
        results[pin] = (cor / n * 100 if n else 0.0, changed, n)
        print(f"  [{name}] pin={pin!s:9} Acc@1={results[pin][0]:.2f}%  changed={changed}", flush=True)
    return results


if __name__ == "__main__":
    allres = {name: run(name, cfg) for name, cfg in DATASETS.items()}
    print("\n=== SWEEP SUMMARY (Acc@1 %, changed) ===")
    hdr = "pin_threshold | " + " | ".join(DATASETS)
    print(hdr)
    print("Recall@10      | " + " | ".join(f"{allres[n]['_r10']:.2f}%" for n in DATASETS))
    for pin, _ in PINS:
        cells = []
        for name in DATASETS:
            a, ch, n = allres[name][pin]
            cells.append(f"{a:.2f}% (Δ{ch})")
        print(f"{str(pin):13} | " + " | ".join(cells))
    print("ALL DONE")
