#!/usr/bin/env python
"""Load the demo graph from NLM's MeSH descriptor file (disease branch) into Neo4j.

Runs inside the api container, with the password in NEO4J_PASSWORD:

    python scripts/build_demo_graph.py --mesh desc2026.gz

One node per descriptor, linked to its parents, its children and its "see
related" descriptors, in HELIX's default schema. Every :Concept node already in
the database is replaced, so point this only at the demo's own Neo4j. A database
holding any :Concept node without a MeSH "D..." id is left untouched unless
--replace is given. The file is read in full before the database is touched; a
load that stops part-way is put right by running the script again.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable

from neo4j import GraphDatabase
from neo4j.exceptions import (AuthError, DriverError, Neo4jError, ServiceUnavailable,
                              SessionExpired, TransientError)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.mesh import Descriptor, read_descriptors  # noqa: E402

DEFAULT_URI = "bolt://neo4j:7687"
MIN_PASSWORD_LENGTH = 8  # Neo4j 5 refuses shorter passwords by default
WAIT_SECONDS = 120
RETRY_SECONDS = 2

# The labels and property names are the defaults of eltool.graph_client.GraphSchema.
# Concepts the demo load cannot have made. =~ matches the whole value, and is null
# for a cui that is not a string, which the outer coalesce counts as no match.
COUNT_FOREIGN = ("MATCH (c:Concept) "
                 "WHERE NOT coalesce(coalesce(c.cui, '') =~ 'D[0-9]+', false) "
                 "RETURN count(c) AS n")
CONSTRAINT = ("CREATE CONSTRAINT concept_cui IF NOT EXISTS "
              "FOR (c:Concept) REQUIRE c.cui IS UNIQUE")
DELETE_BATCH = "MATCH (c:Concept) WITH c LIMIT $limit DETACH DELETE c RETURN count(*) AS n"
CREATE_NODES = ("UNWIND $rows AS row "
                "CREATE (:Concept {cui: row.cui, concept: row.concept}) "
                "RETURN count(*) AS n")
CREATE_RELS = ("UNWIND $rows AS row "
               "MATCH (a:Concept {cui: row.`start`}) "
               "MATCH (b:Concept {cui: row.`end`}) "
               "CREATE (a)-[:RELATES_TO {rel: row.rel}]->(b) "
               "RETURN count(*) AS n")


def graph_rows(descriptors: Iterable[Descriptor]) -> tuple[list[dict], list[dict]]:
    """Node rows ``{cui, concept}`` and relationship rows ``{start, end, rel}``.

    A parent is the descriptor owning a tree number minus its last part. Links are
    only made between two different descriptors of the input, each one once.
    """
    records = {d.ui: d for d in descriptors}
    owners = {number: d.ui for d in records.values() for number in d.tree_numbers}
    hierarchy: dict[tuple[str, str, str], None] = {}  # a set that keeps file order
    related: dict[frozenset[str], tuple[str, str, str]] = {}
    for ui, descriptor in records.items():
        for number in descriptor.tree_numbers:
            parent = owners.get(number.rpartition(".")[0])
            if parent and parent != ui:
                hierarchy[ui, "parent", parent] = None
                hierarchy[parent, "child", ui] = None
        for other in descriptor.see_also:
            if other in records and other != ui:
                related.setdefault(frozenset((ui, other)), (ui, "see_related", other))
    nodes = [{"cui": ui, "concept": d.name} for ui, d in records.items()]
    rels = [{"start": start, "end": end, "rel": rel}
            for start, rel, end in (*hierarchy, *related.values())]
    return nodes, rels


def load_graph(driver, nodes: list[dict], rels: list[dict], *, uri: str = DEFAULT_URI,
               replace: bool = False, batch: int = 5000) -> tuple[int, int]:
    """Replace every concept with these rows; return (nodes, relationships) created.

    Exits, before writing anything, if the database holds a concept that is not
    from MeSH and ``replace`` is false. ``uri`` only names the database then.
    """
    if batch < 1:
        raise ValueError("batch must be at least 1")
    if not nodes:
        raise ValueError("no descriptors to load")
    with driver.session() as session:
        def count(query: str, **params) -> int:
            return session.run(query, params).single()["n"]

        def create(query: str, rows: list[dict]) -> int:
            return sum(count(query, rows=rows[start:start + batch])
                       for start in range(0, len(rows), batch))

        foreign = count(COUNT_FOREIGN)  # first: the constraint below is already a write
        if foreign and not replace:
            raise SystemExit(f"Neo4j at {uri} holds {foreign} :Concept node(s) without a MeSH "
                             f"D... id: this does not look like the demo graph, so nothing was "
                             f"changed (--replace deletes every :Concept node there).")
        session.run(CONSTRAINT).consume()  # also the index the relationship matches use
        while count(DELETE_BATCH, limit=batch):  # in batches, to bound transaction memory
            pass
        return create(CREATE_NODES, nodes), create(CREATE_RELS, rels)


def _one_line(error: Exception) -> str:
    return " ".join(str(error).split())


def _wait_for_neo4j(driver, uri: str) -> None:
    """Retry while the server is still starting; other errors are raised at once."""
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            driver.verify_connectivity()
            return
        except (ServiceUnavailable, SessionExpired, TransientError) as error:
            if time.monotonic() >= deadline:
                raise SystemExit(f"Neo4j at {uri} did not accept connections within "
                                 f"{WAIT_SECONDS} s: {_one_line(error)}") from None
            time.sleep(RETRY_SECONDS)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mesh", type=Path, required=True,
                        help="MeSH descriptor file (desc<year>.xml or .gz)")
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--replace", action="store_true",
                        help="delete every :Concept node even if the database does not look "
                             "like the demo graph")
    args = parser.parse_args(argv)

    password = os.environ.get("NEO4J_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Set NEO4J_PASSWORD to the Neo4j password "
                         f"(at least {MIN_PASSWORD_LENGTH} characters).")
    if not args.mesh.is_file():
        raise SystemExit(f"MeSH descriptor file not found: {args.mesh}")
    nodes, rels = graph_rows(read_descriptors(args.mesh))
    if not nodes:
        raise SystemExit(f"No disease (tree number C...) descriptors found in {args.mesh}")

    try:
        # No server notifications: on an empty database the guard's query would warn
        # that the label and property do not exist yet.
        with GraphDatabase.driver(args.uri, auth=(args.user, password), connection_timeout=10,
                                  notifications_min_severity="OFF") as driver:
            _wait_for_neo4j(driver, args.uri)
            node_count, rel_count = load_graph(driver, nodes, rels, uri=args.uri,
                                               replace=args.replace)
    except AuthError:
        raise SystemExit(f"Neo4j at {args.uri} rejected the login for user {args.user}; "
                         f"check NEO4J_PASSWORD.") from None
    except (DriverError, Neo4jError) as error:
        raise SystemExit(f"Neo4j at {args.uri}: {_one_line(error)}") from None
    print(f"Loaded {node_count} concepts and {rel_count} relationships into {args.uri}")


if __name__ == "__main__":
    main()
