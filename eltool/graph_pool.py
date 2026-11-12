"""Per-connection pool of Neo4j graph clients, and session -> client resolution."""
from __future__ import annotations

import threading
from collections import OrderedDict

from pydantic import ValidationError

from eltool.config import GraphConnection, SessionConfig
from eltool.graph_client import GraphError, Neo4jGraphClient, load_neo4j_settings
from eltool.hosting import hosted_mode


class GraphClientPool:
    """LRU pool keyed on the full connection (uri, credentials, schema)."""

    def __init__(self, max_size: int = 32):
        self.max_size = max_size
        self._clients: OrderedDict[str, Neo4jGraphClient] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, conn: GraphConnection) -> Neo4jGraphClient:
        key = conn.model_dump_json()
        evicted: list[Neo4jGraphClient] = []
        with self._lock:
            client = self._clients.get(key)
            if client is not None:
                self._clients.move_to_end(key)
            else:
                client = Neo4jGraphClient.from_connection(conn)
                self._clients[key] = client
                while len(self._clients) > self.max_size:
                    _, old = self._clients.popitem(last=False)
                    evicted.append(old)
        for old in evicted:
            try:
                old.close()
            except Exception:  # pragma: no cover
                pass
        return client


def connection_from_settings(settings: dict) -> GraphConnection:
    """Map the load_neo4j_settings() dict (incl. its schema block) to a GraphConnection."""
    known = set(GraphConnection.model_fields) - {"uri", "user", "password", "database"}
    schema = settings.get("schema") or {}
    extra = {k: v for k, v in schema.items() if k in known}
    for k in ("rel_include", "rel_exclude"):
        if extra.get(k) is None:
            extra.pop(k, None)
    try:
        return GraphConnection(
            uri=settings["uri"],
            user=settings.get("user") or "neo4j",
            password=settings.get("password") or "",
            database=settings.get("database") or None,
            **extra,
        )
    except ValidationError as e:
        raise GraphError(f"Invalid Neo4j settings in neo4j.yaml: {e}") from e


def resolve_graph_client(cfg: SessionConfig, pool: GraphClientPool) -> Neo4jGraphClient:
    """session.graph, else (local only) the server neo4j.yaml, else GraphError."""
    if cfg.graph is not None:
        return pool.get(cfg.graph)
    if hosted_mode():
        raise GraphError("This session has no Neo4j connection configured.")
    return pool.get(connection_from_settings(load_neo4j_settings()))
