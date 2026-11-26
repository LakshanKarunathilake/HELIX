"""Inter-annotator agreement.

Pure functions over human decisions only; no database or web imports.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations

from eltool.answers import join_ids, parse_ids


def _cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa; None when fewer than 2 items or expected agreement is 1."""
    n = len(a)
    if n < 2:
        return None
    p_o = sum(x == y for x, y in zip(a, b)) / n
    count_a, count_b = Counter(a), Counter(b)
    p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in count_a.keys() | count_b.keys())
    if p_e == 1:
        return None
    return (p_o - p_e) / (1 - p_e)


def pairwise_agreement(values: dict[int, dict[int, str]]) -> list[dict]:
    """Agreement for every user pair (a < b) over the rows both have answered.

    ``values[user_id][row_idx]`` is one KB's value (human decisions only),
    compared as a set of concept ids; a row where either set is empty is not
    shared. ``percent`` is exact-set agreement and ``overlap`` the mean Jaccard
    index, both in 0-100 (None when no shared rows); ``kappa`` is Cohen's kappa
    over the sets (None when n < 2 or expected agreement is 1).
    """
    out: list[dict] = []
    for a, b in combinations(sorted(values), 2):
        shared = []
        for row in sorted(values[a].keys() & values[b].keys()):
            ids_a, ids_b = parse_ids(values[a][row]), parse_ids(values[b][row])
            if ids_a and ids_b:
                shared.append((set(ids_a), set(ids_b)))
        n = len(shared)
        percent = 100.0 * sum(x == y for x, y in shared) / n if n else None
        overlap = 100.0 * sum(len(x & y) / len(x | y) for x, y in shared) / n if n else None
        kappa = _cohen_kappa([join_ids(x) for x, _ in shared],
                             [join_ids(y) for _, y in shared])
        out.append({"a": a, "b": b, "n": n, "percent": percent,
                    "kappa": kappa, "overlap": overlap})
    return out
