#!/usr/bin/env python3
"""Can a better-scored graph help entity linking? Training-free variants, measured offline.

Builds on run_hint_analysis.py (same pools, type scoping, abbreviation expansion and the
cached MRREL slice), starting from its "abbrev" pool -- the best training-free retrieval
so far. For every mention the graph may only re-rank the scoped top-k, and only among
candidates within DELTA of the leader's dense score (the tool's switch margin).

Graph score of a candidate c, over a set of document anchors A:
  relations  which MRREL REL codes count as a link (SIB is never loaded):
               all         every REL
               no-struct   drop AQ/QB (MeSH allowed-qualifier metadata), XR, RU, DEL
               no-hier     no-struct minus PAR/CHD/RB/RN (broader/narrower)
               ro          RO only (associative "other related")
  weighting  count       distinct linked anchors (what the tool does today)
             hub         count / log2(2 + deg c)     -- penalise hub candidates
             aa          sum over linked anchors of 1/log2(2 + deg a), / log2(2 + deg c)
  anchors    pred        every other mention's predicted top-1 (the tool today)
             trusted     only other mentions whose top-1 is unambiguous: a unique exact
                         KB-name match, or a dense margin over #2 of at least --trust-margin
             gold-prev   gold of the mentions EARLIER in the document -- what an annotator
                         working top-down has already accepted (realistic in the tool)
             gold        gold of every other mention (ceiling)
Mentions sharing text never anchor each other (run_hint_analysis.independent_others).

Commands:
  score     the grid above + isolated-pick analysis on one dataset/split/index
  cross     BC5CDR: link chemical and disease mentions of the same document
            (reads MRREL once for the union of both tracks' candidates; cached)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.run_hint_analysis import (BC5CDR_PUBTATOR, choose, hit, independent_others,  # noqa: E402
                                    mcnemar, norm, prepare)
from eval.run_type_scoped_baseline import OUT, id_parts  # noqa: E402

STRUCT = {"AQ", "QB", "XR", "RU", "DEL"}
HIER = {"PAR", "CHD", "RB", "RN"}
REL_SETS = {
    "all": lambda rs: bool(rs),
    "no-struct": lambda rs: bool(rs - STRUCT),
    "no-hier": lambda rs: bool(rs - STRUCT - HIER),
    "ro": lambda rs: "RO" in rs,
}
WEIGHTS = ("count", "hub", "aa")
ANCHORS = ("pred", "trusted", "gold-prev", "gold")
DELTAS = (0.0, 0.01, 0.02, 0.05, 0.10)
POOL = "abbrev"


# ── pure logic ────────────────────────────────────────────────────────────────

def lg(d: float) -> float:
    return math.log2(2 + d)


def graph_score(links: list[tuple[set, float]], deg_c: float, relset: str, weight: str) -> float:
    """links = [(REL codes between c and one distinct anchor, anchor degree)]."""
    keep = REL_SETS[relset]
    linked = [da for rs, da in links if keep(rs)]
    if not linked:
        return 0.0
    if weight == "count":
        return float(len(linked))
    if weight == "hub":
        return len(linked) / lg(deg_c)
    return sum(1.0 / lg(da) for da in linked) / lg(deg_c)


def gated_pick(cands: list[tuple[str, float]], scores: list[float], delta: float) -> str | None:
    """Leader unless a candidate within delta of its dense score has strictly more graph support."""
    return choose(cands, graph=scores, delta=delta)


LAMBDAS = (0.0, 0.01, 0.02, 0.05, 0.1)


def err_auroc(score: list[float], ok: list[bool]) -> float:
    """How well a confidence score ranks wrong picks below right ones (1.0 = perfectly)."""
    return 1 - auroc([s for s, o in zip(score, ok) if not o], [s for s, o in zip(score, ok) if o])


def combo_pick(cands, graph: list[float], names: list[int], delta: float) -> str | None:
    """Graph support decides among near-ties; exact dense ties with no graph difference fall
    back to the "more KB names" tie-break (run_hint_analysis 'abbrev + ties: more KB names')."""
    if not cands:
        return None
    lead = cands[0][1]
    near = [i for i in range(len(cands)) if lead - cands[i][1] <= delta]
    best = max(near, key=lambda i: (graph[i], names[i] if cands[i][1] == lead else -1, -i))
    return cands[best][0]


def auroc(pos_scores: list[float], neg_scores: list[float]) -> float:
    """P(score of a random positive > random negative), ties count half."""
    if not pos_scores or not neg_scores:
        return float("nan")
    allv = sorted([(s, 1) for s in pos_scores] + [(s, 0) for s in neg_scores])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (i + j + 1) / 2                     # 1-based average rank of the tie block
        rank_sum += avg * sum(1 for _, y in allv[i:j] if y)
        i = j
    n1, n0 = len(pos_scores), len(neg_scores)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


# ── relation access ───────────────────────────────────────────────────────────

class Rel:
    """REL codes between two dictionary ids (composite ids = union of their CUIs)."""

    def __init__(self, kb: dict):
        self.rel, self.cuis, self.deg = kb["rel"], kb["cuis"], kb["deg"]
        self.memo: dict[tuple, frozenset] = {}

    def codes(self, a: str, b: str) -> frozenset:
        if a == b:
            return frozenset()
        key = (a, b) if a < b else (b, a)
        got = self.memo.get(key)
        if got is None:
            out: set = set()
            for x in self.cuis.get(a, ()):
                for y in self.cuis.get(b, ()):
                    out |= self.rel.get(x, {}).get(y, set()) | self.rel.get(y, {}).get(x, set())
            got = self.memo[key] = frozenset(out)
        return got

    def degree(self, a: str) -> float:
        return float(sum(self.deg.get(x, 0) for x in self.cuis.get(a, ())))


def span_start(row: dict) -> int:
    try:
        return int(row["entity_span"].split("-")[0])
    except ValueError:
        return 0


def anchor_sets(ms, by_doc, surfaces, kb, trust_margin: float) -> tuple[list[dict], dict]:
    """Per mention: anchor kind -> list of distinct anchor ids. Also trust stats."""
    names = kb["names"]
    trusted = []
    for m in ms:
        c = m[POOL]
        if not c:
            trusted.append(False)
            continue
        text = norm(m["expanded"] or m["row"]["entity_text"])
        lex = [text in names.get(cid, ()) for cid, _ in c]
        margin = c[0][1] - c[1][1] if len(c) > 1 else 1.0
        trusted.append((lex[0] and sum(lex) == 1) or margin >= trust_margin)
    t_idx = [i for i, t in enumerate(trusted) if t]
    stats = {"trusted_share": round(100 * len(t_idx) / max(1, len(ms)), 1),
             "trusted_acc1": round(100 * sum(hit(ms[i][f"top_{POOL}"], ms[i]["gold"]) for i in t_idx)
                                   / max(1, len(t_idx)), 1)}
    out: list[dict] = [{} for _ in ms]
    for idxs in by_doc.values():
        for i in idxs:
            others = independent_others(i, idxs, surfaces)
            s_i = span_start(ms[i]["row"])
            pred = {ms[j][f"top_{POOL}"] for j in others if ms[j][f"top_{POOL}"]}
            out[i] = {
                "pred": pred,
                "trusted": {ms[j][f"top_{POOL}"] for j in others if trusted[j] and ms[j][f"top_{POOL}"]},
                "gold-prev": {g for j in others if span_start(ms[j]["row"]) < s_i for g in ms[j]["gold"]},
                "gold": {g for j in others for g in ms[j]["gold"]},
            }
    return out, stats


def candidate_links(cands, anchors: set, rel: Rel) -> list[list[tuple[set, float]]]:
    """Per candidate, [(REL codes, anchor degree)] for every anchor it shares a relation with.

    Anchors that are parts of the same composite id count once (they are one concept).
    """
    out = []
    for cid, _ in cands:
        mine = id_parts(cid) | {cid}
        links = []
        for a in anchors:
            if a in mine or id_parts(a) & mine:
                continue
            rs = rel.codes(cid, a)
            if rs:
                links.append((set(rs), rel.degree(a)))
        out.append(links)
    return out


# ── score command ─────────────────────────────────────────────────────────────

def evaluate(ms, anchors, rel: Rel, label: str) -> dict:
    """Grid of rules against the abbrev baseline, plus the separation ceiling and isolation."""
    n = len(ms)
    base_ok = [hit(choose(m[POOL]), m["gold"]) for m in ms]
    pos = [next((j for j, (c, _) in enumerate(m[POOL]) if hit(c, m["gold"])), None) for m in ms]
    fixable = [i for i in range(n) if pos[i]]
    print(f"\n=== {label}: {n} mentions, baseline (abbrev pool) Acc@1 "
          f"{100 * sum(base_ok) / n:.2f}, fixable (gold at rank 2-k) {len(fixable)} ===", flush=True)

    links = {kind: [candidate_links(m[POOL], anchors[i][kind], rel) for i, m in enumerate(ms)]
             for kind in ANCHORS}
    degs = [[rel.degree(c) for c, _ in m[POOL]] for m in ms]

    rules, ceiling = {}, {}
    for kind in ANCHORS:
        for relset in REL_SETS:
            for weight in WEIGHTS:
                sc = [[graph_score(l, d, relset, weight) for l, d in zip(links[kind][i], degs[i])]
                      for i in range(n)]
                sep = sum(1 for i in fixable if sc[i][pos[i]] > sc[i][0])
                ceiling[f"{kind}/{relset}/{weight}"] = sep
                for delta in DELTAS:
                    ok = [hit(gated_pick(ms[i][POOL], sc[i], delta), ms[i]["gold"]) if ms[i][POOL] else False
                          for i in range(n)]
                    mc = mcnemar(base_ok, ok)
                    rules[f"{kind}/{relset}/{weight}/d{delta}"] = {
                        "acc1": round(100 * sum(ok) / n, 2), **mc}

    print(f"{'anchors/relations/weight':30} | sep. | " + " | ".join(f"d={d:<4} net" for d in DELTAS))
    for key, sep in ceiling.items():
        cells = []
        for d in DELTAS:
            r = rules[f"{key}/d{d}"]
            cells.append(f"{r['acc1']:6.2f} {r['net']:+5d}")
        print(f"{key:30} | {sep:4d} | " + " | ".join(cells))
    names_ok = [hit(combo_pick(m[POOL], [0.0] * len(m[POOL]), m["names_n"], 0.0), m["gold"]) for m in ms]
    print(f"\n--- on top of the 'abbrev + ties: more KB names' rule (Acc@1 {100 * sum(names_ok) / n:.2f}) ---")
    combos = {}
    for key in ("pred/all/count", "pred/all/aa", "trusted/all/aa", "gold-prev/all/aa", "gold-prev/all/count"):
        kind, relset, weight = key.split("/")
        sc = [[graph_score(l, d, relset, weight) for l, d in zip(links[kind][i], degs[i])] for i in range(n)]
        cells = []
        for delta in DELTAS:
            ok = [hit(combo_pick(ms[i][POOL], sc[i], ms[i]["names_n"], delta), ms[i]["gold"]) for i in range(n)]
            mc = mcnemar(names_ok, ok)
            combos[f"{key}/d{delta}"] = {"acc1": round(100 * sum(ok) / n, 2), **mc}
            cells.append(f"{100 * sum(ok) / n:6.2f} {mc['net']:+5d}")
        print(f"{key + ' + names':30} |      | " + " | ".join(cells))
    best = max(rules.items(), key=lambda kv: kv[1]["net"])
    best_real = max(((k, v) for k, v in rules.items() if not k.startswith("gold/")), key=lambda kv: kv[1]["net"])
    print(f"best rule overall: {best[0]} -> {best[1]}")
    print(f"best rule without the full-gold ceiling: {best_real[0]} -> {best_real[1]}")

    iso = isolation(ms, links, degs, base_ok)
    return {"n": n, "baseline_acc1": round(100 * sum(base_ok) / n, 2), "fixable": len(fixable),
            "separation": ceiling, "rules": rules, "names_baseline_acc1": round(100 * sum(names_ok) / n, 2),
            "on_top_of_names": combos, "best": best, "best_without_gold": best_real,
            "isolation": iso}


def isolation(ms, links, degs, base_ok) -> dict:
    """Is a top-1 with no graph link to the document more often wrong? (review-queue signal)"""
    out = {}
    top_score = [m[POOL][0][1] if m[POOL] else 0.0 for m in ms]
    # dense-score quartiles, to check the flag adds information beyond the score itself
    # equal-count quartiles by rank (dense scores tie a lot, so value cut-points leave empty bins)
    order = sorted(range(len(ms)), key=lambda i: top_score[i])
    quart = [0] * len(ms)
    for r, i in enumerate(order):
        quart[i] = min(3, 4 * r // max(1, len(ms)))
    dense_auc = auroc([s for s, ok in zip(top_score, base_ok) if not ok],
                      [s for s, ok in zip(top_score, base_ok) if ok])
    # note: for the dense score, LOW score flags errors, so report 1 - AUROC of errors-high
    out["dense_score_auroc_error"] = round(1 - dense_auc, 3)        # == err_auroc(top_score, base_ok)
    print("\n--- isolated top-1 picks as an error flag (abbrev pool) ---")
    print(f"dense top-1 score as an error detector: AUROC {out['dense_score_auroc_error']}")
    for kind in ("pred", "trusted", "gold-prev", "gold"):
        for relset in ("all", "no-struct", "no-hier"):
            sup = [graph_score(links[kind][i][0], degs[i][0], relset, "count") if ms[i][POOL] else 0.0
                   for i in range(len(ms))]
            isolated = [s == 0 for s in sup]
            n_iso = sum(isolated)
            acc_iso = sum(ok for ok, f in zip(base_ok, isolated) if f) / max(1, n_iso)
            acc_con = sum(ok for ok, f in zip(base_ok, isolated) if not f) / max(1, len(ms) - n_iso)
            per_q = []
            for q in range(4):
                idx = [i for i in range(len(ms)) if quart[i] == q]
                ii = [i for i in idx if isolated[i]]
                cc = [i for i in idx if not isolated[i]]
                per_q.append((round(100 * sum(base_ok[i] for i in ii) / max(1, len(ii)), 1),
                              round(100 * sum(base_ok[i] for i in cc) / max(1, len(cc)), 1)))
            errs = [i for i, ok in enumerate(base_ok) if not ok]
            recall = sum(isolated[i] for i in errs) / max(1, len(errs))
            # review-queue score: dense score minus a small penalty for an isolated pick
            # (training-free; lambda is the one knob, picked on dev and checked on test)
            combos = {}
            for lam in LAMBDAS:
                cs = [top_score[i] - lam * isolated[i] for i in range(len(ms))]
                combos[lam] = round(err_auroc(cs, base_ok), 3)
            within = [(round(err_auroc([top_score[i] for i in range(len(ms)) if quart[i] == q],
                                       [base_ok[i] for i in range(len(ms)) if quart[i] == q]), 3),
                       round(err_auroc([top_score[i] - 0.05 * isolated[i] for i in range(len(ms)) if quart[i] == q],
                                       [base_ok[i] for i in range(len(ms)) if quart[i] == q]), 3))
                      for q in range(4)]
            key = f"{kind}/{relset}"
            out[key] = {"isolated_share": round(100 * n_iso / len(ms), 1),
                        "auroc_dense_minus_lambda_isolated": combos,
                        "auroc_within_quartile_dense_vs_penalised": within,
                        "acc_isolated": round(100 * acc_iso, 1), "acc_connected": round(100 * acc_con, 1),
                        "error_recall": round(100 * recall, 1),
                        "error_precision": round(100 * (1 - acc_iso), 1),
                        "by_score_quartile_iso_vs_conn": per_q}
            print(f"  {key:22} isolated {out[key]['isolated_share']:5.1f}% | Acc@1 isolated "
                  f"{out[key]['acc_isolated']:5.1f} vs connected {out[key]['acc_connected']:5.1f} | "
                  f"catches {out[key]['error_recall']:5.1f}% of errors | quartiles (iso,conn) {per_q} | "
                  f"AUROC dense-lambda*isolated {combos}, per quartile (dense, dense-0.05*iso) {within}")
    return out


def score(args) -> None:
    prep = prepare(args)
    ms, kb = prep["ms"], prep["kb"]
    rel = Rel(kb)
    for m in ms:
        m["names_n"] = [len(kb["names"].get(c, ())) for c, _ in m[POOL]]
    anchors, tstats = anchor_sets(ms, prep["by_doc"], prep["surfaces"], kb, args.trust_margin)
    print(f"[{args.dataset} {args.split} {prep['tag']}] trusted anchors: {tstats['trusted_share']}% of "
          f"mentions, their top-1 Acc@1 {tstats['trusted_acc1']}", flush=True)
    res = evaluate(ms, anchors, rel, f"{args.dataset} {args.split} {prep['tag']}")
    res.update({"dataset": args.dataset, "split": args.split, "index": prep["tag"], "k": args.k,
                "trust_margin": args.trust_margin, "trust": tstats})
    path = OUT / f"{prep['stem']}.graphscore.json"
    json.dump(res, open(path, "w"), indent=1)
    print(f"\nwrote {path}")


# ── cross command (BC5CDR chemical <-> disease) ──────────────────────────────

def load_cross_rel(ids_cuis: set[str], meta: Path, stem: str) -> dict:
    """MRREL among the union of both tracks' CUIs, with REL and RELA (cached)."""
    path = OUT / f"{stem}.cross.rel.pkl"
    if path.exists():
        got = pickle.load(open(path, "rb"))
        if ids_cuis <= got["cuis"]:
            return got
    rel: dict[str, dict] = defaultdict(dict)
    rela: Counter = Counter()
    print(f"  reading MRREL among {len(ids_cuis)} CUIs of both tracks (a few minutes)...", flush=True)
    with open(meta / "MRREL.RRF", encoding="utf-8") as f:
        for line in f:
            cols = line.split("|", 11)
            c1, r, c2 = cols[0], cols[3], cols[4]
            if r == "SIB" or c1 == c2 or c1 not in ids_cuis or c2 not in ids_cuis:
                continue
            rel[c1].setdefault(c2, set()).add(r)
            rel[c1].setdefault(("rela", c2), set()).add(cols[7] or r)
    got = {"cuis": set(ids_cuis), "rel": dict(rel)}
    pickle.dump(got, open(path, "wb"))
    return got


def cross(args) -> None:
    preps = {}
    for ds in ("bc5cdr-chem", "bc5cdr-dis"):
        a = SimpleNamespace(**{**vars(args), "dataset": ds})
        if ds == "bc5cdr-chem" and args.index != "full":
            a.index = "dataset-{split}"
        preps[ds] = prepare(a)
    cuis = {}
    for p in preps.values():
        cuis.update(p["kb"]["cuis"])
    all_cuis = set().union(*cuis.values())
    xr = load_cross_rel(all_cuis, Path(args.umls_meta), f"bc5cdr-cross.{args.index.split('-')[0]}.{args.split}")
    deg = {}
    for p in preps.values():
        deg.update(p["kb"]["deg"])
    rel = Rel({"rel": {c: {k: v for k, v in d.items() if not isinstance(k, tuple)} for c, d in xr["rel"].items()},
               "cuis": cuis, "deg": deg})

    def rela_between(a: str, b: str) -> set:
        out = set()
        for x in cuis.get(a, ()):
            for y in cuis.get(b, ()):
                out |= xr["rel"].get(x, {}).get(("rela", y), set()) | xr["rel"].get(y, {}).get(("rela", x), set())
        return out

    # gold chemical-disease links per document: does the graph even connect them?
    chem, dis = preps["bc5cdr-chem"], preps["bc5cdr-dis"]
    docs = set(chem["by_doc"]) & set(dis["by_doc"])
    linked_docs, pair_n, pair_linked = 0, 0, 0
    relas: Counter = Counter()
    for d in docs:
        cg = {g for i in chem["by_doc"][d] for g in chem["ms"][i]["gold"]}
        dg = {g for i in dis["by_doc"][d] for g in dis["ms"][i]["gold"]}
        any_link = False
        for a in cg:
            for b in dg:
                pair_n += 1
                rs = rel.codes(a, b)
                if rs:
                    pair_linked += 1
                    any_link = True
                    relas.update(rela_between(a, b))
        linked_docs += any_link
    print(f"\n=== BC5CDR {args.split} {args.index}: chemical <-> disease links in MRREL ===")
    print(f"documents with both tracks: {len(docs)}; with >=1 linked gold chem-dis pair: {linked_docs} "
          f"({100 * linked_docs / max(1, len(docs)):.1f}%)")
    print(f"gold chem-dis concept pairs: {pair_n}; linked: {pair_linked} ({100 * pair_linked / max(1, pair_n):.1f}%)")
    print(f"most common RELA on those links: {relas.most_common(12)}")

    results = {"docs": len(docs), "linked_docs": linked_docs, "pairs": pair_n, "pairs_linked": pair_linked,
               "rela": relas.most_common(30), "tracks": {}}
    # re-rank each track with anchors from the OTHER track (and both)
    for me, other in (("bc5cdr-chem", "bc5cdr-dis"), ("bc5cdr-dis", "bc5cdr-chem")):
        P, O = preps[me], preps[other]
        for m in P["ms"]:
            m["names_n"] = [len(P["kb"]["names"].get(c, ())) for c, _ in m[POOL]]
        own, _ = anchor_sets(P["ms"], P["by_doc"], P["surfaces"], P["kb"], args.trust_margin)
        anchors = []
        for i, m in enumerate(P["ms"]):
            d = m["row"]["document_id"]
            s_i = span_start(m["row"])
            oth = O["by_doc"].get(d, [])
            x_pred = {O["ms"][j][f"top_{POOL}"] for j in oth if O["ms"][j][f"top_{POOL}"]}
            x_gold = {g for j in oth for g in O["ms"][j]["gold"]}
            x_gprev = {g for j in oth if span_start(O["ms"][j]["row"]) < s_i for g in O["ms"][j]["gold"]}
            anchors.append({"pred": x_pred, "trusted": own[i]["trusted"] | x_pred,
                            "gold-prev": x_gprev, "gold": x_gold})
        print(f"\n[{me}] anchors from the other track (row 'trusted' = own trusted + other's predictions)")
        results["tracks"][me] = evaluate(P["ms"], anchors, rel, f"{me} {args.split}: cross-track anchors")
        for v in results["tracks"][me]["rules"].values():
            v.pop("p", None)
    path = OUT / f"bc5cdr-cross.{args.index.split('-')[0]}.{args.split}.graphscore.json"
    json.dump(results, open(path, "w"), indent=1, default=list)
    print(f"\nwrote {path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("score", "cross"):
        s = sub.add_parser(name)
        if name == "score":
            s.add_argument("--dataset", choices=("medmentions", "bc5cdr-chem", "bc5cdr-dis"), required=True)
        s.add_argument("--split", default="dev", choices=("train", "dev", "test"))
        s.add_argument("--index", default="full")
        s.add_argument("--k", type=int, default=10)
        s.add_argument("--trust-margin", type=float, default=0.05)
        s.add_argument("--bc5cdr-pubtator", default=os.environ.get("BC5CDR_PUBTATOR", str(BC5CDR_PUBTATOR)))
        s.add_argument("--umls-meta", default=os.environ.get("UMLS_META"))
        s.add_argument("--semgroups", default=os.environ.get("SEMGROUPS"))
    args = p.parse_args()
    if not args.umls_meta:
        p.error("--umls-meta (or env UMLS_META) is required")
    score(args) if args.cmd == "score" else cross(args)


if __name__ == "__main__":
    main()
