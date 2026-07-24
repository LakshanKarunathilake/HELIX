"""
Neo4j graph client for HELIX candidate/entity neighborhoods.

Two levels of graph exploration:

* **Individual level** — given the candidate CUIs retrieved for one mention,
  ``candidate_graph`` returns how those candidates connect to each other and to
  their 1-hop neighbors. Seeing that one "lung" candidate sits among *anatomy*
  concepts and another among *disease* concepts makes disambiguation obvious.

* **Document level** — given the CUIs a human accepted across all mentions of a
  document, ``document_graph`` returns the subgraph among those selections plus a
  per-node connection count. Well-connected entities corroborate each other; an
  isolated selection (0 links to the rest) is flagged so the annotator can
  reconsider / switch it during the document-level "negotiation" step.

Schema (from the user's Neo4j):
    (:Concept {cui})  -[:RELATES_TO {rel}]-  (:Concept {cui})
The relationship *type* is generic ``RELATES_TO``; the semantic label lives in
the ``rel`` property (e.g. "mapped_from", "is_a"). Label / id-prop / rel-type /
rel-prop are all configurable but validated as identifiers to keep the
interpolated Cypher injection-safe.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Repo root = parent of the eltool package.
_ROOT = Path(__file__).resolve().parent.parent


def _config_search_paths() -> list[Path]:
    """Where we look for the Neo4j config file, in priority order."""
    paths: list[Path] = []
    env_path = os.environ.get("HELIX_NEO4J_CONFIG")
    if env_path:
        paths.append(Path(env_path))
    paths += [
        _ROOT / "neo4j.yaml",
        _ROOT / "neo4j.yml",
        _ROOT / "config" / "neo4j.yaml",
    ]
    return paths


def load_neo4j_settings() -> dict[str, Any]:
    """
    Resolve Neo4j settings. Precedence: config file > environment > defaults.

    File format (YAML):
        uri: bolt://localhost:7687
        user: neo4j
        password: secret
        database: neo4j          # optional
        schema:                  # optional; overrides the built-in defaults
          node_label: Concept
          id_prop: cui
          rel_type: RELATES_TO
          rel_prop: rel
          name_prop: concept
    """
    file_cfg: dict[str, Any] = {}
    src: Path | None = None
    for path in _config_search_paths():
        if path.is_file():
            try:
                import yaml
                file_cfg = yaml.safe_load(path.read_text()) or {}
                src = path
                break
            except Exception as e:  # pragma: no cover
                logger.warning("Failed to read Neo4j config %s: %s", path, e)
    if src:
        logger.info("Loaded Neo4j settings from %s", src)

    def pick(key: str, env: str, default: str | None) -> str | None:
        val = file_cfg.get(key)
        if val is not None and str(val) != "":
            return str(val)
        return os.environ.get(env, default)

    schema_cfg = file_cfg.get("schema") or {}
    return {
        "uri": pick("uri", "NEO4J_URI", "bolt://localhost:7687"),
        "user": pick("user", "NEO4J_USER", "neo4j"),
        "password": pick("password", "NEO4J_PASSWORD", ""),
        "database": pick("database", "NEO4J_DATABASE", None) or None,
        "schema": schema_cfg if isinstance(schema_cfg, dict) else {},
        "source": str(src) if src else None,
    }


class GraphError(RuntimeError):
    """Raised when a graph query cannot be completed; callers degrade gracefully."""


def _ident(value: str, what: str) -> str:
    """Validate a Cypher identifier (label / property / rel type) before interpolation."""
    if not _IDENT_RE.match(value or ""):
        raise GraphError(f"Invalid {what} for Cypher: {value!r}")
    return value


@dataclass
class GraphSchema:
    node_label: str = "Concept"
    id_prop: str = "cui"
    rel_type: str = "RELATES_TO"
    rel_prop: str = "rel"          # property holding the semantic relation label
    name_prop: str = "concept"     # human-readable node label property (falls back to cui)
    # Optional relation-label filters (values of the `rel` property). Empty = all.
    rel_include: list[str] = field(default_factory=list)
    rel_exclude: list[str] = field(default_factory=list)

    def validated(self) -> "GraphSchema":
        _ident(self.node_label, "node_label")
        _ident(self.id_prop, "id_prop")
        _ident(self.rel_type, "rel_type")
        _ident(self.rel_prop, "rel_prop")
        _ident(self.name_prop, "name_prop")
        return self

    def rel_filter(self, alias: str = "r") -> tuple[str, dict[str, Any]]:
        """Extra WHERE fragment (leading ' AND …') + params for relation filtering."""
        clauses, params = [], {}
        if self.rel_include:
            clauses.append(f"{alias}.{self.rel_prop} IN $rel_include")
            params["rel_include"] = list(self.rel_include)
        if self.rel_exclude:
            clauses.append(f"NOT {alias}.{self.rel_prop} IN $rel_exclude")
            params["rel_exclude"] = list(self.rel_exclude)
        clause = (" AND " + " AND ".join(clauses)) if clauses else ""
        return clause, params


@dataclass
class GraphResult:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges}


class Neo4jGraphClient:
    """Thin wrapper over the official neo4j driver. Lazily connects."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        schema: GraphSchema | None = None,
    ):
        settings = load_neo4j_settings()
        self.uri = uri or settings["uri"]
        self.user = user or settings["user"]
        self.password = password or settings["password"]
        self.database = database or settings["database"]
        # Schema precedence: explicit arg > file's `schema:` block > built-in defaults.
        if schema is None:
            known = GraphSchema().__dict__
            overrides = {k: v for k, v in settings["schema"].items() if k in known}
            schema = GraphSchema(**{**known, **overrides})
        self.schema = schema.validated()
        self._driver = None

    # ── connection ───────────────────────────────────────────────────────────
    def _driver_lazy(self):
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as e:  # pragma: no cover
                raise GraphError("neo4j driver not installed (pip install neo4j).") from e
            try:
                self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                self._driver.verify_connectivity()
            except Exception as e:
                self._driver = None
                raise GraphError(f"Could not connect to Neo4j at {self.uri}: {e}") from e
        return self._driver

    def _run(self, cypher: str, **params) -> list[dict[str, Any]]:
        driver = self._driver_lazy()
        try:
            with driver.session(database=self.database) as sess:
                return [r.data() for r in sess.run(cypher, **params)]
        except GraphError:
            raise
        except Exception as e:
            raise GraphError(f"Cypher query failed: {e}") from e

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    # ── individual level: candidate neighborhood ─────────────────────────────
    def candidate_graph(self, ids: list[str], neighbor_limit: int = 25) -> GraphResult:
        """How the candidate CUIs connect to each other and to their 1-hop neighbors."""
        ids = [str(i) for i in ids if i]
        if not ids:
            return GraphResult()
        s = self.schema
        L, P, R, RP, N = s.node_label, s.id_prop, s.rel_type, s.rel_prop, s.name_prop
        rf, rparams = s.rel_filter("r")

        # Edges strictly among the candidate set (undirected match, dedup by ordering).
        among = self._run(
            f"MATCH (a:{L})-[r:{R}]-(b:{L}) "
            f"WHERE a.{P} IN $ids AND b.{P} IN $ids AND a.{P} < b.{P}{rf} "
            f"RETURN DISTINCT a.{P} AS source, b.{P} AS target, r.{RP} AS rel",
            ids=ids, **rparams,
        )
        # 1-hop neighbors of each candidate (excluding other candidates), capped.
        neigh = self._run(
            f"UNWIND $ids AS cid "
            f"MATCH (c:{L} {{{P}: cid}})-[r:{R}]-(n:{L}) "
            f"WHERE NOT n.{P} IN $ids{rf} "
            f"WITH cid, n, r LIMIT $lim "
            f"RETURN cid AS source, n.{P} AS target, r.{RP} AS rel, "
            f"n.{N} AS target_name",
            ids=ids, lim=neighbor_limit, **rparams,
        )
        return self._assemble(ids, among, neigh)

    # ── names for a set of ids (so nodes show a human-readable label) ─────────
    def _names_for(self, ids: list[str]) -> dict[str, str]:
        """id -> human-readable name (from the schema's name_prop). Empty if none."""
        ids = [str(i) for i in ids if i]
        if not ids:
            return {}
        s = self.schema
        L, P, N = s.node_label, s.id_prop, s.name_prop
        rows = self._run(
            f"MATCH (n:{L}) WHERE n.{P} IN $ids RETURN n.{P} AS id, n.{N} AS name",
            ids=ids,
        )
        return {r["id"]: r["name"] for r in rows if r.get("name")}

    # ── document level: subgraph among accepted entities ─────────────────────
    def document_graph(self, cuis: list[str]) -> GraphResult:
        """Subgraph among the CUIs the human accepted across a document."""
        cuis = [str(c) for c in cuis if c]
        if not cuis:
            return GraphResult()
        s = self.schema
        L, P, R, RP = s.node_label, s.id_prop, s.rel_type, s.rel_prop
        rf, rparams = s.rel_filter("r")
        among = self._run(
            f"MATCH (a:{L})-[r:{R}]-(b:{L}) "
            f"WHERE a.{P} IN $ids AND b.{P} IN $ids AND a.{P} < b.{P}{rf} "
            f"RETURN DISTINCT a.{P} AS source, b.{P} AS target, r.{RP} AS rel",
            ids=cuis, **rparams,
        )
        res = self._assemble(cuis, among, [])
        # human-readable names for the accepted nodes (the `among` query only returns ids)
        names = self._names_for(cuis)
        # connection count per accepted node → drives the "isolated pick" flag.
        degree: dict[str, int] = {c: 0 for c in cuis}
        for e in res.edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        for n in res.nodes:
            n["degree"] = degree.get(n["id"], 0)
            n["isolated"] = degree.get(n["id"], 0) == 0
            if names.get(n["id"]):
                n["name"] = names[n["id"]]
        return res

    # ── automatic collective disambiguation ─────────────────────────────────
    def _edges_among(self, cuis: list[str]) -> list[tuple[str, str]]:
        """Undirected edges (as ordered pairs) among the given CUIs."""
        if not cuis:
            return []
        s = self.schema
        L, P, R = s.node_label, s.id_prop, s.rel_type
        rf, rparams = s.rel_filter("r")
        rows = self._run(
            f"MATCH (a:{L})-[r:{R}]-(b:{L}) "
            f"WHERE a.{P} IN $ids AND b.{P} IN $ids AND a.{P} < b.{P}{rf} "
            f"RETURN DISTINCT a.{P} AS source, b.{P} AS target",
            ids=cuis, **rparams,
        )
        return [(r["source"], r["target"]) for r in rows]

    def optimize_selection(
        self,
        mention_candidates: list[tuple[int, list[str]]],
        fixed: dict[int, str] | None = None,
        iterations: int = 10,
    ) -> dict[str, Any]:
        """
        Pick one candidate per mention to maximise the number of graph edges among
        the selected concepts (greedy hill-climbing / collective disambiguation).

        mention_candidates: [(mention_idx, [candidate_cui, ...]), ...] to optimise.
        fixed: mentions whose selection is pinned (already annotated); their CUIs
               count towards coherence but are never changed.
        Returns {selections: {idx: cui}} for the OPTIMISED mentions only, plus
        {edges_before, edges_after, changed}.
        """
        fixed = dict(fixed or {})
        mc = [(idx, [c for c in cands if c]) for idx, cands in mention_candidates]
        mc = [(idx, cands) for idx, cands in mc if cands]
        candmap = dict(mc)
        order = [idx for idx, _ in mc]
        universe = sorted({c for _, cands in mc for c in cands} | set(fixed.values()))
        if not universe or not order:
            return {"selections": {}, "edges_before": 0, "edges_after": 0, "changed": 0,
                    "nodes_queried": 0, "edges_found": 0, "query_ms": 0.0, "iterations": 0}

        # Neo4j edge query — the expensive part; measure it explicitly.
        _t0 = time.perf_counter()
        edge_pairs = self._edges_among(universe)
        query_ms = (time.perf_counter() - _t0) * 1000.0

        adj: dict[str, set[str]] = defaultdict(set)
        for a, b in edge_pairs:
            adj[a].add(b)
            adj[b].add(a)

        # full state = pinned mentions + optimisable ones (init from retrieval top-1)
        sel: dict[int, str] = dict(fixed)
        for idx in order:
            sel[idx] = candmap[idx][0]
        initial = dict(sel)

        def internal_edges(values) -> int:
            vals = list({v for v in values})
            c = 0
            for i in range(len(vals)):
                ai = adj.get(vals[i], ())
                for j in range(i + 1, len(vals)):
                    if vals[j] in ai:
                        c += 1
            return c

        iters = 0
        for _ in range(iterations):
            iters += 1
            changed = False
            for idx in order:                      # only optimisable mentions move
                cands = candmap[idx]
                others = {sel[j] for j in sel if j != idx}
                best = max(
                    cands,
                    key=lambda c: (len(adj.get(c, set()) & others),
                                   c == sel.get(idx),
                                   -cands.index(c)),
                )
                if best != sel.get(idx):
                    sel[idx] = best
                    changed = True
            if not changed:
                break

        return {
            "selections": {str(idx): sel[idx] for idx in order},
            "edges_before": internal_edges(initial.values()),
            "edges_after": internal_edges(sel.values()),
            "changed": sum(1 for idx in order if sel[idx] != initial.get(idx)),
            # query cost / scale stats
            "nodes_queried": len(universe),      # candidate CUIs sent to Neo4j
            "edges_found": len(edge_pairs),      # edges Neo4j returned among them
            "query_ms": round(query_ms, 2),      # Neo4j edge-query latency
            "iterations": iters,                 # greedy hill-climb passes
        }

    # ── shared assembly ──────────────────────────────────────────────────────
    def _assemble(self, ids: list[str], among, neigh) -> GraphResult:
        candidate_set = set(ids)
        node_ids = set(ids)
        names: dict[str, str] = {}
        # Collapse the two stored directions (e.g. mapped_to / mapped_from) into
        # a single undirected edge per pair, merging the distinct relation labels.
        pair_rels: dict[tuple[str, str], set[str]] = {}

        def add_edge(a: str, b: str, rel: str | None) -> None:
            if a == b:
                return
            key = (a, b) if a < b else (b, a)
            rels = pair_rels.setdefault(key, set())
            if rel:
                rels.add(rel)

        for row in among:
            add_edge(row["source"], row["target"], row.get("rel"))
        for row in neigh:
            tgt = row["target"]
            node_ids.add(tgt)
            if row.get("target_name"):
                names[tgt] = row["target_name"]
            add_edge(row["source"], tgt, row.get("rel"))

        edges = [
            {"source": a, "target": b,
             "rel": " / ".join(sorted(rels)), "rels": sorted(rels)}
            for (a, b), rels in pair_rels.items()
        ]
        nodes = [
            {"id": nid, "name": names.get(nid, nid), "is_candidate": nid in candidate_set}
            for nid in node_ids
        ]
        return GraphResult(nodes=nodes, edges=edges)
