import pytest
from pydantic import ValidationError

from eltool import graph_client
from eltool.config import GraphConnection, SessionConfig
from eltool.graph_client import GraphError, Neo4jGraphClient
from eltool.graph_pool import GraphClientPool, resolve_graph_client


def _boom():
    raise AssertionError("load_neo4j_settings must not be called")


def _cfg(graph=None):
    d = {
        "session_name": "s", "annotator": "a", "input_path": "x.csv",
        "label_column": "label",
        "kbs": [{"name": "kb", "endpoint_url": "http://127.0.0.1:8765/query"}],
    }
    if graph is not None:
        d["graph"] = graph
    return SessionConfig.model_validate(d)


def test_from_connection_ignores_neo4j_yaml(monkeypatch):
    monkeypatch.setattr(graph_client, "load_neo4j_settings", _boom)
    c = Neo4jGraphClient.from_connection(GraphConnection(uri="neo4j+s://x"))
    assert c.schema.node_label == "Concept"
    assert c.password == ""


def test_bad_scheme_rejected():
    with pytest.raises(ValidationError):
        GraphConnection(uri="http://x")


def test_pool_reuses_and_evicts(monkeypatch):
    closed = []
    monkeypatch.setattr(Neo4jGraphClient, "close", lambda self: closed.append(self.uri))
    pool = GraphClientPool(max_size=2)
    a = GraphConnection(uri="neo4j+s://a")
    b = GraphConnection(uri="neo4j+s://b")
    c = GraphConnection(uri="neo4j+s://c")
    ca = pool.get(a)
    assert pool.get(a) is ca
    pool.get(b)
    assert closed == []
    pool.get(c)
    assert closed == ["neo4j+s://a"]


def test_session_graph_wins(monkeypatch):
    monkeypatch.setattr(graph_client, "load_neo4j_settings", _boom)
    cfg = _cfg({"uri": "neo4j+s://mine"})
    client = resolve_graph_client(cfg, GraphClientPool())
    assert client.uri == "neo4j+s://mine"


def test_legacy_session_uses_server_neo4j_yaml_locally(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    monkeypatch.setattr("eltool.graph_pool.load_neo4j_settings", lambda: {
        "uri": "bolt://localhost:7687", "user": "neo4j", "password": "pw",
        "database": None, "schema": {"node_label": "Thing"}, "source": None,
    })
    client = resolve_graph_client(_cfg(), GraphClientPool())
    assert client.uri == "bolt://localhost:7687"
    assert client.schema.node_label == "Thing"


def test_hosted_without_graph_errors(monkeypatch):
    monkeypatch.setenv("HELIX_HOSTED", "1")
    with pytest.raises(GraphError, match="no Neo4j connection"):
        resolve_graph_client(_cfg(), GraphClientPool())


def test_hosted_plain_bolt_refused_on_connect(monkeypatch):
    import neo4j
    monkeypatch.setenv("HELIX_HOSTED", "1")

    def fail(*a, **k):
        raise AssertionError("driver must not be created")

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", fail)
    c = Neo4jGraphClient.from_connection(GraphConnection(uri="bolt://10.0.0.5:7687"))
    with pytest.raises(GraphError):
        c._driver_lazy()


def test_closed_client_refuses_to_reconnect(monkeypatch):
    import neo4j

    def fail(*a, **k):
        raise AssertionError("driver must not be created")

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", fail)
    c = Neo4jGraphClient.from_connection(GraphConnection(uri="neo4j+s://x"))
    c.close()
    with pytest.raises(GraphError, match="closed"):
        c._driver_lazy()


def _yaml(**over):
    d = {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "",
         "database": None, "schema": {}, "source": None}
    d.update(over)
    return d


def test_yaml_blank_rel_include_ok(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    monkeypatch.setattr("eltool.graph_pool.load_neo4j_settings",
                        lambda: _yaml(schema={"rel_include": None, "rel_exclude": None}))
    c = resolve_graph_client(_cfg(), GraphClientPool())
    assert c.schema.rel_include == [] and c.schema.rel_exclude == []


def test_yaml_schemeless_uri_is_graph_error(monkeypatch):
    monkeypatch.delenv("HELIX_HOSTED", raising=False)
    monkeypatch.setattr("eltool.graph_pool.load_neo4j_settings",
                        lambda: _yaml(uri="localhost:7687"))
    with pytest.raises(GraphError, match="neo4j.yaml"):
        resolve_graph_client(_cfg(), GraphClientPool())


# ── /api/graph/test ──────────────────────────────────────────────────────────
from fastapi.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402


def _post(graph):
    return TestClient(app).post("/api/graph/test", json={"graph": graph}).json()


def test_graph_test_bad_scheme():
    r = _post({"uri": "http://x"})
    assert r["ok"] is False and r["error"]


def test_graph_test_ping_error_still_closes(monkeypatch):
    closed = []

    def ping(self):
        raise GraphError("nope")

    monkeypatch.setattr(Neo4jGraphClient, "ping", ping)
    monkeypatch.setattr(Neo4jGraphClient, "close", lambda self: closed.append(1))
    r = _post({"uri": "neo4j+s://x"})
    assert r == {"ok": False, "error": "nope"}
    assert closed == [1]


def test_graph_test_success(monkeypatch):
    closed = []
    monkeypatch.setattr(Neo4jGraphClient, "ping", lambda self: 42)
    monkeypatch.setattr(Neo4jGraphClient, "close", lambda self: closed.append(1))
    assert _post({"uri": "neo4j+s://x"}) == {"ok": True, "nodes": 42}
    assert closed == [1]
