from eltool.llm_evidence import render_graph_evidence


def _graph(nodes, edges, enabled=True):
    g = {"nodes": nodes, "edges": edges}
    if enabled is not None:
        g["enabled"] = enabled
    return g


def test_candidate_to_candidate_edges_come_first():
    nodes = [
        {"id": "C001", "label": "Alpha"},
        {"id": "C002", "label": "Beta"},
        {"id": "C003", "label": "Gamma"},
    ]
    edges = [
        {"source": "C001", "target": "C003", "rel": "isa"},  # candidate -> neighbour
        {"source": "C003", "target": "C002", "rel": "part_of"},  # neighbour -> candidate (reversed)
        {"source": "C002", "target": "C001", "rel": "isa"},  # candidate <-> candidate
    ]
    out = render_graph_evidence(_graph(nodes, edges), ["C001", "C002"])
    lines = out.split("\n")
    assert lines == [
        "C002 (Beta) —[isa]— C001 (Alpha)",
        "C001 (Alpha) —[isa]— C003 (Gamma)",
        "C002 (Beta) —[part_of]— C003 (Gamma)",
    ]


def test_names_fall_back_to_id():
    nodes = [{"id": "C001", "label": "Alpha"}, {"id": "C009"}, {"id": "C010", "label": ""}]
    edges = [
        {"source": "C001", "target": "C009", "rel": None},
        {"source": "C001", "target": "C010", "rel": ""},
    ]
    out = render_graph_evidence(_graph(nodes, edges), ["C001"])
    assert out.split("\n") == [
        "C001 (Alpha) —[related]— C009 (C009)",
        "C001 (Alpha) —[related]— C010 (C010)",
    ]


def test_empty_or_disabled_graph_gives_empty_string():
    nodes = [{"id": "C001", "label": "Alpha"}, {"id": "C002", "label": "Beta"}]
    edges = [{"source": "C001", "target": "C002", "rel": "isa"}]
    assert render_graph_evidence(None, ["C001"]) == ""
    assert render_graph_evidence({}, ["C001"]) == ""
    assert render_graph_evidence(_graph(nodes, edges, enabled=False), ["C001"]) == ""
    assert render_graph_evidence(_graph(nodes, [], enabled=True), ["C001"]) == ""
    # Edges touching no candidate are dropped.
    assert render_graph_evidence(_graph(nodes, edges), ["C999"]) == ""


def test_missing_enabled_key_is_treated_as_enabled():
    nodes = [{"id": "C001", "label": "Alpha"}, {"id": "C002", "label": "Beta"}]
    edges = [{"source": "C001", "target": "C002", "rel": "isa"}]
    out = render_graph_evidence(_graph(nodes, edges, enabled=None), ["C001"])
    assert out == "C001 (Alpha) —[isa]— C002 (Beta)"


def test_duplicate_pairs_with_same_rel_are_removed():
    nodes = [{"id": "C001", "label": "Alpha"}, {"id": "C002", "label": "Beta"}]
    edges = [
        {"source": "C001", "target": "C002", "rel": "isa"},
        {"source": "C002", "target": "C001", "rel": "isa"},
        {"source": "C001", "target": "C002", "rel": "part_of"},
    ]
    out = render_graph_evidence(_graph(nodes, edges), ["C001", "C002"])
    assert out.split("\n") == [
        "C001 (Alpha) —[isa]— C002 (Beta)",
        "C001 (Alpha) —[part_of]— C002 (Beta)",
    ]


def test_output_is_capped_at_max_lines():
    nodes = [{"id": "C000", "label": "Hub"}]
    edges = []
    for i in range(60):
        nid = f"N{i:03d}"
        nodes.append({"id": nid, "label": f"Neighbour {i}"})
        edges.append({"source": "C000", "target": nid, "rel": "isa"})
    out = render_graph_evidence(_graph(nodes, edges), ["C000"])
    assert len(out.split("\n")) == 30
    default_cap = render_graph_evidence(_graph(nodes, edges), ["C000"], max_lines=5)
    assert len(default_cap.split("\n")) == 5
