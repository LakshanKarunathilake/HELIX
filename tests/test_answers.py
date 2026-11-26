"""Comparing answers as concept sets (pure functions)."""
from eltool.answers import (
    basis_for,
    is_blank,
    is_complete,
    mixes_nil,
    normalise,
    parse_ids,
)


def test_parse_ids_trims_dedupes_sorts():
    assert parse_ids(" C2 |C1||C2 ") == ("C1", "C2")
    assert parse_ids("") == parse_ids(None) == parse_ids(" | ") == ()


def test_nil_is_single_valued():
    assert parse_ids("nil") == ("NIL",)
    assert parse_ids("NIL|C1") == ("C1",)
    assert mixes_nil("NIL|C1") and not mixes_nil("NIL") and not mixes_nil("C1|C2")


def test_minus_one_is_an_ordinary_id():
    assert parse_ids("-1") == ("-1",)


def test_order_does_not_matter():
    assert normalise({"U": "C1|C2"}, ["U"]) == normalise({"U": " C2 | C1 "}, ["U"])


def test_blank_and_complete():
    a = normalise({"U": "C1"}, ["U", "M"])
    assert not is_blank(a) and not is_complete(a)
    assert is_blank(normalise({}, ["U", "M"]))


def _a(u, m="D1"):
    return normalise({"U": u, "M": m}, ["U", "M"])


def test_basis_accepted_lists_every_matching_user():
    assert basis_for(_a("C1"), {2: _a("C1"), 1: _a("C1"), 3: _a("C2")}) == ("accepted", [1, 2])


def test_basis_combined_from_two_users():
    assert basis_for(_a("C1|C2"), {1: _a("C1"), 2: _a("C2")}) == ("combined", [1, 2])


def test_basis_combined_for_a_subset():
    assert basis_for(_a("C1"), {1: _a("C1|C2"), 2: _a("C3", "D2")}) == ("combined", [1])


def test_basis_override_keeps_partial_sources():
    assert basis_for(_a("C1|C9"), {1: _a("C1"), 2: _a("C2", "D2")}) == ("override", [1])


def test_basis_concept_proposed_for_another_kb_is_override():
    assert basis_for(_a("D1"), {1: _a("C1")})[0] == "override"


def test_basis_nil_nobody_chose_is_override():
    assert basis_for(_a("NIL"), {1: _a("C1")})[0] == "override"
