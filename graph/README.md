# HELIX — UMLS → Neo4j graph builder

Builds the Neo4j knowledge graph that powers HELIX's graph-neighborhood
disambiguation and document-level negotiation. It converts UMLS `MRREL.RRF`
(relationships) and `MRCONSO.RRF` (concept names) into Neo4j bulk-import CSVs and
loads them, producing:

```
(:Concept {cui, concept, name})  -[:RELATES_TO {rel}]-  (:Concept {...})
```

**Same extraction strategy** as before — one `RELATES_TO` edge per
`(cui2, rel, cui1)`, hyphens in relation names replaced with `_`, self-references
dropped — with one addition: **every node now carries the English concept name**
(`name`), read from MRCONSO (`STR` where `LAT == ENG`), falling back to the CUI
when no English name exists.

> The UMLS RRF files are **not** in this repo (they're large and licensed). Point
> the config at your local copies.

---

## 1. Configure

```bash
cp graph/graph_config.example.yaml graph/graph_config.yaml
```
Edit `graph/graph_config.yaml`:
- `mrrel`, `mrconso` → your local UMLS RRF paths
- `neo4j.home` → your Neo4j install (must contain `bin/neo4j-admin` and `import/`)
- `neo4j.database` → the database name (should match `database:` in `neo4j.yaml`)

Connection credentials are read from the existing `neo4j.yaml`, so you don't
repeat them here.

## 2. Build (automated)

```bash
python graph/build_graph.py
```
This runs the whole pipeline:
1. Generate `umls_nodes.csv` / `umls_relations.csv` (with English names).
2. Copy them into `$NEO4J_HOME/import/`.
3. `neo4j-admin database import full <db>` (offline bulk import).
4. `CREATE DATABASE <db>` + `START DATABASE <db>` via `cypher-shell` (Enterprise).

Useful flags:
- `--csv-only` — just generate the CSVs, do nothing to Neo4j.
- `--skip-db-create` — run the import but leave the `CREATE/START` to you.

> **Neo4j edition note:** a *named* database (`umls`) requires **Neo4j Enterprise**
> (or Aura). Community Edition only serves the default `neo4j` database — set
> `neo4j.database: neo4j` in both configs if you're on Community.

> **If the import fails because the DB is running**, stop it first:
> ```
> cypher-shell -u <user> -p <pw> -d system "STOP DATABASE umls;"
> ```

## 3. Point HELIX at the names

After a rebuild, nodes have a real `name` property. Set it in `neo4j.yaml` so the
graph panel shows English names instead of CUIs:
```yaml
schema:
  name_prop: name      # was 'concept' (which is just the CUID)
```

Verify:
```cypher
:use umls
MATCH (n:Concept) RETURN n.cui, n.name LIMIT 5;
```

---

## Manual path (no automation)

Generate CSVs only, then import yourself:
```bash
python graph/umls_processor.py --config graph/graph_config.yaml
# copy graph/out/*.csv into $NEO4J_HOME/import/, then:
bin/neo4j-admin database import full umls \
  --nodes=import/umls_nodes.csv \
  --relationships=import/umls_relations.csv \
  --overwrite-destination --skip-bad-relationships --verbose
```
`umls_processor.py` also accepts explicit flags (`--mrrel`, `--mrconso`,
`--sample`, `--prefix`, `--language`) instead of `--config`.

## Output columns

**`<prefix>_nodes.csv`** — `concept:ID` (CUI, becomes node id + `concept` prop) ·
`name` (English concept name) · `cui` (duplicate) · `:LABEL` (`Concept`).

**`<prefix>_relations.csv`** — `:START_ID` · `rel` (relation label) · `:END_ID` ·
`:TYPE` (`RELATES_TO`).

## Performance
Streaming, set-deduplicated. MRREL (~88M lines) ≈ 2–5 min; MRCONSO name pass is
restricted to CUIs that appear in relationships to keep memory modest. Use
`sample:` in the config for a quick test build first.

---

## MeSH-only graph  (`build_mesh_graph.py`)

Builds the same CSV format but restricted to the **MeSH** slice of UMLS: nodes are
the CUIs that have an `SAB=MSH` atom (each keeps its MeSH id, e.g. `D012711`), and
an edge is kept only when **both** endpoints are MeSH concepts. Edge labels use
MRREL `RELA` when present, else the coarse `REL` mapped to readable labels
(`broader`/`narrower`/…); `--rela-only` reproduces the RELA-only convention above.

```bash
python graph/build_mesh_graph.py \
  --mrconso /path/UMLS/MRCONSO.RRF --mrrel /path/UMLS/MRREL.RRF   # → graph/out/mesh/ (default)
```

Output in **`graph/out/mesh/`**: `mesh_nodes.csv` (`concept:ID, name, mesh, cui, :LABEL`) +
`mesh_relations.csv` + `mesh_graph_summary.json`.

> **Output layout** — each graph lives in its own folder so they never clash:
> the full UMLS graph in `graph/out/umls/` (`umls_processor.py`, default) and the MeSH
> graph in `graph/out/mesh/` (`build_mesh_graph.py`, default).

## Loading CSVs into a running server  (`load_neo4j.py`)

The offline `neo4j-admin` path above needs `$NEO4J_HOME` and a stopped database.
When you have a **running** Neo4j instead (e.g. Neo4j Desktop), `load_neo4j.py`
loads any of these CSV pairs over Bolt — reusing the connection in `neo4j.yaml`
(via `eltool.graph_client`) — and creates the id index for you:

1. (Enterprise) `CREATE DATABASE <db>` and wait until online (`--recreate` to DROP first);
2. a **uniqueness constraint** on the id property (`:Concept(cui)`), created *before* load
   so node upserts and relationship endpoint lookups are indexed;
3. loads nodes + relationships, either **server-side `LOAD CSV`** (pass `--import-dir`, faster:
   the CSVs are copied there and read by the server) or **streamed over Bolt** (default, needs
   no filesystem access);
4. any secondary indexes you ask for (`--index mesh --index name`);
5. verification (node/rel counts + indexes).

```bash
# MeSH CSVs → a NEW 'mesh' database (leaves 'umls' untouched)
python graph/load_neo4j.py --database mesh --index mesh --index name \
  --user neo4j --password <ADMIN_PASSWORD>          # defaults to graph/out/mesh/mesh_*.csv

# faster: copy into the server's import dir and load with LOAD CSV
python graph/load_neo4j.py --database mesh --index mesh --index name \
  --import-dir "/path/to/dbms/import" --user neo4j --password <ADMIN_PASSWORD>

# RE-IMPORT the full UMLS graph → the 'umls' database (--recreate DROPs + rebuilds it).
# The uniqueness constraint/index on cui is created automatically.
python graph/load_neo4j.py --database umls --recreate \
  --nodes graph/out/umls/umls_nodes.csv --relationships graph/out/umls/umls_relations.csv \
  --user neo4j --password <ADMIN_PASSWORD>
```

> Find the server's import directory from `--dry-run` (it prints
> `server.directories.import` when your account can read settings), or in Neo4j Desktop:
> the DBMS's **Open folder → Import**.

> **Credentials:** the account in `neo4j.yaml` is typically **read-only** (it exists
> for HELIX's graph *queries*). Creating a database and writing data needs an **admin**
> account — pass `--user/--password` (or set `NEO4J_ADMIN_USER`/`NEO4J_ADMIN_PASSWORD`).
> The script detects a read-only account up front and prints the exact command to re-run.
> Use `--dry-run` to preview the plan without writing.
