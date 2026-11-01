"""Render a candidate graph payload as plain-text evidence lines for the LLM.

Mirrors what the human sees in study condition C3: how candidate concepts
connect to each other and to their neighbours.
"""

from __future__ import annotations


def render_graph_evidence(
    graph: dict | None,
    candidate_ids: list[str],
    max_lines: int = 30,
) -> str:
    """Return newline-joined evidence lines, or "" when there is nothing to show.

    Line format: ``<src> (<name>) —[<rel>]— <tgt> (<name>)``.
    Candidate-to-candidate edges come first, then candidate-to-neighbour edges
    (candidate end always written first). Edges touching no candidate are
    dropped, duplicates (same unordered pair and rel) are removed, and the
    output is capped at ``max_lines``.
    """
    if not graph or graph.get("enabled", True) is False:
        return ""

    candidates = set(candidate_ids)
    names = {}
    for node in graph.get("nodes") or []:
        nid = node.get("id")
        if nid is None:
            continue
        names[nid] = node.get("label") or nid

    both: list[str] = []
    one: list[str] = []
    seen: set[tuple[frozenset, str]] = set()

    for edge in graph.get("edges") or []:
        src = edge.get("source")
        tgt = edge.get("target")
        rel = edge.get("rel") or "related"
        src_c = src in candidates
        tgt_c = tgt in candidates
        if not (src_c or tgt_c):
            continue

        key = (frozenset((src, tgt)), rel)
        if key in seen:
            continue
        seen.add(key)

        if src_c and tgt_c:
            both.append(_line(src, tgt, rel, names))
        elif tgt_c:
            # Put the candidate end first.
            one.append(_line(tgt, src, rel, names))
        else:
            one.append(_line(src, tgt, rel, names))

    return "\n".join((both + one)[:max_lines])


def _line(first: str, second: str, rel: str, names: dict) -> str:
    return f"{first} ({names.get(first, first)}) —[{rel}]— {second} ({names.get(second, second)})"
