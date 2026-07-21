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
