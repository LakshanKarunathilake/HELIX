"""optimize_selection: the measured tie-break rule (no Neo4j needed -- edges are stubbed)."""
from eltool.graph_client import GraphSchema, Neo4jGraphClient


def client(edges):
    # bypass __init__ (no neo4j.yaml / driver needed); only the edge query is used
    c = Neo4jGraphClient.__new__(Neo4jGraphClient)
    c.schema = GraphSchema()
    c._edges_among = lambda ids: [(a, b) for a, b in edges if a in ids and b in ids]
    return c


def test_exact_tie_is_broken_even_when_confident():
    # mention 0: "cold" ties between C_TEMP and C_DISEASE at score 1.0; mention 1 anchors on C_VIRUS
    gc = client([("C_DISEASE", "C_VIRUS")])
    res = gc.optimize_selection([(0, [("C_TEMP", 1.0), ("C_DISEASE", 1.0)]), (1, [("C_VIRUS", 0.99)])],
                                labels={0: "cold", 1: "rhinovirus"})
    assert res["selections"] == {"0": "C_DISEASE", "1": "C_VIRUS"}
    assert res["changed"] == 1 and res["free"] == 1


def test_confident_unique_top1_is_never_moved():
    gc = client([("B", "Z")])
    res = gc.optimize_selection([(0, [("A", 0.95), ("B", 0.94)]), (1, [("Z", 1.0)])])
    assert res["selections"]["0"] == "A"


def test_near_tie_below_pin_only_within_margin():
    gc = client([("B", "Z"), ("C", "Z")])
    cands = [(0, [("A", 0.80), ("B", 0.79), ("C", 0.70)]), (1, [("Z", 1.0)])]
    assert client([("C", "Z")]).optimize_selection(cands)["selections"]["0"] == "A"   # C is 0.10 behind
    assert gc.optimize_selection(cands)["selections"]["0"] == "B"
    assert gc.optimize_selection(cands, switch_margin=0.0)["selections"]["0"] == "A"


def test_equal_support_keeps_retrieval_order():
    gc = client([("A", "Z"), ("B", "Z")])
    res = gc.optimize_selection([(0, [("A", 1.0), ("B", 1.0)]), (1, [("Z", 1.0)])])
    assert res["selections"]["0"] == "A" and res["changed"] == 0


def test_same_text_mentions_do_not_anchor_each_other():
    # two "cold" mentions: mention 1's top-1 (C_TEMP) must not vote for C_TEMP's neighbour
    gc = client([("C_DISEASE", "C_TEMP")])
    cands = [(0, [("C_TEMP", 1.0), ("C_DISEASE", 1.0)]), (1, [("C_TEMP", 1.0), ("C_DISEASE", 1.0)])]
    res = gc.optimize_selection(cands, labels={0: "Cold", 1: "cold."})
    assert res["selections"] == {"0": "C_TEMP", "1": "C_TEMP"}
    # without labels the echo is counted (the tool's old behaviour)
    assert gc.optimize_selection(cands)["selections"]["0"] == "C_DISEASE"


def test_fixed_annotations_anchor_but_never_move():
    gc = client([("B", "H")])
    res = gc.optimize_selection([(0, [("A", 1.0), ("B", 1.0)])], fixed={5: "H"})
    assert res["selections"] == {"0": "B"}
    assert "5" not in res["selections"]


def test_bare_id_lists_are_one_tie():
    gc = client([("B", "Z")])
    res = gc.optimize_selection([(0, ["A", "B"]), (1, ["Z"])])
    assert res["selections"]["0"] == "B"


def test_empty_input():
    assert client([]).optimize_selection([])["selections"] == {}
