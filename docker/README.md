# HELIX in Docker

Ships all four components as containers:

| Service | Image | Port | Notes |
|---|---|---|---|
| `kb`    | `Dockerfile.kb`  | 8765 | SAPBERT FAISS + lexical; heavy (torch/faiss). Index + adapter **mounted**, not baked. |
| `api`   | `Dockerfile.api` | 8000 | FastAPI; LLM re-ranker; talks to `kb` + `neo4j`. |
| `web`   | `Dockerfile.web` | 5173 | Vite build served by nginx, proxies `/api` → `api`. |
| `neo4j` | `neo4j:5-enterprise` | 7474/7687 | Optional; only for the graph feature. |

## Quick start

```bash
cp .env.example .env                         # fill in secrets + host paths
cp neo4j.docker.example.yaml neo4j.docker.yaml
docker compose up --build                    # first build is slow (torch/faiss)
# open http://localhost:5173
```

The FAISS index (~GBs) and adapter are **mounted from the host** via `.env`
(`INDEX_HOST_DIR`, `INDEX_PREFIX`, `ADAPTER_HOST_DIR`) — nothing large is copied
into an image. The KB server takes ~1–3 min to load the model + index on start;
`docker compose logs -f kb` shows progress, and its healthcheck stays "starting"
until `/health` responds.

## Three caveats that will bite you if skipped

1. **KB endpoint URL inside Docker.** Sessions store the KB endpoint (e.g.
   `http://127.0.0.1:8765/query`). Inside containers `127.0.0.1` is the *api*
   container, not `kb`. When you add a KB in the Configure page, set its endpoint
   to **`http://kb:8765/query`**. Existing sessions created outside Docker must be
   updated to that URL.

2. **Neo4j URI.** `neo4j.docker.yaml`'s `uri` must be reachable from the api
   container: **`bolt://neo4j:7687`** when using the bundled `neo4j` service, or
   **`bolt://host.docker.internal:7687`** when Neo4j runs on your host. `localhost`
   will not work.

3. **CPU-only inference.** Containers on macOS have no MPS/GPU access, so the KB
   server runs SAPBERT on CPU — correct, just slower than your native `mps` run.

## Building the graph (optional)

Generate the CSVs on the host (see `graph/README.md`), then import into the
`neo4j` container — the compose file mounts `./graph/out` to `/import`:

```bash
python graph/build_graph.py --csv-only
docker compose exec neo4j neo4j-admin database import full umls \
  --nodes=/import/umls_nodes.csv \
  --relationships=/import/umls_relations.csv \
  --overwrite-destination --skip-bad-relationships
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" -d system \
  "CREATE DATABASE umls IF NOT EXISTS; START DATABASE umls;"
```

## Common commands

```bash
docker compose up --build          # start everything
docker compose logs -f kb          # watch the KB server load the index
docker compose down                # stop
docker compose build api           # rebuild one service after code changes
```

## Running only some services

- **No graph:** remove/ignore the `neo4j` service and leave `enable_graph` off.
- **Backend only (use your native web dev server):** `docker compose up kb api`,
  then run `cd web && npm run dev` on the host (its Vite proxy targets
  `localhost:8000`, which the `api` container exposes).

## Apple Silicon note

`torch` and `faiss-cpu` install linux/arm64 wheels in the build. If `faiss-cpu`
fails to install for arm64, add `platform: linux/amd64` to the `kb` service
(runs under emulation — slower) or build the index image on an x86 host.
