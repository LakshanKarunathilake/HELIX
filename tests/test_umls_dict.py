"""Assembling a dictionary row for create_sapbert_index.py.

The builder writes CUI, SemanticGroups, SemanticTypes, Definition, Terms with
'||' separators. Aliases are deduplicated case-insensitively because the index
embeds one vector per alias — duplicates are pure cost with no recall gain.
"""

from __future__ import annotations

from eval.build_umls_full_dict import build_row, dedupe_aliases


def test_aliases_are_deduplicated_case_insensitively_keeping_first_form():
    assert dedupe_aliases(["Lung", "lung", "LUNG", "Pulmo"]) == ["Lung", "Pulmo"]


def test_blank_and_whitespace_aliases_are_dropped():
    assert dedupe_aliases(["  ", "", "lung", " lung "]) == ["lung"]


def test_row_joins_types_and_terms_with_the_double_pipe_separator():
    row = build_row(
        "C0024109",
        types=[("T024", "Tissue"), ("T017", "Anatomical Structure")],
        definition="",
        aliases=["lung", "pulmo"],
    )

    assert row["CUI"] == "C0024109"
    assert row["SemanticTypes"] == "T017:Anatomical Structure||T024:Tissue"
    assert row["Terms"] == "lung||pulmo"


def test_types_are_ordered_deterministically():
    a = build_row("C1", [("T103", "Chemical"), ("T017", "Anat")], "", ["x"])
    b = build_row("C1", [("T017", "Anat"), ("T103", "Chemical")], "", ["x"])

    assert a["SemanticTypes"] == b["SemanticTypes"]


def test_a_concept_with_no_aliases_yields_no_row():
    assert build_row("C1", [], "", []) is None
    assert build_row("C1", [], "", ["   "]) is None


def test_newlines_in_a_definition_are_flattened():
    """A raw newline would corrupt the CSV row."""
    row = build_row("C1", [], "line one\nline two", ["x"])

    assert "\n" not in row["Definition"]
    assert row["Definition"] == "line one line two"
