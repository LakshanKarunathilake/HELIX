"""Per-decision feedback: which hint decided an annotation, and how sure the
annotator was (Annotation Guide, Sec. 8 "Feedback on each decision").

The option lists live here and are served to the UI, so the codes written to
the output CSV are defined in exactly one place. Codes are stable slugs; the
labels are what the annotator reads and may be reworded freely.
"""

from __future__ import annotations

# One main hint per decision, in the order the guide lists them. The UI binds
# keys 1-9 to this order.
HINTS: list[tuple[str, str]] = [
    ("name_match", "Name or synonym matched the term"),
    ("definition", "Definition"),
    ("entity_type", "Entity type"),
    ("list_position", "Position in the suggestion list"),
    ("graph_links", "Graph links to other terms in the abstract"),
    ("same_term_in_abstract", "How the same term was linked elsewhere in the abstract"),
    ("background_knowledge", "Own background knowledge"),
    ("external_lookup", "Looked it up outside the tool"),
    ("guess", "Guess"),
]

CONFIDENCE: list[tuple[str, str]] = [
    ("sure", "Sure"),
    ("fairly_sure", "Fairly sure"),
    ("unsure", "Unsure"),
]

# UI panels whose opening is logged passively in `_panels`.
PANELS = {"definition", "synonyms", "graph", "lookup", "document_review"}

HINT_CODES = {c for c, _ in HINTS}
CONFIDENCE_CODES = {c for c, _ in CONFIDENCE}


def options() -> dict:
    return {
        "hints": [{"code": c, "label": l} for c, l in HINTS],
        "confidence": [{"code": c, "label": l} for c, l in CONFIDENCE],
    }


def format_panels(panels: list[str] | None) -> str:
    """Encode the panels opened on a row. "none" (rather than blank) records
    that nothing was opened, so a blank cell always means "not yet saved"."""
    seen = sorted({p for p in (panels or []) if p in PANELS})
    return ";".join(seen) if seen else "none"


def validate_reflection_ranking(ranking: list[str]) -> None:
    if sorted(ranking) != sorted(HINT_CODES):
        raise ValueError("ranking must list every hint code exactly once")
