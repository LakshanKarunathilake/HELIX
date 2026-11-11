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


# ── composite ids: matched through every part (Neo4j stubbed at the query level) ──
def node_client(edges, names=None, multi=None):
    """Fake Neo4j over single-code nodes. ``multi`` maps a code to extra node copies
    (one MeSH code sits on several CUI nodes in the mesh graph)."""
    names = names or {}
    c = Neo4jGraphClient.__new__(Neo4jGraphClient)
    c.schema = GraphSchema()
    c.calls = []

    def run(cypher, ids=(), lim=None, **_):
        c.calls.append(list(ids))
        ids = set(ids)
        rows = []
        if "UNWIND" in cypher:                                    # neighbours
            for a, b in edges:
                for x, y in ((a, b), (b, a)):
                    if x in ids and y not in ids:
                        rows.append({"source": x, "target": y, "rel": "r", "target_name": names.get(y)})
            return rows[:lim]
        if "AS name" in cypher:                                   # names
            return [{"id": k, "name": names.get(k)} for k in sorted(ids)
                    for _ in range(1 + (multi or {}).get(k, 0)) if k in names]
        for a, b in edges:                                        # edges among
            if a in ids and b in ids:
                for _ in range(1 + (multi or {}).get(a, 0)):      # duplicate rows from copies
                    rows.append({"source": min(a, b), "target": max(a, b), "rel": "r"})
        return rows

    c._run = run
    return c


def test_id_parts():
    from eltool.graph_client import id_parts
    assert id_parts("D002544") == ["D002544"]
    assert id_parts("601367|D002544") == ["601367|D002544", "601367", "D002544"]
    assert id_parts(" C565100 | 125852 ") == ["C565100 | 125852", "C565100", "125852"]
    assert id_parts("") == [] and id_parts(None) == []


def test_composite_id_is_linked_through_any_part():
    gc = node_client([("D002544", "D001249")])
    assert gc._edges_among(["601367|D002544", "D001249"]) == [("601367|D002544", "D001249")]
    assert set(gc.calls[0]) >= {"601367", "D002544", "D001249"}
    # a composite *mention* id links through each of its concepts
    gc = node_client([("D001943", "D009369")])
    assert gc._edges_among(["D010051|D001943", "D009369"]) == [("D009369", "D010051|D001943")]


def test_ids_sharing_a_part_never_link_to_each_other():
    gc = node_client([("D002544", "D001249")])
    # the composite and its own part are one concept, even if the graph relates their parts
    assert gc._edges_among(["601367|D002544", "D002544"]) == []
    gc = node_client([("601367", "D002544")])
    assert gc._edges_among(["601367|D002544", "D002544|999"]) == []
    # but both still link to a third concept
    gc = node_client([("D002544", "D001249")])
    assert gc._edges_among(["601367|D002544", "D002544", "D001249"]) == [
        ("601367|D002544", "D001249"), ("D001249", "D002544")]


def test_optimize_uses_composite_candidates_and_anchors():
    gc = node_client([("D002544", "D001249"), ("C567286", "D009369")])
    # mention 0 ties; its composite candidate shares a part with the anchor of mention 1's relation
    res = gc.optimize_selection([(0, [("D000001", 1.0), ("601367|D002544", 1.0)]),
                                 (1, [("D001249", 0.99)])], labels={0: "a", 1: "b"})
    assert res["selections"]["0"] == "601367|D002544"
    # a composite anchor (mention 1's top-1) supports a single-id candidate
    res = gc.optimize_selection([(0, [("D000001", 1.0), ("D009369", 1.0)]),
                                 (1, [("612520|C567286", 0.99)])], labels={0: "a", 1: "b"})
    assert res["selections"]["0"] == "D009369"
    # a fixed (human-accepted) composite anchors too
    res = gc.optimize_selection([(0, [("D000001", 1.0), ("D009369", 1.0)])], fixed={3: "612520|C567286"})
    assert res["selections"]["0"] == "D009369"


def test_document_graph_composite_is_not_isolated():
    gc = node_client([("D002544", "D001249")], names={"D002544": "Cardiomyopathies", "D001249": "Asthma"})
    res = gc.document_graph(["601367|D002544", "D001249", "194300"])
    nodes = {n["id"]: n for n in res.nodes}
    assert set(nodes) == {"601367|D002544", "D001249", "194300"}
    assert nodes["601367|D002544"]["isolated"] is False and nodes["601367|D002544"]["degree"] == 1
    assert nodes["601367|D002544"]["name"] == "Cardiomyopathies"
    assert nodes["194300"]["isolated"] is True          # OMIM-only id: no node in a MeSH graph
    assert res.edges == [{"source": "601367|D002544", "target": "D001249", "rel": "r", "rels": ["r"]}] or \
           res.edges == [{"source": "D001249", "target": "601367|D002544", "rel": "r", "rels": ["r"]}]


def test_candidate_graph_composite_edges_and_neighbours():
    gc = node_client([("D002544", "D001249"), ("D002544", "D000999")], names={"D000999": "Other"})
    res = gc.candidate_graph(["601367|D002544", "D001249"], neighbor_limit=10)
    ids = {n["id"] for n in res.nodes}
    assert ids == {"601367|D002544", "D001249", "D000999"}       # parts are not shown as extra nodes
    pairs = {frozenset((e["source"], e["target"])) for e in res.edges}
    assert pairs == {frozenset(("601367|D002544", "D001249")), frozenset(("601367|D002544", "D000999"))}


def test_names_for_composites_and_duplicate_nodes():
    gc = node_client([], names={"D010051": "Ovarian Neoplasms", "D001943": "Breast Neoplasms",
                                "D002544": "Cardiomyopathies"}, multi={"D002544": 2})
    assert gc._names_for(["D010051|D001943", "601367|D002544", "194300"]) == {
        "D010051|D001943": "Ovarian Neoplasms | Breast Neoplasms",
        "601367|D002544": "Cardiomyopathies"}


def test_one_code_on_several_nodes_gives_one_edge():
    gc = node_client([("D008579", "D001249")], multi={"D008579": 25})
    assert gc._edges_among(["D008579", "D001249"]) == [("D001249", "D008579")]
