"""Type mapping used to restrict candidates to the mention's given type."""
from __future__ import annotations

from eval.type_map import (TypeScope, load_cui_tuis, load_mesh_tuis, load_semgroups,
                           load_tui_tree, parse_tuis, subtree)

# A slice of the UMLS semantic network: T038 Biologic Function and some descendants,
# plus T103 Chemical with a descendant, and T017 Anatomical Structure.
TREE = {
    "T038": "B2.2.1", "T046": "B2.2.1.2", "T047": "B2.2.1.2.1", "T191": "B2.2.1.2.1.2",
    "T039": "B2.2.1.1", "T103": "A1.4.1", "T121": "A1.4.1.1.1", "T017": "A1.2.3.1",
    "T038X": "B2.2.10",  # same prefix text, different branch: must NOT count as a child
}


def test_parse_tuis_reads_server_format():
    assert parse_tuis("T116:Amino Acid, Peptide, or Protein||T121:Pharmacologic Substance") == {"T116", "T121"}


def test_parse_tuis_empty():
    assert parse_tuis("") == set()
    assert parse_tuis(None) == set()


def test_subtree_includes_self_and_descendants():
    assert subtree("T038", TREE) == {"T038", "T046", "T047", "T191", "T039"}


def test_subtree_respects_dot_boundaries():
    assert "T038X" not in subtree("T038", TREE)


def test_subtree_of_unknown_type_is_empty():
    assert subtree("T999", TREE) == set()


def test_medmentions_scope_accepts_descendant_types():
    ts = TypeScope.medmentions(TREE, ["T038", "T103"])
    assert ts.compatible("T038", {"T047"})
    assert ts.compatible("T103", {"T121", "T017"})  # any of the candidate's types may fit
    assert not ts.compatible("T038", {"T017"})


def test_unknown_mention_type_does_not_filter():
    ts = TypeScope.medmentions(TREE, ["T038"])
    assert ts.allowed("T200") is None
    assert ts.compatible("T200", {"T017"})


def test_candidate_without_types_is_incompatible_for_known_type():
    ts = TypeScope.medmentions(TREE, ["T038"])
    assert not ts.compatible("T038", set())


def test_bc5cdr_scope_uses_semantic_groups():
    groups = {"T121": "CHEM", "T103": "CHEM", "T047": "DISO", "T184": "DISO", "T017": "ANAT"}
    ts = TypeScope.bc5cdr(groups)
    assert ts.compatible("Chemical", {"T121"})
    assert ts.compatible("Disease", {"T184"})
    assert not ts.compatible("Disease", {"T121", "T017"})


def test_load_tui_tree_and_cui_tuis(tmp_path):
    p = tmp_path / "MRSTY.RRF"
    p.write_text("C1|T047|B2.2.1.2.1|Disease or Syndrome|AT1|256|\n"
                 "C1|T191|B2.2.1.2.1.2|Neoplastic Process|AT2|256|\n"
                 "C2|T047|B2.2.1.2.1|Disease or Syndrome|AT3|256|\n")
    assert load_tui_tree(p) == {"T047": "B2.2.1.2.1", "T191": "B2.2.1.2.1.2"}
    assert load_cui_tuis(p, {"C1"}) == {"C1": {"T047", "T191"}}


def test_load_semgroups(tmp_path):
    p = tmp_path / "SemGroups.txt"
    p.write_text("CHEM|Chemicals & Drugs|T121|Pharmacologic Substance\n"
                 "DISO|Disorders|T047|Disease or Syndrome\n")
    assert load_semgroups(p) == {"T121": "CHEM", "T047": "DISO"}


def test_load_mesh_tuis_joins_mesh_codes_to_types(tmp_path):
    conso = tmp_path / "MRCONSO.RRF"
    sty = tmp_path / "MRSTY.RRF"
    row = "{cui}|ENG|P|L1|PF|S1|Y|A1||{code}|{code}|{sab}|MH|{code}|name|0|N|256|\n"
    conso.write_text(row.format(cui="C1", code="D003920", sab="MSH")
                     + row.format(cui="C2", code="C051883", sab="MSH")
                     + row.format(cui="C3", code="X1", sab="SNOMEDCT_US"))
    sty.write_text("C1|T047|B2.2.1.2.1|Disease or Syndrome|AT1|256|\n"
                   "C2|T109|A1.4.1.2.1|Organic Chemical|AT2|256|\n"
                   "C3|T017|A1.2.3.1|Anatomical Structure|AT3|256|\n")
    assert load_mesh_tuis(conso, sty) == {"D003920": {"T047"}, "C051883": {"T109"}}
