#!/usr/bin/env python3
"""
End-to-end HELIX graph build: UMLS RRF  →  CSVs  →  Neo4j database.

Reads graph/graph_config.yaml for the dataset paths + Neo4j home/database, and
reuses the connection credentials already in neo4j.yaml (via eltool.graph_client)
for the optional CREATE/START step — so credentials live in exactly one place.

Steps:
  1. Generate <prefix>_nodes.csv / <prefix>_relations.csv (with English names).
  2. Copy them into $NEO4J_HOME/import/.
  3. Run `neo4j-admin database import full <db>` (offline bulk import).
  4. (best-effort) CREATE DATABASE <db> IF NOT EXISTS + START DATABASE, via cypher-shell.

Usage:
  python graph/build_graph.py                 # full pipeline
  python graph/build_graph.py --csv-only      # stop after step 1
  python graph/build_graph.py --skip-db-create   # do import but not CREATE/START
  python graph/build_graph.py --config graph/graph_config.yaml
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from graph.umls_processor import load_build_config, run_from_config  # noqa: E402


def _find_config(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for c in [ROOT / "graph" / "graph_config.yaml", ROOT / "graph" / "graph_config.yml"]:
        if c.is_file():
            return c
    raise SystemExit("No graph/graph_config.yaml found. Copy graph/graph_config.example.yaml first.")


def _neo4j_conn():
    """Connection creds from neo4j.yaml (single source), for the CREATE/START step."""
    try:
        from eltool.graph_client import load_neo4j_settings
        s = load_neo4j_settings()
        return s["uri"], s["user"], s["password"], s["database"]
    except Exception as e:  # noqa: BLE001
        print(f"  (could not read neo4j.yaml connection: {e})")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--csv-only", action="store_true", help="Generate CSVs and stop")
    ap.add_argument("--skip-db-create", action="store_true", help="Import but don't CREATE/START the database")
    args = ap.parse_args()

    cfg = load_build_config(_find_config(args.config))
    prefix = cfg.get("prefix", "umls")
    neo = cfg.get("neo4j") or {}
    db = neo.get("database", prefix)

    # 1. generate CSVs
    print("── Step 1/4: generating CSVs from UMLS …")
    nodes_file, rels_file = run_from_config(cfg)
    print(f"   nodes: {nodes_file}\n   rels : {rels_file}")
    if args.csv_only:
        print("Done (--csv-only).")
        return

    home = neo.get("home")
    if not home:
        raise SystemExit("neo4j.home is not set in the config — needed for import. Use --csv-only to skip.")
    home = Path(home)
    admin = home / "bin" / "neo4j-admin"
    import_dir = home / "import"
    if not admin.exists():
        raise SystemExit(f"neo4j-admin not found at {admin}. Check neo4j.home.")
    import_dir.mkdir(parents=True, exist_ok=True)

    # 2. copy CSVs into the Neo4j import dir
    print(f"── Step 2/4: copying CSVs into {import_dir} …")
    shutil.copy(nodes_file, import_dir / nodes_file.name)
    shutil.copy(rels_file, import_dir / rels_file.name)

    # 3. bulk import
    print(f"── Step 3/4: neo4j-admin database import full {db} …")
    cmd = [
        str(admin), "database", "import", "full", db,
        f"--nodes={import_dir / nodes_file.name}",
        f"--relationships={import_dir / rels_file.name}",
        "--overwrite-destination", "--skip-bad-relationships", "--verbose",
    ]
    print("   " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        raise SystemExit(
            f"neo4j-admin import exited {rc}. If the DB is running, stop it first:\n"
            f"    cypher-shell -a <uri> -u <user> -p <pw> -d system \"STOP DATABASE {db};\""
        )

    if args.skip_db_create:
        print("Import done (--skip-db-create). Remember to CREATE/START the database.")
        return

    # 4. CREATE + START the database (Enterprise; Community only supports the default db)
    print(f"── Step 4/4: CREATE/START DATABASE {db} …")
    conn = _neo4j_conn()
    cypher_shell = home / "bin" / "cypher-shell"
    if conn and cypher_shell.exists():
        uri, user, pw, _ = conn
        for stmt in (f"CREATE DATABASE {db} IF NOT EXISTS;", f"START DATABASE {db};"):
            r = subprocess.run([str(cypher_shell), "-a", uri, "-u", user, "-p", pw, "-d", "system", stmt])
            if r.returncode != 0:
                print(f"   (cypher-shell failed on: {stmt} — run it manually)")
    else:
        print("   cypher-shell or neo4j.yaml creds unavailable — run manually in the system database:")
        print(f"       CREATE DATABASE {db} IF NOT EXISTS;")
        print(f"       START DATABASE {db};")

    print("\n✅ Done. Verify in Neo4j Browser:")
    print(f"   :use {db}")
    print("   MATCH (n:Concept) RETURN n.cui, n.name LIMIT 5;")
    print("\nThen set  name_prop: name  in neo4j.yaml so HELIX shows English names.")


if __name__ == "__main__":
    main()
