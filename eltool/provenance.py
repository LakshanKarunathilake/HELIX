"""Derive per-decision provenance from the raw signals the annotation UI reports.

Kept separate from the API layer so the study's method taxonomy is testable and
defined in exactly one place.
"""

from __future__ import annotations

NIL = "NIL"


def classify_method(
    selected: str,
    top1_id: str,
    user_interacted: bool,
    manual_entry: bool = False,
) -> str:
    """Return one of: nil, manual_entry, auto_accept, user_confirm, user_override.

    ``selected`` is the committed annotation (possibly pipe-joined multi-match);
    ``top1_id`` is the candidate retrieval ranked first.
    """
    if selected == NIL:
        return NIL.lower()
    if manual_entry:
        return "manual_entry"
    if not user_interacted:
        return "auto_accept"
    return "user_confirm" if selected == top1_id else "user_override"
