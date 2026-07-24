#!/usr/bin/env python3
"""
Load HELIX graph CSVs into a Neo4j database (driver-based, works against a
running server) and create the ID index that makes lookups fast.

This is the online counterpart to ``graph/build_graph.py`` (which does an offline
``neo4j-admin`` bulk import and needs ``$NEO4J_HOME``). This script needs nothing
but a Bolt connection — it reuses the credentials/schema already in ``neo4j.yaml``
(via ``eltool.graph_client``) — so it works with Neo4j Desktop / a running server
and on both Community and Enterprise.

What it does, in order:
  1. (Enterprise) CREATE the target database and wait for it to come online.
     ``--recreate`` DROPs it first for a clean reload. Community: use an existing db.
  2. Create a UNIQUENESS constraint on the id property (``:Concept(cui)``) — this
     is the "proper index for ID" and it is created **before** loading so that
     node upserts and relationship endpoint lookups are indexed (fast).
  3. Stream the nodes CSV in batches → MERGE nodes with all their properties.
  4. Stream the relationships CSV in batches → connect them by id.
  5. Create any secondary indexes requested with ``--index`` (e.g. the MeSH code).
  6. Verify: node/relationship counts + the indexes present.

Accepts the CSVs produced by ``build_mesh_graph.py`` or ``umls_processor.py``
(headers ``concept:ID,name,[mesh,]cui,:LABEL`` and
``:START_ID,rel,:END_ID,:TYPE``).

Examples:
  # MeSH graph → a new 'mesh' database (leaves 'umls' untouched), index the MeSH code too
  python graph/load_neo4j.py --database mesh \
      --nodes graph/out/mesh_nodes.csv --relationships graph/out/mesh_relations.csv \
      --index mesh --recreate

  # dry run (print the plan, touch nothing)
  python graph/load_neo4j.py --database mesh --dry-run
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("load_neo4j")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Never DROP these — the DBMS built-ins. User data databases (umls, mesh, …) are
# fair game for --recreate, which is already an explicit opt-in guarded by a prompt.
PROTECTED_DBS = {"system", "neo4j"}


def ident(value: str, what: str) -> str:
    """Validate a Cypher identifier (label / property / rel-type / db name) before interpolation."""
    if not _IDENT_RE.match(value or ""):
        raise SystemExit(f"Invalid {what} for Cypher (must be a plain identifier): {value!r}")
    return value


# ── CSV plumbing ────────────────────────────────────────────────────────────

def base_name(header: str) -> str | None:
    """Property name from a neo4j-import header. 'concept:ID'->'concept', 'name'->'name',
    ':LABEL'/':TYPE'/':START_ID' (structural-only) -> None."""
    if not header or header.startswith(":"):
        return None
    return header.split(":", 1)[0]


def find_id_column(headers: list[str], id_prop: str) -> str:
    """The CSV column that holds the node id: a plain column named `id_prop`,
    else the `X:ID` column whose base name is `id_prop`."""
    if id_prop in headers:
        return id_prop
    for h in headers:
        if h.endswith(":ID") and base_name(h) == id_prop:
            return h
    raise SystemExit(f"Nodes CSV has no column for id-prop {id_prop!r}. Columns: {headers}")


def stream_batches(path: Path, transform: Callable[[dict], dict | None],
                   batch_size: int) -> Iterator[list[dict]]:
    """Yield lists of transformed rows (skipping any the transform returns None for)."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        batch: list[dict] = []
        for row in reader:
            out = transform(row)
            if out is None:
                continue
            batch.append(out)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def count_rows(path: Path) -> int:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return max(sum(1 for _ in fh) - 1, 0)   # minus header


# ── Neo4j helpers ───────────────────────────────────────────────────────────

def connect(user: str | None, password: str | None):
    """Connect, preferring an explicit admin override over the (often read-only)
    account in neo4j.yaml. Override order: --user/--password > NEO4J_ADMIN_* env > neo4j.yaml."""
    from eltool.graph_client import load_neo4j_settings
    from neo4j import GraphDatabase
    s = load_neo4j_settings()
    user = user or os.environ.get("NEO4J_ADMIN_USER") or s["user"]
    password = password if password is not None else (os.environ.get("NEO4J_ADMIN_PASSWORD") or s["password"])
    drv = GraphDatabase.driver(s["uri"], auth=(user, password))
    drv.verify_connectivity()
    s = dict(s)
    s["user"] = user
    return drv, s


# Roles that can write data / manage schema. 'reader' cannot; database management needs 'admin'.
WRITE_ROLES = {"admin", "architect", "publisher", "editor"}


def current_user_roles(drv) -> tuple[str | None, list[str]]:
    with drv.session(database="system") as sess:
        rec = sess.run("SHOW CURRENT USER YIELD user, roles RETURN user, roles").single()
    return (rec["user"], list(rec["roles"])) if rec else (None, [])


def server_edition(drv) -> str:
    with drv.session(database="system") as sess:
        rec = sess.run("CALL dbms.components() YIELD edition RETURN edition").single()
        return (rec["edition"] if rec else "community").lower()


def server_import_dir(drv) -> str | None:
    """Best-effort: the server's configured LOAD CSV import directory (may need admin; may be relative)."""
    try:
        with drv.session(database="system") as sess:
            rec = sess.run("SHOW SETTINGS YIELD name, value WHERE name = 'server.directories.import' "
                           "RETURN value").single()
        return rec["value"] if rec else None
    except Exception:
        return None


def database_exists(drv, db: str) -> bool:
    with drv.session(database="system") as sess:
        return sess.run("SHOW DATABASES YIELD name WHERE name = $db RETURN count(*) AS c",
                        db=db).single()["c"] > 0


def wait_online(drv, db: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with drv.session(database="system") as sess:
            rec = sess.run("SHOW DATABASE $db YIELD currentStatus RETURN currentStatus",
                           db=db).single()
        if rec and rec["currentStatus"] == "online":
            return
        time.sleep(1.0)
    raise SystemExit(f"Database {db!r} did not come online within {timeout:.0f}s.")


def create_database(drv, db: str, recreate: bool) -> None:
    ident(db, "database name")
    with drv.session(database="system") as sess:
        if recreate:
            if db in PROTECTED_DBS:
                raise SystemExit(f"Refusing to --recreate protected database {db!r}.")
            logger.info("DROP DATABASE %s IF EXISTS …", db)
            sess.run(f"DROP DATABASE {db} IF EXISTS")
        logger.info("CREATE DATABASE %s IF NOT EXISTS …", db)
        sess.run(f"CREATE DATABASE {db} IF NOT EXISTS")
    wait_online(drv, db)
    logger.info("Database %s is online.", db)


def run_write_batches(drv, db: str, cypher: str, batches: Iterator[list[dict]],
                      total: int, what: str, log_every: int = 250_000) -> int:
    def _work(tx, rows):
        tx.run(cypher, rows=rows)

    done = 0
    next_log = log_every
    with drv.session(database=db) as sess:
        for batch in batches:
            sess.execute_write(_work, batch)
            done += len(batch)
            if done >= next_log:
                logger.info("  %s: %s / %s (%.0f%%)", what, f"{done:,}", f"{total:,}",
                            100.0 * done / total if total else 100.0)
                next_log += log_every
    logger.info("  %s: %s / %s (done)", what, f"{done:,}", f"{total:,}")
    return done


def load_via_import_dir(drv, db: str, import_dir: Path, nodes_path: Path, rels_path: Path,
                        label: str, id_prop: str, id_col: str, node_prop_cols: list[str],
                        rel_type: str, rel_prop: str, rel_verb: str, batch: int) -> None:
    """Copy the CSVs into the server's import directory and load them with server-side
    ``LOAD CSV … CALL {…} IN TRANSACTIONS`` (faster than streaming rows over Bolt)."""
    if not import_dir.is_dir():
        raise SystemExit(f"--import-dir is not a directory: {import_dir}")

    # 1. copy CSVs into the import dir (unless they're already there)
    for src in (nodes_path, rels_path):
        tgt = import_dir / src.name
        if src.resolve() != tgt.resolve():
            logger.info("Copying %s → %s", src.name, tgt)
            shutil.copy(src, tgt)

    # 2. nodes: MERGE keyed on the id column, set every property column
    set_items = [f"n.`{base_name(h)}` = row[{h!r}]" for h in node_prop_cols]
    set_clause = ("SET " + ", ".join(set_items)) if set_items else ""
    node_q = (f"LOAD CSV WITH HEADERS FROM $url AS row "
              f"CALL (row) {{ MERGE (n:{label} {{{id_prop}: row[{id_col!r}]}}) {set_clause} }} "
              f"IN TRANSACTIONS OF {batch} ROWS")
    logger.info("LOAD CSV nodes  ← file:///%s", nodes_path.name)
    with drv.session(database=db) as sess:
        c = sess.run(node_q, url=f"file:///{nodes_path.name}").consume().counters
        logger.info("  nodes created: %s, properties set: %s",
                    f"{c.nodes_created:,}", f"{c.properties_set:,}")

    # 3. relationships: match endpoints by id (uses the constraint index), then CREATE/MERGE
    rel_q = (f"LOAD CSV WITH HEADERS FROM $url AS row "
             f"CALL (row) {{ "
             f"MATCH (a:{label} {{{id_prop}: row[':START_ID']}}) "
             f"MATCH (b:{label} {{{id_prop}: row[':END_ID']}}) "
             f"{rel_verb} (a)-[x:{rel_type} {{{rel_prop}: row[{rel_prop!r}]}}]->(b) }} "
             f"IN TRANSACTIONS OF {batch} ROWS")
    logger.info("LOAD CSV rels   ← file:///%s", rels_path.name)
    with drv.session(database=db) as sess:
        c = sess.run(rel_q, url=f"file:///{rels_path.name}").consume().counters
        logger.info("  relationships created: %s", f"{c.relationships_created:,}")


def admin_needed_msg(user, roles, db, nodes_path, rels_path, index_props, original=None) -> str:
    idx = "".join(f" --index {p}" for p in index_props) or " --index mesh"
    cmd = (f"python graph/load_neo4j.py --database {db} "
           f"--nodes {nodes_path} --relationships {rels_path}{idx} "
           f"--user neo4j --password <ADMIN_PASSWORD>")
    lines = [
        "",
        f"Neo4j account '{user}' (roles={roles or '?'}) cannot create databases or write data.",
        "The neo4j.yaml account is read-only — correct for HELIX queries, but not for loading.",
        "",
        "Re-run with an admin account (the default superuser is 'neo4j'):",
        f"  {cmd}",
        "",
        "…or set NEO4J_ADMIN_USER / NEO4J_ADMIN_PASSWORD and re-run without --user/--password.",
        "On Community (no named databases) add --no-create-db and target an existing database.",
    ]
    if original:
        lines += ["", f"Original Neo4j error: {original}"]
    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Load HELIX graph CSVs into Neo4j and index the id property.")
    ap.add_argument("--database", required=True, help="Target database name (e.g. 'mesh').")
    ap.add_argument("--nodes", default="graph/out/mesh/mesh_nodes.csv", help="Nodes CSV.")
    ap.add_argument("--relationships", default="graph/out/mesh/mesh_relations.csv", help="Relationships CSV.")
    ap.add_argument("--index", action="append", default=[], metavar="PROP",
                    help="Extra node property to range-index (repeatable), e.g. --index mesh --index name.")
    ap.add_argument("--import-dir", help="Neo4j's LOAD CSV import directory. If given, the CSVs are copied "
                                         "there and loaded server-side with LOAD CSV (faster) instead of "
                                         "streamed over Bolt.")
    ap.add_argument("--batch-size", type=int, default=10_000, help="Rows per write transaction (default 10000).")
    ap.add_argument("--recreate", action="store_true",
                    help="DROP the target database first for a clean reload (Enterprise; blocked for umls/neo4j/system).")
    ap.add_argument("--merge-rels", action="store_true",
                    help="MERGE relationships instead of CREATE (idempotent re-runs; slower). Default CREATE.")
    ap.add_argument("--no-create-db", action="store_true",
                    help="Do not CREATE the database (use an existing one, e.g. on Community).")
    ap.add_argument("--user", help="Override the Neo4j user (neo4j.yaml's account is often read-only; "
                                   "creating/loading needs an admin account). Falls back to $NEO4J_ADMIN_USER.")
    ap.add_argument("--password", help="Password for --user. Falls back to $NEO4J_ADMIN_PASSWORD.")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan and exit without writing.")
    ap.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = ap.parse_args()

    nodes_path = Path(args.nodes)
    rels_path = Path(args.relationships)
    for p in (nodes_path, rels_path):
        if not p.is_file():
            raise SystemExit(f"CSV not found: {p}")
    db = args.database
    if db == "system":
        raise SystemExit("Refusing to load into the 'system' database.")

    from neo4j.exceptions import AuthError, Forbidden

    drv, settings = connect(args.user, args.password)
    conn_user, conn_roles, index_props = None, [], []
    try:
        conn_user, conn_roles = current_user_roles(drv)
        privileged = bool(set(conn_roles) & WRITE_ROLES)
        schema = settings.get("schema") or {}
        label = ident(schema.get("node_label", "Concept"), "node_label")
        id_prop = ident(schema.get("id_prop", "cui"), "id_prop")
        rel_type = ident(schema.get("rel_type", "RELATES_TO"), "rel_type")
        rel_prop = ident(schema.get("rel_prop", "rel"), "rel_prop")
        index_props = [ident(p, "index prop") for p in args.index]

        # inspect CSV headers
        with open(nodes_path, encoding="utf-8", newline="") as fh:
            node_headers = next(csv.reader(fh))
        with open(rels_path, encoding="utf-8", newline="") as fh:
            rel_headers = next(csv.reader(fh))
        id_col = find_id_column(node_headers, id_prop)
        node_prop_cols = [h for h in node_headers if base_name(h) is not None]
        if rel_prop not in rel_headers:
            raise SystemExit(f"Relationships CSV has no {rel_prop!r} column. Columns: {rel_headers}")

        n_nodes = count_rows(nodes_path)
        n_rels = count_rows(rels_path)
        edition = server_edition(drv)
        create_db = not args.no_create_db and edition == "enterprise"

        constraint_name = f"{label.lower()}_{id_prop.lower()}"
        rel_verb = "MERGE" if args.merge_rels else "CREATE"

        # ── plan ──
        logger.info("Plan:")
        logger.info("  server        : Neo4j %s (%s)", settings["uri"], edition)
        logger.info("  connecting as : %s  roles=%s%s", conn_user, conn_roles or "?",
                    "" if privileged else "   <-- read-only, cannot create/write")
        logger.info("  target db     : %s%s", db, "  [CREATE" + (" + DROP first" if args.recreate else "") + "]"
                    if create_db else "  [use existing]")
        logger.info("  label / id    : (:%s {%s})   unique constraint '%s'", label, id_prop, constraint_name)
        logger.info("  nodes         : %s from %s  (props: %s)", f"{n_nodes:,}", nodes_path,
                    ", ".join(base_name(h) for h in node_prop_cols))
        logger.info("  relationships : %s from %s  [:%s {%s}]  via %s", f"{n_rels:,}", rels_path,
                    rel_type, rel_prop, rel_verb)
        if args.import_dir:
            logger.info("  load method   : LOAD CSV (copy into %s)", args.import_dir)
            hint = server_import_dir(drv)
            if hint:
                logger.info("  server import : server.directories.import = %s", hint)
        else:
            logger.info("  load method   : streamed over Bolt (batch %s)", f"{args.batch_size:,}")
        logger.info("  extra indexes : %s", ", ".join(index_props) or "(none)")
        if create_db and edition != "enterprise":
            logger.warning("  NOTE: named databases require Enterprise; this server is %s.", edition)
        if not create_db and not args.no_create_db and edition != "enterprise":
            logger.warning("  Community server: not creating a db — database %r must already exist.", db)

        if args.dry_run:
            logger.info("--dry-run: nothing written.")
            return

        if not privileged:
            raise SystemExit(admin_needed_msg(conn_user, conn_roles, db, nodes_path, rels_path, index_props))

        if not args.yes:
            reply = input(f"\nProceed: load {n_nodes:,} nodes + {n_rels:,} rels into '{db}'? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                logger.info("Aborted.")
                return

        t0 = time.time()

        # 1. database
        if create_db:
            create_database(drv, db, args.recreate)
        elif not database_exists(drv, db):
            raise SystemExit(f"Database {db!r} does not exist and --no-create-db / Community — create it first.")

        # 2. id constraint FIRST (fast upserts + fast endpoint lookups)
        logger.info("Creating uniqueness constraint %s on (:%s.%s) …", constraint_name, label, id_prop)
        with drv.session(database=db) as sess:
            sess.run(f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                     f"FOR (n:{label}) REQUIRE n.{id_prop} IS UNIQUE")

        # 3+4. load nodes then relationships — server-side LOAD CSV, or streamed over Bolt
        if args.import_dir:
            load_via_import_dir(drv, db, Path(args.import_dir), nodes_path, rels_path,
                                label, id_prop, id_col, node_prop_cols, rel_type, rel_prop,
                                rel_verb, args.batch_size)
        else:
            logger.info("Loading nodes …")

            def node_tx(row: dict) -> dict | None:
                nid = row.get(id_col, "").strip()
                if not nid:
                    return None
                props = {base_name(h): row[h] for h in node_prop_cols if row.get(h, "") != ""}
                return {"id": nid, "props": props}

            node_cypher = (f"UNWIND $rows AS row "
                           f"MERGE (n:{label} {{{id_prop}: row.id}}) "
                           f"SET n += row.props")
            run_write_batches(drv, db, node_cypher,
                              stream_batches(nodes_path, node_tx, args.batch_size), n_nodes, "nodes")

            logger.info("Loading relationships …")

            def rel_tx(row: dict) -> dict | None:
                s, e = row.get(":START_ID", "").strip(), row.get(":END_ID", "").strip()
                if not s or not e:
                    return None
                return {"s": s, "e": e, "rel": row.get(rel_prop, "")}

            rel_cypher = (f"UNWIND $rows AS row "
                          f"MATCH (a:{label} {{{id_prop}: row.s}}) "
                          f"MATCH (b:{label} {{{id_prop}: row.e}}) "
                          f"{rel_verb} (a)-[x:{rel_type} {{{rel_prop}: row.rel}}]->(b)")
            run_write_batches(drv, db, rel_cypher,
                              stream_batches(rels_path, rel_tx, args.batch_size), n_rels, "relationships")

        # 5. secondary indexes
        for prop in index_props:
            iname = f"{label.lower()}_{prop.lower()}"
            logger.info("Creating index %s on (:%s.%s) …", iname, label, prop)
            with drv.session(database=db) as sess:
                sess.run(f"CREATE INDEX {iname} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})")

        # 6. verify
        with drv.session(database=db) as sess:
            nc = sess.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
            rc = sess.run(f"MATCH (:{label})-[r:{rel_type}]->() RETURN count(r) AS c").single()["c"]
            idx = sess.run("SHOW INDEXES YIELD name, labelsOrTypes, properties, type, owningConstraint "
                           "RETURN name, labelsOrTypes, properties, type, owningConstraint").data()

        dt = time.time() - t0
        print("\n" + "=" * 64)
        print(f"Loaded into database '{db}' in {dt:.1f}s")
        print("=" * 64)
        print(f"  nodes         : {nc:,}")
        print(f"  relationships : {rc:,}")
        print("  indexes:")
        for i in idx:
            kind = "CONSTRAINT" if i.get("owningConstraint") else i.get("type", "")
            print(f"    {i['name']:<28} {i.get('labelsOrTypes')} {i.get('properties')}  [{kind}]")
        print(f"\n  Point HELIX at it:  set  database: {db}  in neo4j.yaml")
        print(f"  Sanity check:       MATCH (n:{label} {{{id_prop}:'C0004096'}}) RETURN n.name, n.mesh  // Asthma")
    except (Forbidden, AuthError) as e:
        raise SystemExit(admin_needed_msg(conn_user, conn_roles, db, nodes_path, rels_path,
                                          index_props, original=str(e)))
    finally:
        drv.close()


if __name__ == "__main__":
    main()
