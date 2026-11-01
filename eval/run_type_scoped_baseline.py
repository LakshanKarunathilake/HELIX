#!/usr/bin/env python3
"""Type-scoped retrieval baseline (option A: filter after retrieval).

Each mention comes with its text, span and type, and nothing else. Dense retrieval
returns a pool of candidates; candidates whose semantic type does not fit the
mention's type are dropped, and the first k that remain are the scoped list. Every
later step (lexical rules, graph) is measured against this scoped baseline.

Two steps, so the slow part runs once:

  fetch   query the KB server for a pool of candidates per unique mention text and
          cache it (resumable):
            python eval/run_type_scoped_baseline.py fetch --dataset medmentions \
                --kb http://127.0.0.1:8765
  score   filter the cached pools by type and report Acc@1 / Acc@k per split,
          before and after filtering:
            python eval/run_type_scoped_baseline.py score --dataset medmentions \
                --umls-meta "$UMLS_META" --semgroups "$SEMGROUPS"

Per split it reports, besides accuracy: mentions left with no compatible candidate,
mentions whose gold concept fails its own type rule (the mapping is too strict
there, or the label is suspect), and gold concepts retrieved but past the top k.
Outputs go to eval/out/typescope/ (gitignored).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.type_map import (TypeScope, load_cui_tuis, load_mesh_tuis,  # noqa: E402
                           load_semgroups, load_tui_tree, parse_tuis)

OUT = Path("eval/out/typescope")
SPLITS = ("train", "dev", "test")
MM_SPLIT_FILES = {"train": "trng", "dev": "dev", "test": "test"}

DATASETS = {
    "medmentions": dict(kb_ids="umls",
                        inputs={None: "eval/out/medmentions-st21pv.input.csv"},
                        split_dir="eval/out/medmentions_splits"),
    "bc5cdr-chem": dict(kb_ids="mesh",
                        inputs={s: f"eval/out/bc5cdr-chemical.{s}.input.csv" for s in SPLITS}),
    "bc5cdr-dis": dict(kb_ids="mesh",
                       inputs={s: f"eval/out/bc5cdr-disease.{s}.input.csv" for s in SPLITS}),
}


# ── pure scoring logic ────────────────────────────────────────────────────────

def gold_ids(x: str | None) -> set[str]:
    """Gold ids of one mention; composites are '|'-joined, '-1' marks no concept."""
    return {i.strip() for i in (x or "").split("|") if i.strip() and i.strip() != "-1"}


def hit_rank(ids: list[str], gold: set[str]) -> int | None:
    """1-based rank of the first id in gold, or None."""
    for r, cid in enumerate(ids, 1):
        if cid in gold:
            return r
    return None


def score_mention(pool, gold: set[str], ok, gold_tuis: set[str] | None) -> dict:
    """pool = [(id, score, tuis)] in retrieval order; ok(tuis) -> type fits."""
    scoped = [cid for cid, _, tuis in pool if ok(tuis)]
    rank_all = hit_rank([cid for cid, _, _ in pool], gold)
    rank_scoped = hit_rank(scoped, gold)
    return {
        "rank_all": rank_all,
        "rank_scoped": rank_scoped,
        "rank_fallback": rank_scoped if scoped else rank_all,
        "n_compatible": len(scoped),
        "gold_compatible": None if gold_tuis is None else ok(gold_tuis),
    }


def summarize(recs: list[dict], k: int) -> dict:
    n = len(recs)

    def pct(pred):
        return round(100.0 * sum(1 for r in recs if pred(r)) / n, 2) if n else 0.0

    out = {"n": n}
    for key in ("all", "scoped", "fallback"):
        rk = f"rank_{key}"
        out[f"acc1_{key}"] = pct(lambda r: r[rk] == 1)
        out[f"acck_{key}"] = pct(lambda r: r[rk] is not None and r[rk] <= k)
    out["no_compatible"] = sum(1 for r in recs if r["n_compatible"] == 0)
    out["gold_incompatible"] = sum(1 for r in recs if r["gold_compatible"] is False)
    out["gold_beyond_k_all"] = sum(1 for r in recs if r["rank_all"] is not None and r["rank_all"] > k)
    return out


# ── data ──────────────────────────────────────────────────────────────────────

def load_rows(name: str) -> list[dict]:
    cfg = DATASETS[name]
    doc_split: dict[str, str] = {}
    if "split_dir" in cfg:
        for split, stem in MM_SPLIT_FILES.items():
            path = Path(cfg["split_dir"]) / f"corpus_pubtator_pmids_{stem}.txt"
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    doc_split[line.strip()] = split
    rows = []
    for split, path in cfg["inputs"].items():
        for r in csv.DictReader(open(path, encoding="utf-8")):
            r["split"] = split or doc_split.get(r["document_id"], "")
            rows.append(r)
    return rows


def cache_path(name: str) -> Path:
    return OUT / f"{name}.pool.jsonl"


def load_cache(name: str) -> dict[str, list]:
    p = cache_path(name)
    cache = {}
    if p.exists():
        for line in open(p, encoding="utf-8"):
            rec = json.loads(line)
            cache[rec["label"]] = rec["cands"]
    return cache


# ── commands ──────────────────────────────────────────────────────────────────

def fetch(args) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = sorted({r["entity_text"] for r in load_rows(args.dataset)})
    done = set(load_cache(args.dataset))
    todo = [lbl for lbl in labels if lbl not in done]
    print(f"[{args.dataset}] {len(labels)} unique mention texts, {len(todo)} to fetch", flush=True)
    url = args.kb.rstrip("/") + "/query/dense"
    session = requests.Session()
    with open(cache_path(args.dataset), "a", encoding="utf-8") as out:
        for i, lbl in enumerate(todo, 1):
            try:
                res = session.post(url, json={"label": lbl, "top_k": args.pool}, timeout=120).json()["results"]
            except Exception as exc:                       # keep going; a re-run retries it
                print(f"  failed: {lbl!r}: {exc}", flush=True)
                continue
            cands = [[c["id"], float(c.get("score") or 0.0), c.get("semantic_types") or ""] for c in res]
            out.write(json.dumps({"label": lbl, "cands": cands}) + "\n")
            if i % 1000 == 0:
                out.flush()
                print(f"  {i}/{len(todo)}", flush=True)
    print("done", flush=True)


def build_scope(name: str, rows: list[dict], umls_meta: Path, semgroups: Path | None):
    """-> (TypeScope, candidate-TUI lookup, gold-TUI lookup)."""
    mrsty = umls_meta / "MRSTY.RRF"
    if DATASETS[name]["kb_ids"] == "umls":
        scope = TypeScope.medmentions(load_tui_tree(mrsty), {r["entity_type"] for r in rows})
        golds = set().union(*(gold_ids(r["gold_cui"]) for r in rows))
        cui_tuis = load_cui_tuis(mrsty, golds)
        return scope, (lambda cid, types: parse_tuis(types)), cui_tuis
    if semgroups is None:
        raise SystemExit("--semgroups (or env SEMGROUPS) is required for BC5CDR")
    cached = OUT / "mesh_tuis.json"
    if cached.exists():
        mesh_tuis = {k: set(v) for k, v in json.load(open(cached)).items()}
    else:
        print("  building MeSH id -> semantic types from MRCONSO (one-off)...", flush=True)
        mesh_tuis = load_mesh_tuis(umls_meta / "MRCONSO.RRF", mrsty)
        json.dump({k: sorted(v) for k, v in mesh_tuis.items()}, open(cached, "w"))
    return TypeScope.bc5cdr(load_semgroups(semgroups)), (lambda cid, types: mesh_tuis.get(cid, set())), mesh_tuis


def score(args) -> None:
    rows = load_rows(args.dataset)
    cache = load_cache(args.dataset)
    scope, cand_tuis, id_tuis = build_scope(args.dataset, rows, Path(args.umls_meta),
                                            Path(args.semgroups) if args.semgroups else None)

    by_split: dict[str, list] = defaultdict(list)
    by_type: dict[tuple, list] = defaultdict(list)
    missing = nil = 0
    per_mention = OUT / f"{args.dataset}.mentions.tsv"
    with open(per_mention, "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo, delimiter="\t")
        w.writerow(["split", "document_id", "entity_type", "entity_text", "gold", "rank_all",
                    "rank_scoped", "n_compatible", "gold_compatible"])
        for r in rows:
            gold = gold_ids(r["gold_cui"])
            if not gold:
                nil += 1
                continue
            if r["entity_text"] not in cache:
                missing += 1
            pool = [(cid, s, cand_tuis(cid, types)) for cid, s, types in cache.get(r["entity_text"], [])]
            gt = [id_tuis[g] for g in gold if g in id_tuis]
            mtype = r["entity_type"]
            rec = score_mention(pool, gold, lambda t: scope.compatible(mtype, t),
                                set().union(*gt) if gt else None)
            by_split[r["split"]].append(rec)
            by_type[(r["split"], mtype)].append(rec)
            w.writerow([r["split"], r["document_id"], mtype, r["entity_text"], r["gold_cui"],
                        rec["rank_all"], rec["rank_scoped"], rec["n_compatible"], rec["gold_compatible"]])

    k = args.k
    summary = {s: summarize(by_split[s], k) for s in SPLITS if by_split.get(s)}
    print(f"\n=== {args.dataset}: type-scoped baseline (pool {args.pool_label}, k={k}) ===")
    print(f"skipped: {nil} mentions without a gold concept; {missing} mentions not in the pool cache")
    print("split  |      n | Acc@1 all | Acc@1 scoped | Acc@{k} all | Acc@{k} scoped | no compat | gold incompat | gold past k".format(k=k))
    for s, m in summary.items():
        print(f"{s:6} | {m['n']:6} | {m['acc1_all']:9} | {m['acc1_scoped']:12} | {m['acck_all']:10} | "
              f"{m['acck_scoped']:13} | {m['no_compatible']:9} | {m['gold_incompatible']:13} | {m['gold_beyond_k_all']}")

    print(f"\n--- by mention type, {args.report_split} split ---")
    print("type      |     n | Acc@1 all | Acc@1 scoped | Acc@{k} scoped | gold incompat".format(k=k))
    types = sorted({t for s, t in by_type if s == args.report_split},
                   key=lambda t: -len(by_type[(args.report_split, t)]))
    per_type = {}
    for t in types:
        m = summarize(by_type[(args.report_split, t)], k)
        per_type[t] = m
        print(f"{t:9} | {m['n']:5} | {m['acc1_all']:9} | {m['acc1_scoped']:12} | {m['acck_scoped']:12} | {m['gold_incompatible']}")

    json.dump({"dataset": args.dataset, "k": k, "by_split": summary,
               f"by_type_{args.report_split}": per_type},
              open(OUT / f"{args.dataset}.summary.json", "w"), indent=2)
    print(f"\nwrote {per_mention} and {OUT / (args.dataset + '.summary.json')}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="cache a candidate pool per unique mention text")
    f.add_argument("--dataset", choices=DATASETS, required=True)
    f.add_argument("--kb", required=True, help="KB server base URL, e.g. http://127.0.0.1:8765")
    f.add_argument("--pool", type=int, default=100, help="candidates fetched per mention (server max 100)")
    s = sub.add_parser("score", help="filter the cached pools by type and report accuracy")
    s.add_argument("--dataset", choices=DATASETS, required=True)
    s.add_argument("--k", type=int, default=10)
    s.add_argument("--umls-meta", default=os.environ.get("UMLS_META"),
                   help="UMLS META folder with MRSTY.RRF and MRCONSO.RRF (env UMLS_META)")
    s.add_argument("--semgroups", default=os.environ.get("SEMGROUPS"),
                   help="SemGroups.txt, needed for BC5CDR (env SEMGROUPS)")
    s.add_argument("--report-split", default="dev", choices=SPLITS)
    args = p.parse_args()
    if args.cmd == "fetch":
        fetch(args)
    else:
        if not args.umls_meta:
            p.error("--umls-meta (or env UMLS_META) is required")
        args.pool_label = "cached"
        score(args)


if __name__ == "__main__":
    main()
