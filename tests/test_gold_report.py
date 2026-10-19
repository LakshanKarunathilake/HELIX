"""What the debug card says about the gold answer.

The point is to separate the two failure modes at a glance:
  * gold is NOT in the index  -> a coverage failure; no reranking can fix it
  * gold IS in the index      -> a retrieval/ranking failure, which is fixable
Gold may list several equivalent CUIs, so each component is reported.
"""

from __future__ import annotations

from api.main import build_gold_report

INDEX = {
    "C0032461": {
        "primary_alias": "polycythemia",
        "all_aliases": ["polycythemia", "erythrocythemia"],
        "semantic_groups": "DISO",
        "semantic_types": "T047:Disease or Syndrome",
        "definition": "An increase in the total red cell mass.",
    }
}


def fake_lookup(entity_id: str):
    return INDEX.get(entity_id)


def test_reports_an_indexed_gold_with_its_concept_details():
    [r] = build_gold_report("C0032461", fake_lookup)

    assert r["found"] is True
    assert r["id"] == "C0032461"
    assert r["label"] == "polycythemia"
    assert r["semantic_groups"] == "DISO"
    assert r["n_aliases"] == 2


def test_flags_a_gold_that_is_absent_from_the_index():
    [r] = build_gold_report("C9999999", fake_lookup)

    assert r["found"] is False
    assert r["id"] == "C9999999"


def test_every_component_of_a_multi_valued_gold_is_reported():
    rs = build_gold_report("C0032461|C9999999", fake_lookup)

    assert [r["id"] for r in rs] == ["C0032461", "C9999999"]
    assert [r["found"] for r in rs] == [True, False]


def test_blank_gold_reports_nothing():
    assert build_gold_report("", fake_lookup) == []
    assert build_gold_report(None, fake_lookup) == []


def test_a_lookup_failure_is_not_mistaken_for_a_missing_concept():
    """A KB that errors must not be reported as 'not in the index'."""
    def boom(_):
        raise RuntimeError("KB down")

    [r] = build_gold_report("C1", boom)

    assert r["found"] is None      # unknown, not False
    assert "error" in r
