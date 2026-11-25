"""Inter-annotator agreement and the final-value rule for reviewed exports.

Pure functions over human decisions only; no database or web imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass
class FinalRow:
    """Final value per KB column for one row, and whether it needs review."""

    values: dict[str, str]
    needs_review: bool
    reason: str


def _cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa; None when fewer than 2 items or expected agreement is 1."""
    n = len(a)
    if n < 2:
        return None
    p_o = sum(x == y for x, y in zip(a, b)) / n
    p_e = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    if p_e == 1:
        return None
    return (p_o - p_e) / (1 - p_e)


def pairwise_agreement(values: dict[int, dict[int, str]]) -> list[dict]:
    """Agreement for every user pair (a < b) over the rows both have decided.

    ``values[user_id][row_idx]`` is one KB's value (human decisions only).
    ``percent`` is exact-match agreement in 0-100 (None when no shared rows);
    ``kappa`` is Cohen's kappa (None when n < 2 or expected agreement is 1).
    """
    out: list[dict] = []
    for a, b in combinations(sorted(values), 2):
        shared = sorted(values[a].keys() & values[b].keys())
        va = [values[a][r] for r in shared]
        vb = [values[b][r] for r in shared]
        n = len(shared)
        percent = 100.0 * sum(x == y for x, y in zip(va, vb)) / n if n else None
        out.append({"a": a, "b": b, "n": n, "percent": percent,
                    "kappa": _cohen_kappa(va, vb)})
    return out


def final_rows(
    human: dict[int, dict[int, dict[str, str]]],
    adjudications: dict[int, dict[str, str]],
    row_count: int,
    kb_names: list[str],
    script_only_rows: frozenset[int] = frozenset(),
) -> dict[int, FinalRow]:
    """Final values for rows 0..row_count-1, applying in order:

    1. an adjudication wins (reason "");
    2. two or more humans matching in every KB column give that value (reason "");
    3. two or more humans that differ: blank, needs review, "disagree";
    4. exactly one human: blank, needs review, "single_annotator";
    5. no humans and the row is in ``script_only_rows``: blank, "script_only";
    6. otherwise: blank, "no_decisions".

    A decision missing a KB counts as "" for that KB.
    """
    out: dict[int, FinalRow] = {}
    for row in range(row_count):
        if row in adjudications:
            adj = adjudications[row]
            out[row] = FinalRow({kb: adj.get(kb, "") for kb in kb_names}, False, "")
            continue
        decisions = [tuple(d[row].get(kb, "") for kb in kb_names)
                     for d in human.values() if row in d]
        blank = {kb: "" for kb in kb_names}
        if len(decisions) >= 2 and len(set(decisions)) == 1:
            out[row] = FinalRow(dict(zip(kb_names, decisions[0])), False, "")
        elif len(decisions) >= 2:
            out[row] = FinalRow(blank, True, "disagree")
        elif len(decisions) == 1:
            out[row] = FinalRow(blank, True, "single_annotator")
        elif row in script_only_rows:
            out[row] = FinalRow(blank, True, "script_only")
        else:
            out[row] = FinalRow(blank, True, "no_decisions")
    return out
