#!/usr/bin/env python3
"""Score the pilot study (paper Sec. 4.5).

Joins each study session's annotations to the gold file that was deliberately
kept out of the sessions, and reports per condition:

  * the retrieval baseline Acc@1 and the human's final Acc@1 over the SAME
    mentions, and the paired **delta** between them. Both terms come from the
    identical mentions, so document difficulty cancels — necessary because
    per-document Acc@1 varies by ~18 points and each condition has only two
    documents, which would swamp a raw between-condition comparison.
  * time per mention, the accept/confirm/override mix, and revisit counts.
  * for sessions that collected per-decision feedback (Annotation Guide
    Sec. 8): which hint decided each answer, the human's Acc@1 per hint and per
    confidence level, and how often each UI panel was opened.

Usage:
    python eval/analyze_study.py                     # all study2-* sessions
    python eval/analyze_study.py --prefix study2 --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GOLD_FILE = ROOT / "eval" / "out" / "study_gold.csv"
UID_COL = "mention_uid"


def _idset(v) -> set[str]:
    """Any-match id set. A gold cell may list several equivalent ids, and a KB
    id may itself be composite (e.g. '601367|D002544'), so both sides split on
    '|' and match on any shared component — the convention eval/score.py uses.
    """
    return {p.strip() for p in str(v or "").split("|") if p.strip()}


def _hit(pred, gold) -> bool:
    return bool(_idset(pred) & _idset(gold))


def summarise(df: pd.DataFrame, kb_col: str) -> dict[str, dict]:
    """Per-condition metrics. Rows with no annotation are ignored."""
    out: dict[str, dict] = {}
    done = df[df[kb_col].astype(str).str.strip() != ""].copy()

    # Rows annotated while the debug gold display was on cannot count toward
    # accuracy — the annotator was shown the answer. Sessions predating the
    # flag have no such column and are treated as clean.
    if "_debug_gold_shown" not in done.columns:
        done["_debug_gold_shown"] = "0"
    dirty = done["_debug_gold_shown"].astype(str).str.strip() == "1"
    excluded = done[dirty].groupby("_condition").size().to_dict()
    done = done[~dirty]

    for cond, g in done.groupby("_condition", sort=True):
        secs = pd.to_numeric(g["_elapsed_ms"], errors="coerce").dropna() / 1000.0
        ranks = pd.to_numeric(g["_selected_rank"], errors="coerce").dropna()
        revs = pd.to_numeric(g.get("_revisions"), errors="coerce").fillna(0)

        base = [_hit(r["_top1_id"], r["gold_cui"]) for _, r in g.iterrows()]
        human = [_hit(r[kb_col], r["gold_cui"]) for _, r in g.iterrows()]
        b = 100.0 * sum(base) / len(g)
        h = 100.0 * sum(human) / len(g)

        out[str(cond)] = {
            "n": len(g),
            "baseline_acc1": b,
            "human_acc1": h,
            "delta": h - b,
            "rescued": sum(1 for x, y in zip(base, human) if not x and y),
            "broken": sum(1 for x, y in zip(base, human) if x and not y),
            "median_secs": float(secs.median()) if len(secs) else None,
            "mean_secs": float(secs.mean()) if len(secs) else None,
            "total_mins": float(secs.sum() / 60) if len(secs) else None,
            "mean_rank": float(ranks.mean()) if len(ranks) else None,
            "revisit_rate": float((revs > 0).mean() * 100) if len(revs) else 0.0,
            "methods": dict(Counter(g["_method"].fillna(""))),
            "excluded_debug": int(excluded.get(cond, 0)),
            **_feedback_summary(g, human),
        }
    for cond, n in excluded.items():                 # conditions with ONLY dirty rows
        out.setdefault(str(cond), {"n": 0, "excluded_debug": int(n)})
    return out


def _feedback_summary(g: pd.DataFrame, human: list[bool]) -> dict:
    """Hint / confidence / panel breakdowns; empty for sessions without feedback."""
    if "_hint" not in g.columns:
        return {}
    hit = pd.Series(human, index=g.index)

    def acc_by(col: str) -> dict[str, dict]:
        out = {}
        for key, idx in g.groupby(g[col].astype(str).str.strip()).groups.items():
            if key:
                out[key] = {"n": len(idx), "human_acc1": 100.0 * hit[idx].mean()}
        return out

    panels = Counter(
        p for cell in g["_panels"].astype(str) for p in cell.split(";")
        if p and p != "none"
    ) if "_panels" in g.columns else Counter()
    return {
        "feedback_missing": int((g["_hint"].astype(str).str.strip() == "").sum()),
        "by_hint": acc_by("_hint"),
        "by_confidence": acc_by("_confidence"),
        "panels_opened": dict(panels),
    }


def load(prefix: str) -> tuple[pd.DataFrame, str]:
    gold = pd.read_csv(GOLD_FILE, dtype=str, keep_default_na=False)
    frames, kb_col = [], None
    for sdir in sorted((ROOT / "sessions").glob(f"{prefix}-*")):
        out = sdir / "annotated.csv"
        if not out.exists():
            continue
        cfg = json.loads((sdir / "session.json").read_text())
        kb_col = cfg["kbs"][0]["name"]
        frames.append(pd.read_csv(out, dtype=str, keep_default_na=False))
    if not frames:
        raise SystemExit(f"no annotated sessions matching '{prefix}-*' yet")
    df = pd.concat(frames, ignore_index=True)
    missing = set(df[UID_COL]) - set(gold[UID_COL])
    if missing:
        raise SystemExit(f"{len(missing)} mentions have no gold entry — stale gold file?")
    return df.merge(gold, on=UID_COL, how="left"), kb_col


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="study2")
    ap.add_argument("--json", help="also write the summary here")
    args = ap.parse_args()

    df, kb_col = load(args.prefix)
    res = summarise(df, kb_col)

    hdr = f"{'cond':6}{'n':>5}{'base':>8}{'human':>8}{'delta':>8}{'resc':>6}{'brok':>6}{'med s':>8}{'mins':>7}{'revis':>7}"
    print(hdr)
    print("-" * len(hdr))
    for c, s in res.items():
        if not s["n"]:
            print(f"{c:6}{0:>5}   (all rows excluded — annotated with gold visible)")
            continue
        print(f"{c:6}{s['n']:>5}{s['baseline_acc1']:>7.1f}%{s['human_acc1']:>7.1f}%"
              f"{s['delta']:>+7.1f}{s['rescued']:>6}{s['broken']:>6}"
              f"{(s['median_secs'] or 0):>8.1f}{(s['total_mins'] or 0):>7.1f}"
              f"{s['revisit_rate']:>6.0f}%")
    print("\nbase/human = Acc@1 of retrieval top-1 vs the annotator's final answer,")
    print("over the same mentions. resc = baseline wrong -> human right; brok = the reverse.\n")
    for c, s in res.items():
        if s.get("methods"):
            print(f"{c}: {s['methods']}")

    for c, s in res.items():
        if "by_hint" not in s:
            continue
        print(f"\n{c} feedback ({s['feedback_missing']} decision(s) without feedback)")
        for k, v in sorted(s["by_hint"].items(), key=lambda kv: -kv[1]["n"]):
            print(f"  hint {k:24}{v['n']:>4}  human Acc@1 {v['human_acc1']:5.1f}%")
        for k, v in s["by_confidence"].items():
            print(f"  conf {k:24}{v['n']:>4}  human Acc@1 {v['human_acc1']:5.1f}%")
        if s["panels_opened"]:
            print(f"  panels opened: {s['panels_opened']}")

    contaminated = sum(s.get("excluded_debug", 0) for s in res.values())
    if contaminated:
        print("\n" + "!" * 72)
        print(f"!! {contaminated} row(s) EXCLUDED: annotated while debug_show_gold was on.")
        print("!! Those rows saw the answer. Turning the flag off does not clean them —")
        print("!! clear the affected sessions and re-annotate before reporting numbers.")
        for c, s in res.items():
            if s.get("excluded_debug"):
                print(f"!!   {c}: {s['excluded_debug']} excluded, {s['n']} usable")
        print("!" * 72)

    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
