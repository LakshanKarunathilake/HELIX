#!/usr/bin/env python3
"""Dense-retrieval evaluation of MedMentions ST21pv against UMLS (bioASQ index).

Queries the KB directly (cached per unique mention), scoring every mention
occurrence. Reports Acc@1 and Recall@10 overall and per semantic type.
"""
from __future__ import annotations
import csv, time
from collections import defaultdict
import requests

import os
KB = os.environ.get("MM_KB", "http://127.0.0.1:8765/query/dense")
INP = "eval/out/medmentions-st21pv.input.csv"
S = requests.Session()


def idset(x): return {i.strip() for i in (x or "").split("|") if i.strip()}


def main():
    rows = list(csv.DictReader(open(INP, encoding="utf-8")))
    cache: dict[str, list[str]] = {}
    tot = [0, 0, 0]                        # n, acc1, recall10
    by_t = defaultdict(lambda: [0, 0, 0])
    t0 = time.time()
    for i, r in enumerate(rows):
        lbl = r["entity_text"]; gold = idset(r["gold_cui"])
        if not gold:
            continue
        if lbl not in cache:
            try:
                res = S.post(KB, json={"label": lbl, "top_k": 10}, timeout=60).json()["results"]
                cache[lbl] = [c["id"] for c in res]
            except Exception:
                cache[lbl] = []
        ids = cache[lbl]
        h1 = 1 if (ids and idset(ids[0]) & gold) else 0
        h10 = 1 if any(idset(x) & gold for x in ids[:10]) else 0
        tot[0] += 1; tot[1] += h1; tot[2] += h10
        t = r["entity_type"]; by_t[t][0] += 1; by_t[t][1] += h1; by_t[t][2] += h10
        if (i + 1) % 20000 == 0:
            print(f"  {i+1}/{len(rows)}  uniq={len(cache)}  acc@1={tot[1]/tot[0]*100:.2f}%  "
                  f"R@10={tot[2]/tot[0]*100:.2f}%  {time.time()-t0:.0f}s", flush=True)
    n, a1, r10 = tot
    print(f"\n=== MedMentions ST21pv vs UMLS (bioASQ, {203282} mentions, dense) ===", flush=True)
    print(f"OVERALL  n={n}  Acc@1={a1/n*100:.2f}%  Recall@10={r10/n*100:.2f}%  "
          f"unique={len(cache)}  {time.time()-t0:.0f}s", flush=True)
    print("\nBy semantic type (top 12 by count):", flush=True)
    for t, (tn, ta, tr) in sorted(by_t.items(), key=lambda kv: -kv[1][0])[:12]:
        print(f"  {t:6} n={tn:6d}  Acc@1={ta/tn*100:5.2f}%  R@10={tr/tn*100:5.2f}%", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
