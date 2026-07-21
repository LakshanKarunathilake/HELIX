#!/usr/bin/env python3
"""
Evaluate HELIX retrieval configurations against an already-annotated gold dataset.

Gold is multi-valued: a mention may map to several correct CUIs joined with '|'.
A prediction is counted correct (any-match) if the top-1 CUI is in the gold set.

Reports, per configuration (lexical / dense / hybrid / hybrid+LLM):
  - Acc@1        top-1 prediction is in the gold set
  - Recall@k     gold set overlaps the top-k candidates
with breakdowns by entity type and by mention length (the short-mention story).

Run:
  python eval/score.py --config eval/eval_config.yaml
The KB server must be running (the same one HELIX queries).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eltool.config import KBConfig            # noqa: E402
from eltool.kb_client import Candidate, KBClient  # noqa: E402
from eltool.llm_reranker import LLMRerankError, LLMReranker  # noqa: E402

CONFIGS = ["lexical", "dense", "hybrid", "hybrid_llm"]


def _derived(url: str, suffix: str) -> str:
    return url.rstrip("/") + suffix


def fuse(dense: list[Candidate], lexical: list[Candidate]) -> list[Candidate]:
    by_id: dict[str, Candidate] = {}
    for c in [*dense, *lexical]:
        prev = by_id.get(c.id)
        if prev is None or (c.score or 0) > (prev.score or 0):
            by_id[c.id] = c
    return sorted(by_id.values(), key=lambda c: c.score or 0, reverse=True)


def length_bucket(text: str) -> str:
    n = len(text)
    return "≤4" if n <= 4 else "5-8" if n <= 8 else ">8"


def _pred_ids(cfg_name: str, dense, lexical, top_k, reranker, text, context):
    if cfg_name == "lexical":
        ranked = lexical
    elif cfg_name == "dense":
        ranked = dense
    else:
        ranked = fuse(dense, lexical)
        if cfg_name == "hybrid_llm" and reranker is not None and ranked:
            try:
                res = reranker.rank(text, context, ranked)
                order = {cid: i for i, cid in enumerate(res.ranking)}
                ranked = sorted(ranked, key=lambda c: order.get(c.id, 10_000))
            except LLMRerankError:
                pass  # fall back to hybrid order
    return [c.id for c in ranked[:top_k]]


def run_eval(rows, query_fn, configs, top_k, reranker=None) -> dict:
    res = {c: {"total": 0, "acc1": 0, "recallk": 0,
               "by_type": defaultdict(lambda: [0, 0]),
               "by_len": defaultdict(lambda: [0, 0])} for c in configs}
    for i, r in enumerate(rows, 1):
        gold = r["gold"]
        if not gold:
            continue
        dense, lexical = query_fn(r["text"])
        for cfg_name in configs:
            top_ids = _pred_ids(cfg_name, dense, lexical, top_k, reranker, r["text"], r.get("context"))
            top1 = top_ids[0] if top_ids else None
            correct1 = 1 if (top1 is not None and top1 in gold) else 0
            recallk = 1 if gold & set(top_ids) else 0
            R = res[cfg_name]
            R["total"] += 1
            R["acc1"] += correct1
            R["recallk"] += recallk
            R["by_type"][r["etype"]][0] += 1
            R["by_type"][r["etype"]][1] += correct1
            R["by_len"][r["lenb"]][0] += 1
            R["by_len"][r["lenb"]][1] += correct1
        if i % 100 == 0:
            print(f"  … {i} mentions")
    return res


def _pct(n, d):
    return f"{100*n/d:5.1f}%" if d else "   – "


def print_report(res: dict) -> None:
    print("\n" + "=" * 60)
    print(f"{'config':<12}{'N':>7}{'Acc@1':>9}{'Recall@k':>11}")
    print("-" * 60)
    for cfg_name, R in res.items():
        print(f"{cfg_name:<12}{R['total']:>7}{_pct(R['acc1'], R['total']):>9}{_pct(R['recallk'], R['total']):>11}")

    print("\nAcc@1 by mention length:")
    print(f"  {'len':<6}" + "".join(f"{c:>12}" for c in res))
    for lb in ["≤4", "5-8", ">8"]:
        cells = []
        for c in res:
            t, ok = res[c]["by_len"].get(lb, [0, 0])
            cells.append(_pct(ok, t))
        print(f"  {lb:<6}" + "".join(f"{x:>12}" for x in cells))

    print("\nAcc@1 by entity type:")
    types = sorted({t for c in res for t in res[c]["by_type"]})
    print(f"  {'type':<12}" + "".join(f"{c:>12}" for c in res))
    for et in types:
        cells = []
        for c in res:
            t, ok = res[c]["by_type"].get(et, [0, 0])
            cells.append(_pct(ok, t))
        print(f"  {et:<12}" + "".join(f"{x:>12}" for x in cells))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="eval/eval_config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())

    cols = cfg["columns"]
    sep = cfg.get("gold_sep", "|")
    gold_df = pd.read_csv(cfg["gold_file"], sep=cfg.get("delimiter", "\t"), dtype=str, keep_default_na=False)
    rows = []
    for _, r in gold_df.iterrows():
        text = str(r[cols["text"]]).strip()
        gold = {g.strip() for g in str(r[cols["gold"]]).split(sep) if g.strip()}
        rows.append({
            "text": text,
            "gold": gold,
            "etype": str(r.get(cols.get("entity_type", ""), "")) if cols.get("entity_type") else "",
            "lenb": length_bucket(text),
            "context": None,
        })
    print(f"Loaded {len(rows)} gold mentions ({sum(1 for x in rows if x['gold'])} with gold CUIs)")

    kb = dict(cfg["kb"])
    top_k = int(kb.get("top_k", 10))
    url = kb["endpoint_url"]
    dense_client = KBClient(KBConfig(**{**kb, "endpoint_url": _derived(url, "/dense")}))
    lex_client = KBClient(KBConfig(**{**kb, "endpoint_url": _derived(url, "/lexical")}))

    def query_fn(text: str):
        try:
            dense = dense_client.query(text)
        except Exception:
            dense = []
        try:
            lexical = lex_client.query(text)
        except Exception:
            lexical = []
        return dense, lexical

    configs = cfg.get("configs", CONFIGS)
    reranker = None
    if "hybrid_llm" in configs:
        lc = cfg.get("llm", {})
        reranker = LLMReranker(model=lc.get("model", "claude-haiku-4-5"), top_n=lc.get("top_n", 10))

    res = run_eval(rows, query_fn, configs, top_k, reranker)
    print_report(res)

    out = cfg.get("output")
    if out:
        serial = {c: {k: (dict(v) if isinstance(v, defaultdict) else v) for k, v in R.items()} for c, R in res.items()}
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(serial, indent=2, ensure_ascii=False))
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
