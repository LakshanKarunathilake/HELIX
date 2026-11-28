"""The demo graph builder loads the MeSH disease hierarchy in HELIX's default schema.

No Neo4j server is used: a fake driver records every query and its parameters.
"""
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from neo4j.exceptions import AuthError, ClientError, ServiceUnavailable

_spec = importlib.util.spec_from_file_location(
    "build_demo_graph",
    Path(__file__).resolve().parent.parent / "scripts" / "build_demo_graph.py",
)
bdg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdg)

from eltool.mesh import Descriptor, read_descriptors  # noqa: E402  (the script puts the root on sys.path)

FIX = Path(__file__).parent / "fixtures" / "mesh_mini.xml"
graph_rows, load_graph = bdg.graph_rows, bdg.load_graph


class FakeResult:
    def __init__(self, count):
        self.count, self.consumed = count, False

    def single(self):
        self.consumed = True
        return {"n": self.count}

    def consume(self):
        self.consumed = True


class FakeDriver:
    """Records queries. ``deletes`` scripts what each delete batch reports removed;
    ``connect_errors`` are raised by successive connectivity checks; ``foreign`` is
    what the guard counts: concepts without a MeSH id."""

    def __init__(self, deletes=(2, 0), connect_errors=(), query_error=None, foreign=0):
        self.foreign = foreign
        self.deletes = list(deletes)
        self.connect_errors = list(connect_errors)
        self.query_error = query_error
        self.calls, self.results = [], []
        self.checks, self.closed = 0, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def verify_connectivity(self):
        self.checks += 1
        if self.connect_errors:
            raise self.connect_errors.pop(0)

    def session(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def run(self, query, parameters=None):
        params = dict(parameters or {})
        self.driver.calls.append((query, params))
        if self.driver.query_error:
            raise self.driver.query_error
        if "=~" in query:
            count = self.driver.foreign
        elif "DELETE" in query:
            count = self.driver.deletes.pop(0)
        else:
            count = len(params.get("rows", []))
        self.driver.results.append(FakeResult(count))
        return self.driver.results[-1]


def kind(query):
    if "=~" in query:
        return "guard"
    if query.startswith("CREATE CONSTRAINT"):
        return "constraint"
    if "DETACH DELETE" in query:
        return "delete"
    return "rels" if "RELATES_TO" in query else "nodes"


def descriptor(ui, *tree_numbers, see_also=()):
    return Descriptor(ui=ui, name=ui.lower(), terms=(ui.lower(),), tree_numbers=tree_numbers,
                      definition="", see_also=tuple(see_also))


def triples(rels):
    return [(r["start"], r["rel"], r["end"]) for r in rels]


def forbid_driver(monkeypatch):
    def made(*args, **kwargs):
        raise AssertionError("a driver was made")

    monkeypatch.setattr(bdg.GraphDatabase, "driver", made)


def use_driver(monkeypatch, fake):
    made = []

    def driver(uri, **kwargs):
        made.append((uri, kwargs["auth"]))
        return fake

    monkeypatch.setattr(bdg.GraphDatabase, "driver", driver)
    return made


@pytest.fixture
def clock(monkeypatch):
    """Time that only passes when the script sleeps."""
    now = [0.0]
    monkeypatch.setattr(bdg.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(bdg.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


# -- rows ------------------------------------------------------------------

def test_rows_from_hierarchy_and_see_also():
    nodes, rels = graph_rows(read_descriptors(FIX))
    assert {"cui": "D001249", "concept": "Asthma"} in nodes and len(nodes) == 3
    assert {(r["start"], r["rel"], r["end"]) for r in rels} == {
        ("D001982", "parent", "D012140"), ("D012140", "child", "D001982"),
        ("D001249", "parent", "D001982"), ("D001982", "child", "D001249"),
        ("D001249", "see_related", "D001982")}
    assert len(rels) == 5


def test_a_pair_linked_through_several_tree_numbers_appears_once():
    _, rels = graph_rows([descriptor("A", "C01", "C02"),
                          descriptor("B", "C01.100", "C02.200")])
    assert triples(rels) == [("B", "parent", "A"), ("A", "child", "B")]


def test_see_related_listed_by_both_ends_appears_once():
    _, rels = graph_rows([descriptor("A", "C01", see_also=["B", "B"]),
                          descriptor("B", "C02", see_also=["A"])])
    assert triples(rels) == [("A", "see_related", "B")]


def test_links_need_two_different_nodes_in_the_set():
    nodes, rels = graph_rows([
        # "Z" is outside the set, "A" refers to itself
        descriptor("A", "C01", see_also=["Z", "A"]),
        # nobody owns the immediate parent C01.100: no link to the grandparent
        descriptor("B", "C01.100.200"),
        # a descriptor under one of its own tree numbers is not its own parent
        descriptor("C", "C03", "C03.100"),
    ])
    assert [n["cui"] for n in nodes] == ["A", "B", "C"]
    assert rels == []


# -- loading ---------------------------------------------------------------

def test_load_clears_then_creates():
    nodes, rels = graph_rows(read_descriptors(FIX))
    driver = FakeDriver(deletes=[2, 1, 0])
    assert load_graph(driver, nodes, rels) == (3, 5)
    assert [kind(q) for q, _ in driver.calls] == [
        "guard", "constraint", "delete", "delete", "delete", "nodes", "rels"]
    assert all(result.consumed for result in driver.results)
    constraint = driver.calls[1][0]
    assert "(c:Concept)" in constraint and "c.cui IS UNIQUE" in constraint
    for query, params in driver.calls[2:5]:
        assert "LIMIT $limit" in query and params == {"limit": 5000}
    assert driver.calls[5][1] == {"rows": nodes}
    assert driver.calls[6][1] == {"rows": rels}


def test_queries_use_the_schema_helix_reads_by_default():
    from eltool.graph_client import GraphSchema

    s = GraphSchema()
    driver = FakeDriver()
    load_graph(driver, *graph_rows(read_descriptors(FIX)))
    queries = {kind(q): q for q, _ in driver.calls}
    assert f"(:{s.node_label} {{{s.id_prop}: row.cui, {s.name_prop}: row.concept}})" \
        in queries["nodes"]
    assert f"(a)-[:{s.rel_type} {{{s.rel_prop}: row.rel}}]->(b)" in queries["rels"]
    assert f"(a:{s.node_label} {{{s.id_prop}: row.`start`}})" in queries["rels"]
    assert f"(b:{s.node_label} {{{s.id_prop}: row.`end`}})" in queries["rels"]


def test_rows_are_sent_in_batches():
    nodes, rels = graph_rows(read_descriptors(FIX))
    driver = FakeDriver()
    assert load_graph(driver, nodes, rels, batch=2) == (3, 5)
    sent = {name: [p["rows"] for q, p in driver.calls if kind(q) == name]
            for name in ("nodes", "rels")}
    assert sent == {"nodes": [nodes[:2], nodes[2:]], "rels": [rels[:2], rels[2:4], rels[4:]]}
    assert all(p == {"limit": 2} for q, p in driver.calls if kind(q) == "delete")


def test_second_load_issues_the_same_queries():
    nodes, rels = graph_rows(read_descriptors(FIX))
    driver = FakeDriver(deletes=[2, 0, 2, 0])
    first = load_graph(driver, nodes, rels)
    once = list(driver.calls)
    second = load_graph(driver, nodes, rels)
    assert first == second == (3, 5)
    assert driver.calls == once + once
    assert [kind(q) for q, _ in once] == [
        "guard", "constraint", "delete", "delete", "nodes", "rels"]


def test_empty_input_leaves_the_graph_alone():
    driver = FakeDriver()
    with pytest.raises(ValueError, match="no descriptors"):
        load_graph(driver, [], [])
    assert driver.calls == []


@pytest.mark.parametrize("batch", [0, -1])
def test_batch_below_one_is_refused_before_any_query(batch):
    driver = FakeDriver()
    with pytest.raises(ValueError, match="batch"):
        load_graph(driver, *graph_rows(read_descriptors(FIX)), batch=batch)
    assert driver.calls == []


# -- delete guard ------------------------------------------------------------

def writes(driver):
    return [q for q, _ in driver.calls
            if re.search(r"\b(CREATE|DELETE|MERGE|SET|REMOVE|DROP)\b", q)]


def test_the_guard_counts_concepts_without_a_mesh_id():
    driver = FakeDriver()
    load_graph(driver, *graph_rows(read_descriptors(FIX)))
    query, params = driver.calls[0]
    assert "MATCH (c:Concept)" in query and "count(c) AS n" in query
    # =~ gives null, not false, for a cui that is not a string; those count too
    assert "NOT coalesce(coalesce(c.cui, '') =~ 'D[0-9]+', false)" in query
    assert params == {}


@pytest.mark.parametrize("foreign", [1, 250_000])
def test_a_graph_that_is_not_the_demo_is_left_alone(foreign):
    driver = FakeDriver(foreign=foreign)
    with pytest.raises(SystemExit) as stop:
        load_graph(driver, *graph_rows(read_descriptors(FIX)), uri="bolt://mine:7687")
    message = str(stop.value)
    assert "bolt://mine:7687" in message and f" {foreign} " in message
    assert "does not look like the demo graph" in message
    assert "--replace" in message and "\n" not in message
    assert [kind(q) for q, _ in driver.calls] == ["guard"]
    assert writes(driver) == []


def test_replace_loads_over_another_graph():
    driver = FakeDriver(foreign=7)
    assert load_graph(driver, *graph_rows(read_descriptors(FIX)), replace=True) == (3, 5)
    assert [kind(q) for q, _ in driver.calls] == [
        "guard", "constraint", "delete", "delete", "nodes", "rels"]
    assert len(writes(driver)) == 5


# -- command line ------------------------------------------------------------

@pytest.mark.parametrize("password", [None, "", "short", "1234567"])
def test_password_required(monkeypatch, password):
    forbid_driver(monkeypatch)
    if password is None:
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("NEO4J_PASSWORD", password)
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(FIX)])
    assert "NEO4J_PASSWORD" in str(stop.value) and "8 characters" in str(stop.value)


def test_missing_mesh_file_is_refused_before_connecting(monkeypatch, tmp_path):
    forbid_driver(monkeypatch)
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    missing = tmp_path / "desc2026.gz"
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(missing)])
    assert str(missing) in str(stop.value)


def test_main_refuses_a_graph_that_is_not_the_demo(monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    fake = FakeDriver(foreign=4)
    use_driver(monkeypatch, fake)
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(FIX), "--uri", "bolt://mine:7687"])
    message = str(stop.value)
    assert "bolt://mine:7687" in message and " 4 " in message and "\n" not in message
    assert writes(fake) == [] and fake.closed
    assert "Loaded" not in capsys.readouterr().out


def test_main_replace_loads_over_another_graph(monkeypatch, capsys):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    fake = FakeDriver(foreign=4)
    use_driver(monkeypatch, fake)
    bdg.main(["--mesh", str(FIX), "--replace"])
    assert [kind(q) for q, _ in fake.calls][-2:] == ["nodes", "rels"]
    assert "3 concepts and 5 relationships" in capsys.readouterr().out


@pytest.mark.parametrize("record", [
    "",  # no descriptors at all
    "<DescriptorRecord><DescriptorUI>D1</DescriptorUI>"
    "<TreeNumberList><TreeNumber>A01</TreeNumber></TreeNumberList></DescriptorRecord>",
])
def test_file_without_disease_descriptors_is_refused_before_connecting(
        monkeypatch, tmp_path, record):
    forbid_driver(monkeypatch)
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    other = tmp_path / "other.xml"
    other.write_text(f"<DescriptorRecordSet>{record}</DescriptorRecordSet>", encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(other)])
    message = str(stop.value)
    assert str(other) in message and "\n" not in message


def test_truncated_file_fails_before_connecting(monkeypatch, tmp_path):
    forbid_driver(monkeypatch)
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    xml = FIX.read_bytes()
    cut = tmp_path / "cut.xml"
    cut.write_bytes(xml[:xml.index(b"D001249</DescriptorUI>")])  # two whole records precede
    with pytest.raises(ET.ParseError):
        bdg.main(["--mesh", str(cut)])


@pytest.mark.parametrize("extra, uri, user", [
    ([], "bolt://neo4j:7687", "neo4j"),
    (["--uri", "bolt://other:1", "--user", "loader"], "bolt://other:1", "loader"),
])
def test_main_loads_the_graph(monkeypatch, capsys, extra, uri, user):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    fake = FakeDriver()
    made = use_driver(monkeypatch, fake)
    bdg.main(["--mesh", str(FIX), *extra])
    assert made == [(uri, (user, "12345678"))]
    assert fake.checks == 1 and fake.closed
    assert [kind(q) for q, _ in fake.calls][-2:] == ["nodes", "rels"]
    assert "3 concepts and 5 relationships" in capsys.readouterr().out


def test_main_asks_for_no_server_notifications(monkeypatch):
    # on an empty database the guard's query would otherwise log "label does not exist"
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    options = {}
    monkeypatch.setattr(bdg.GraphDatabase, "driver",
                        lambda uri, **kwargs: options.update(kwargs) or FakeDriver())
    bdg.main(["--mesh", str(FIX)])
    assert options["notifications_min_severity"] == "OFF"


def test_main_waits_for_neo4j_to_accept_connections(monkeypatch, clock):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    fake = FakeDriver(connect_errors=[ServiceUnavailable("not yet")] * 3)
    use_driver(monkeypatch, fake)
    bdg.main(["--mesh", str(FIX)])
    assert fake.checks == 4 and fake.calls


def test_main_gives_up_after_120_seconds_with_one_line(monkeypatch, clock):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    fake = FakeDriver(connect_errors=[ServiceUnavailable("connection\nrefused")] * 1000)
    use_driver(monkeypatch, fake)
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(FIX)])
    message = str(stop.value)
    assert "bolt://neo4j:7687" in message and "120 s" in message
    assert "connection refused" in message and "\n" not in message
    assert 120 <= clock[0] < 125
    assert fake.calls == [] and fake.closed


def test_main_reports_a_wrong_password_without_retrying(monkeypatch, clock):
    monkeypatch.setenv("NEO4J_PASSWORD", "not-the-one")
    fake = FakeDriver(connect_errors=[AuthError("unauthorized")])
    use_driver(monkeypatch, fake)
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(FIX)])
    message = str(stop.value)
    assert "NEO4J_PASSWORD" in message and "not-the-one" not in message and "\n" not in message
    assert fake.checks == 1 and fake.calls == []


def test_main_reports_a_failed_query_with_one_line(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "12345678")
    use_driver(monkeypatch, FakeDriver(query_error=ClientError("no\nspace")))
    with pytest.raises(SystemExit) as stop:
        bdg.main(["--mesh", str(FIX)])
    assert "no space" in str(stop.value) and "\n" not in str(stop.value)
