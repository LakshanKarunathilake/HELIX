#!/usr/bin/env python3
"""Hybrid (dense+lexical) retrieval evaluation on the BC5CDR HELIX sessions.

For each unique mention we fetch dense + lexical candidates from the session's KB
and compute top-1 under four configurations:
  - dense            : SAPBERT+FAISS top-1
  - lexical          : BM25+n-gram top-1
  - hybrid (max)     : the code's current fusion (dedup by id, keep max score)
  - hybrid (RRF)     : rank-based Reciprocal Rank Fusion (k=60) over both lists
Scored by component-wise any-match; broken down by mention length.
"""
from __future__ import annotations
import csv, sys, time
from collections import defaultdict
import requests

API = "http://127.0.0.1:8000/api/sessions"
S = requests.Session()
RRF_K = 60
SESSIONS = {
    "chem": [("dev", "chemical"), ("train", "chemical"), ("test", "chemical")],
    "dis":  [("dev", "disease"), ("train", "disease"), ("test", "disease")],
}


def ids(x): return {i.strip() for i in (x or "").split("|") if i.strip() and i.strip()[0] in "CD"}
def hit(pred, gold): return bool(ids(pred) & gold)
def bucket(t): return "short(<=4)" if len(t) <= 4 else "med(5-8)" if len(t) <= 8 else "long(>8)"


def fuse_max(dense, lexical):
    by = {}
    for c in dense + lexical:
        cid = c["id"]
        if cid not in by or (c.get("score") or 0) > (by[cid].get("score") or 0):
            by[cid] = c
    return sorted(by.values(), key=lambda c: c.get("score") or 0, reverse=True)


def fuse_rrf(dense, lexical):
    sc = defaultdict(float)
    for rank, c in enumerate(dense):
        sc[c["id"]] += 1.0 / (RRF_K + rank + 1)
    for rank, c in enumerate(lexical):
        sc[c["id"]] += 1.0 / (RRF_K + rank + 1)
    return sorted(sc, key=lambda k: sc[k], reverse=True)


def eval_split(track, split):
    name = f"bc5cdr-{'chem' if track=='chem' else 'dis'}-{split}"
    corpus = "chemical" if track == "chem" else "disease"
    rows = list(csv.DictReader(open(f"eval/out/bc5cdr-{corpus}.{split}.input.csv", encoding="utf-8")))
    cache = {}
    agg = {c: [0, 0] for c in ("dense", "lexical", "hyb_max", "hyb_rrf", "hyb_rrf@10")}
    byb = defaultdict(lambda: {c: [0, 0] for c in ("dense", "lexical", "hyb_rrf")})
    t0 = time.time()
    for r in rows:
        lbl = r["entity_text"]; gold = ids(r["gold_cui"])
        if not gold:
            continue
        if lbl not in cache:
            resp = S.post(f"{API}/{name}/candidates",
                          json={"label": lbl, "kb_name": "mesh", "enable_dense": True, "enable_lexical": True},
                          timeout=90).json()
            cache[lbl] = (resp.get("dense_candidates") or [], resp.get("lexical_candidates") or [])
        dense, lex = cache[lbl]
        d1 = dense[0]["id"] if dense else ""
        l1 = lex[0]["id"] if lex else ""
        hm = fuse_max(dense, lex); hm1 = hm[0]["id"] if hm else ""
        hr = fuse_rrf(dense, lex); hr1 = hr[0] if hr else ""
        b = bucket(lbl)
        for key, pred in (("dense", d1), ("lexical", l1), ("hyb_max", hm1), ("hyb_rrf", hr1)):
            agg[key][0] += 1; agg[key][1] += hit(pred, gold)
        agg["hyb_rrf@10"][0] += 1
        agg["hyb_rrf@10"][1] += 1 if any(hit(x, gold) for x in hr[:10]) else 0
        for key, pred in (("dense", d1), ("lexical", l1), ("hyb_rrf", hr1)):
            byb[b][key][0] += 1; byb[b][key][1] += hit(pred, gold)
    def pct(k): n, c = agg[k]; return f"{c/n*100:5.2f}%" if n else "  -  "
    print(f"[{track}/{split}] n={agg['dense'][0]} uniq={len(cache)} {time.time()-t0:.0f}s  "
          f"dense={pct('dense')} lexical={pct('lexical')} hyb_max={pct('hyb_max')} "
          f"hyb_rrf={pct('hyb_rrf')} (R@10={pct('hyb_rrf@10')})", flush=True)
    for b in ("short(<=4)", "med(5-8)", "long(>8)"):
        d = byb[b]
        if d["dense"][0]:
            f = lambda k: f"{d[k][1]/d[k][0]*100:5.1f}%"
            print(f"        {b:11} n={d['dense'][0]:4d}  dense={f('dense')} lexical={f('lexical')} hyb_rrf={f('hyb_rrf')}", flush=True)


if __name__ == "__main__":
    for track in ("chem", "dis"):
        for split, _ in SESSIONS[track]:
            eval_split(track, split)
    print("ALL DONE", flush=True)
