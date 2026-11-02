#!/usr/bin/env python3
"""Ceiling check: which document-level hints separate the gold concept from the top-1.

Starts from the type-scoped baseline (run_type_scoped_baseline.py) and reuses its
cached candidate pools. On one split (dev by default; test is kept for the single
final run) it asks, for the scoped top-k of every mention:

  abbreviations  rewrite the mention with long forms defined in its own document
                 (eval/abbrev.py) and retrieve again;
  lexical        does the mention text equal one of the candidate's KB names after
                 case and punctuation normalisation (MRCONSO strings: ENG atoms of
                 the CUI for UMLS, MSH atoms of the descriptor for MeSH)?
  graph          how many distinct concepts predicted for the OTHER mentions of the
                 same document is the candidate related to in UMLS (MRREL, SIB
                 dropped)? Anchors are those mentions' top-1 predictions, never
                 their gold; a gold-anchored run is reported only as a ceiling.
                 Mentions with the same text (raw or abbreviation-expanded) are not
                 anchors for each other: they would echo the mention's own top-1.

The setting holds throughout: a mention gives its text, span and type; nothing says
which concept it is or that two mentions are the same entity.

Commands:
  expand   find abbreviations, write the expanded mention texts to fetch:
             python eval/run_hint_analysis.py expand --dataset medmentions
             python eval/run_type_scoped_baseline.py fetch --dataset medmentions \\
                 --kb http://127.0.0.1:8765 --labels-file eval/out/typescope/medmentions.abbrev.labels.txt
  analyze  ceiling breakdown and rule comparison on one split:
             python eval/run_hint_analysis.py analyze --dataset medmentions --umls-meta "$UMLS_META"
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.abbrev import expand_mention, find_abbreviations  # noqa: E402
from eval.run_type_scoped_baseline import (DATASETS, OUT, build_scope, gold_ids,  # noqa: E402
                                           load_cache, load_rows)

MM_CTX = Path("eval/out/medmentions_ctx")
BC5CDR_PUBTATOR = Path.home() / "Downloads/CDR_Data/CDR.Corpus.v010516"
HIER = {"PAR", "CHD", "RB", "RN"}


# ── pure logic ────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", s.lower()).split())


def choose(cands, lex=None, graph=None, delta=None, lex_unique=False):
    """Pick one id from the scoped top-k [(id, score)] in dense order.

    lex: per-candidate exact-name flags -- when any candidate matches, only matches stay
    (with lex_unique, only when exactly one matches). graph: per-candidate evidence --
    the best-supported candidate among those within delta of the current leader wins;
    ties and zero evidence keep dense order.
    """
    if not cands:
        return None
    idx = list(range(len(cands)))
    if lex is not None:
        hits = [i for i in idx if lex[i]]
        if hits and (not lex_unique or len(hits) == 1):
            idx = hits
    if graph is not None:
        lead = cands[idx[0]][1]
        near = [i for i in idx if delta is None or lead - cands[i][1] <= delta]
        best = max(graph[i] for i in near)
        if best > 0:
            idx = [i for i in near if graph[i] == best]
    return cands[idx[0]][0]


def graph_counts(cand_ids, anchors, related) -> list[int]:
    """Per candidate: distinct anchors (other than itself) it is related to."""
    distinct = set(a for a in anchors if a)
    return [sum(1 for a in distinct if a != c and related(c, a)) for c in cand_ids]


def independent_others(i: int, idxs: list[int], surfaces: list[set]) -> list[int]:
    """Mentions of the document that may anchor mention i: not i, and no shared text.

    A repeat of the same text (raw or abbreviation-expanded) would only echo mention
    i's own top-1 back at it, rewarding that concept's neighbours; it is not evidence.
    """
    return [j for j in idxs if j != i and not (surfaces[j] & surfaces[i])]


def mcnemar(base: list[bool], rule: list[bool]) -> dict:
    from scipy.stats import binomtest
    fixes = sum(1 for b, r in zip(base, rule) if r and not b)
    breaks = sum(1 for b, r in zip(base, rule) if b and not r)
    p = binomtest(min(fixes, breaks), fixes + breaks, 0.5).pvalue if fixes + breaks else 1.0
    return {"fixes": fixes, "breaks": breaks, "net": fixes - breaks, "p": p}


# ── data ──────────────────────────────────────────────────────────────────────

def load_doc_texts(name: str, bc5cdr_dir: Path) -> dict[str, str]:
    """document id -> title + ' ' + abstract (the offsets' convention)."""
    if DATASETS[name]["kb_ids"] == "umls":
        return {p.stem: p.read_text(encoding="utf-8") for p in MM_CTX.glob("*.txt")}
    title, abstract = {}, {}
    for path in bc5cdr_dir.glob("*.PubTator.txt"):
        for line in open(path, encoding="utf-8"):
            parts = line.rstrip("\n").split("|", 2)
            if len(parts) == 3 and parts[1] in ("t", "a"):
                (title if parts[1] == "t" else abstract)[parts[0]] = parts[2]
    return {d: title[d] + " " + abstract.get(d, "") for d in title}


def expansions(rows, texts) -> dict[tuple, str]:
    """(document_id, span) -> expanded mention text, for mentions that change."""
    abbrevs = {d: find_abbreviations(t) for d, t in texts.items()}
    out = {}
    for r in rows:
        e = expand_mention(r["entity_text"], abbrevs.get(r["document_id"], {}))
        if e:
            out[(r["document_id"], r["entity_span"])] = e
    return out


def load_kb(name: str, split: str, ids: set[str], meta: Path) -> dict:
    """Names, CUIs and the MRREL relations among the candidate ids (cached per split)."""
    path = OUT / f"{name}.{split}.kb.pkl"
    if path.exists():
        kb = pickle.load(open(path, "rb"))
        if ids <= kb["ids"]:
            return kb
    umls = DATASETS[name]["kb_ids"] == "umls"
    names: dict[str, set] = defaultdict(set)
    cuis: dict[str, set] = defaultdict(set)
    print("  reading MRCONSO for candidate names...", flush=True)
    with open(meta / "MRCONSO.RRF", encoding="utf-8") as f:
        for line in f:
            cols = line.split("|")
            if umls and cols[1] == "ENG" and cols[0] in ids:
                names[cols[0]].add(norm(cols[14]))
                cuis[cols[0]].add(cols[0])
            elif not umls and cols[11] == "MSH" and cols[13] in ids:
                names[cols[13]].add(norm(cols[14]))
                cuis[cols[13]].add(cols[0])
    all_cuis = set().union(*cuis.values())
    rel: dict[str, dict] = defaultdict(dict)
    deg: Counter = Counter()
    print(f"  reading MRREL among {len(all_cuis)} CUIs (a few minutes)...", flush=True)
    with open(meta / "MRREL.RRF", encoding="utf-8") as f:
        for line in f:
            c1, _, _, r, c2 = line.split("|", 5)[:5]
            if r == "SIB" or c1 == c2 or c1 not in all_cuis:
                continue
            deg[c1] += 1
            if c2 in all_cuis:
                rel[c1].setdefault(c2, set()).add(r)
    kb = {"ids": set(ids), "names": dict(names), "cuis": dict(cuis), "rel": dict(rel), "deg": dict(deg)}
    pickle.dump(kb, open(path, "wb"))
    return kb


def relation_fn(kb: dict, kinds: str):
    """related(id_a, id_b) over the ids' CUIs; kinds = 'any' | 'ro' | 'hier'."""
    rel, cuis = kb["rel"], kb["cuis"]
    memo: dict[tuple, bool] = {}

    def ok(rs):
        return kinds == "any" or (kinds == "ro" and "RO" in rs) or (kinds == "hier" and rs & HIER)

    def related(a, b):
        key = (a, b) if a < b else (b, a)
        if key not in memo:
            memo[key] = any(ok(rel.get(x, {}).get(y, set()) | rel.get(y, {}).get(x, set()))
                            for x in cuis.get(a, ()) for y in cuis.get(b, ()))
        return memo[key]
    return related


# ── commands ──────────────────────────────────────────────────────────────────

def expand(args) -> None:
    rows = load_rows(args.dataset)
    texts = load_doc_texts(args.dataset, Path(args.bc5cdr_pubtator))
    missing = {r["document_id"] for r in rows} - set(texts)
    if missing:
        raise SystemExit(f"no text for {len(missing)} documents, e.g. {sorted(missing)[:3]}")
    bad = [r for r in rows if _span_text(texts[r["document_id"]], r["entity_span"]) != r["entity_text"]]
    print(f"[{args.dataset}] {len(rows)} mentions; {len(bad)} whose span does not match the text")
    exp = expansions(rows, texts)
    cache = load_cache(args.dataset)
    by_split = Counter(r["split"] for r in rows if (r["document_id"], r["entity_span"]) in exp)
    print(f"  expanded mentions: {len(exp)} ({dict(by_split)})")
    with open(OUT / f"{args.dataset}.abbrev.tsv", "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo, delimiter="\t")
        w.writerow(["split", "document_id", "entity_span", "entity_text", "expanded"])
        for r in rows:
            e = exp.get((r["document_id"], r["entity_span"]))
            if e:
                w.writerow([r["split"], r["document_id"], r["entity_span"], r["entity_text"], e])
    todo = sorted(set(exp.values()) - set(cache))
    (OUT / f"{args.dataset}.abbrev.labels.txt").write_text("".join(t + "\n" for t in todo), encoding="utf-8")
    print(f"  {len(todo)} expanded texts not yet in the pool cache -> {args.dataset}.abbrev.labels.txt")


def _span_text(text: str, span: str) -> str:
    a, b = (int(x) for x in span.split("-")[:2])
    return text[a:b]


def analyze(args) -> None:
    ds, split, k = args.dataset, args.split, args.k
    rows = [r for r in load_rows(ds) if r["split"] == split and gold_ids(r["gold_cui"])]
    texts = load_doc_texts(ds, Path(args.bc5cdr_pubtator))
    exp = expansions(rows, texts)
    cache = load_cache(ds)
    scope, cand_tuis, _ = build_scope(ds, rows, Path(args.umls_meta),
                                      Path(args.semgroups) if args.semgroups else None)

    tuis_of: dict[str, set] = {}

    def scoped(label, mtype):
        out = []
        for cid, s, types in cache.get(label, []):
            tuis_of.setdefault(cid, cand_tuis(cid, types))
            if scope.compatible(mtype, tuis_of[cid]):
                out.append((cid, s))
        return out[:k]

    ms = []
    unfetched = 0
    for r in rows:
        e = exp.get((r["document_id"], r["entity_span"]))
        base = scoped(r["entity_text"], r["entity_type"])
        if e and e not in cache:
            unfetched += 1
        ms.append({"row": r, "gold": gold_ids(r["gold_cui"]), "expanded": e, "base": base,
                   "abbrev": scoped(e, r["entity_type"]) if e and e in cache else base})

    ids = set().union(*({c for c, _ in m["base"]} | {c for c, _ in m["abbrev"]} | m["gold"] for m in ms))
    kb = load_kb(ds, split, ids, Path(args.umls_meta))
    rels = {kind: relation_fn(kb, kind) for kind in ("any", "ro", "hier")}
    names, deg, cuis = kb["names"], kb["deg"], kb["cuis"]

    def lex_flags(cands, text):
        t = norm(text)
        return [t in names.get(c, ()) for c, _ in cands]

    def degnorm(c):
        return math.log2(2 + sum(deg.get(x, 0) for x in cuis.get(c, ())))

    by_doc = defaultdict(list)
    for i, m in enumerate(ms):
        by_doc[m["row"]["document_id"]].append(i)
    for pool in ("base", "abbrev"):
        for m in ms:
            m[f"top_{pool}"] = m[pool][0][0] if m[pool] else None
    surfaces = [{norm(m["row"]["entity_text"]), norm(m["expanded"] or m["row"]["entity_text"])} for m in ms]
    for doc, idxs in by_doc.items():
        for i in idxs:
            m = ms[i]
            others = independent_others(i, idxs, surfaces)
            for pool in ("base", "abbrev"):
                cands = m[pool]
                ids_ = [c for c, _ in cands]
                text = m["expanded"] if pool == "abbrev" and m["expanded"] else m["row"]["entity_text"]
                m[f"lex_{pool}"] = lex_flags(cands, text)
                anchors = [ms[j][f"top_{pool}"] for j in others]
                for kind, fn in rels.items():
                    m[f"g_{kind}_{pool}"] = graph_counts(ids_, anchors, fn)
                m[f"gdeg_{pool}"] = [g / degnorm(c) for g, c in zip(m[f"g_any_{pool}"], ids_)]
                # tie-breakers from the KB alone: the candidate carries exactly the mention's
                # type (not only a descendant of it), and how many distinct names it has
                exact = [int(m["row"]["entity_type"] in tuis_of.get(c, ())) for c in ids_]
                m[f"names_{pool}"] = [len(names.get(c, ())) for c in ids_]
                m[f"tie_{pool}"] = [e * 10 ** 6 + n for e, n in zip(exact, m[f"names_{pool}"])]
            oracle = [g for j in others for g in ms[j]["gold"]]
            m["g_oracle_base"] = graph_counts([c for c, _ in m["base"]], oracle, rels["any"])

    def run(m, pool="base", lex=False, graph=None, **kw):
        return choose(m[pool], lex=m[f"lex_{pool}"] if lex else None,
                      graph=m[f"{graph}_{pool}"] if graph else None, **kw)

    d = args.delta
    rules = {
        "base (type-scoped)": lambda m: run(m),
        "abbrev": lambda m: run(m, "abbrev"),
        "lex": lambda m: run(m, lex=True),
        "lex (unique only)": lambda m: run(m, lex=True, lex_unique=True),
        "graph any": lambda m: run(m, graph="g_any"),
        "graph RO": lambda m: run(m, graph="g_ro"),
        "graph hier": lambda m: run(m, graph="g_hier"),
        "graph any, deg-norm": lambda m: run(m, graph="gdeg"),
        f"graph any, near-tie {d}": lambda m: run(m, graph="g_any", delta=d),
        f"lex + graph near-tie {d}": lambda m: run(m, lex=True, graph="g_any", delta=d),
        f"abbrev + lex + graph near-tie {d}": lambda m: run(m, "abbrev", lex=True, graph="g_any", delta=d),
        "ties: more KB names": lambda m: run(m, graph="names", delta=0),
        "ties: exact type, then more names": lambda m: run(m, graph="tie", delta=0),
        "abbrev + ties: more KB names": lambda m: run(m, "abbrev", graph="names", delta=0),
        "abbrev + ties: exact type, names": lambda m: run(m, "abbrev", graph="tie", delta=0),
        "CEILING graph, gold anchors": lambda m: run(m, graph="g_oracle"),
    }
    correct = {name: [fn(m) in m["gold"] for m in ms] for name, fn in rules.items()}
    base_ok = correct["base (type-scoped)"]
    n = len(ms)

    print(f"\n=== {ds} {split}: hint ceiling check on the type-scoped top-{k} ({n} mentions) ===")
    if unfetched:
        print(f"warning: {unfetched} expanded mentions have no cached pool -- run fetch --labels-file first")

    # where the gold sits (0-based position in the scoped top-k)
    pos = [next((j for j, (c, _) in enumerate(m["base"]) if c in m["gold"]), None) for m in ms]
    rank = [None if p is None else p + 1 for p in pos]
    fixable = [i for i in range(n) if pos[i]]
    print(f"gold at rank 1: {sum(1 for x in rank if x == 1)}  |  ranks 2-{k} (fixable by reranking): "
          f"{len(fixable)}  |  not in scoped top-{k}: {sum(1 for x in rank if not x)}")
    gaps = sorted(ms[i]["base"][0][1] - ms[i]["base"][pos[i]][1] for i in fixable)
    if gaps:
        q = lambda p: round(gaps[min(len(gaps) - 1, int(p * len(gaps)))], 4)
        print(f"dense score gap top-1 minus gold (fixable): median {q(0.5)}, p25 {q(0.25)}, p75 {q(0.75)}")

    def separates(i, key):
        return ms[i][key][pos[i]] > ms[i][key][0]

    print(f"\n--- of the {len(fixable)} fixable mentions, the hint prefers gold over the top-1 ---")
    ceil = {}
    for label, key in (("lexical exact name", "lex_base"), ("graph any (predicted anchors)", "g_any_base"),
                       ("graph RO (predicted anchors)", "g_ro_base"), ("graph hier (predicted anchors)", "g_hier_base"),
                       ("graph any (GOLD anchors, ceiling)", "g_oracle_base")):
        c = sum(1 for i in fixable if separates(i, key))
        ceil[label] = c
        print(f"  {label:36} {c:6}  ({100 * c / max(1, len(fixable)):.1f}%)")
    either = sum(1 for i in fixable if separates(i, "lex_base") or separates(i, "g_any_base"))
    print(f"  {'lexical or graph any (predicted)':36} {either:6}  ({100 * either / max(1, len(fixable)):.1f}%)")
    n_exp = sum(1 for m in ms if m["expanded"])
    print(f"\nabbreviation-expanded mentions: {n_exp} ({100 * n_exp / n:.1f}%)")

    print(f"\n--- rules (Acc@1; fixes / breaks against the type-scoped baseline; exact McNemar) ---")
    print(f"{'rule':38} | Acc@1  | fixes | breaks |  net  | p")
    table = {}
    for name in rules:
        acc = 100 * sum(correct[name]) / n
        mc = mcnemar(base_ok, correct[name])
        table[name] = {"acc1": round(acc, 2), **mc}
        print(f"{name:38} | {acc:6.2f} | {mc['fixes']:5} | {mc['breaks']:6} | {mc['net']:+5} | {mc['p']:.2g}")

    print(f"\n--- by mention type (Acc@1) ---")
    show = ["base (type-scoped)", "abbrev", "lex", f"graph any, near-tie {d}", "abbrev + ties: more KB names"]
    print(f"{'type':10} | {'n':>5} | " + " | ".join(f"{s[:14]:>14}" for s in show))
    per_type = defaultdict(list)
    for i, m in enumerate(ms):
        per_type[m["row"]["entity_type"]].append(i)
    for t, idxs in sorted(per_type.items(), key=lambda kv: -len(kv[1])):
        accs = [100 * sum(correct[s][i] for i in idxs) / len(idxs) for s in show]
        print(f"{t:10} | {len(idxs):5} | " + " | ".join(f"{a:14.2f}" for a in accs))

    with open(OUT / f"{ds}.{split}.hints.tsv", "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo, delimiter="\t")
        w.writerow(["document_id", "entity_span", "entity_type", "entity_text", "expanded", "gold",
                    "rank_scoped", "top1", "top1_lex", "gold_lex", "top1_graph", "gold_graph"]
                   + [f"ok:{r}" for r in rules])
        for i, m in enumerate(ms):
            ids_ = [c for c, _ in m["base"]]
            g = pos[i]
            w.writerow([m["row"]["document_id"], m["row"]["entity_span"], m["row"]["entity_type"],
                        m["row"]["entity_text"], m["expanded"] or "", m["row"]["gold_cui"], rank[i] or "",
                        ids_[0] if ids_ else "", m["lex_base"][0] if ids_ else "",
                        m["lex_base"][g] if g is not None else "", m["g_any_base"][0] if ids_ else "",
                        m["g_any_base"][g] if g is not None else ""]
                       + [int(correct[r][i]) for r in rules])
    json.dump({"dataset": ds, "split": split, "k": k, "n": n, "fixable": len(fixable),
               "expanded": n_exp, "ceiling": ceil, "rules": table},
              open(OUT / f"{ds}.{split}.hints.json", "w"), indent=2)
    print(f"\nwrote {OUT}/{ds}.{split}.hints.tsv and .json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("expand", "analyze"):
        s = sub.add_parser(name)
        s.add_argument("--dataset", choices=DATASETS, required=True)
        s.add_argument("--bc5cdr-pubtator", default=os.environ.get("BC5CDR_PUBTATOR", str(BC5CDR_PUBTATOR)),
                       help="folder with the CDR *.PubTator.txt files (env BC5CDR_PUBTATOR)")
        if name == "analyze":
            s.add_argument("--split", default="dev", choices=("train", "dev", "test"))
            s.add_argument("--k", type=int, default=10)
            s.add_argument("--delta", type=float, default=0.02,
                           help="graph may only promote candidates this close to the leader's dense score")
            s.add_argument("--umls-meta", default=os.environ.get("UMLS_META"))
            s.add_argument("--semgroups", default=os.environ.get("SEMGROUPS"))
    args = p.parse_args()
    if args.cmd == "expand":
        expand(args)
    else:
        if not args.umls_meta:
            p.error("--umls-meta (or env UMLS_META) is required")
        analyze(args)


if __name__ == "__main__":
    main()
