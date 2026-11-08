# Hosted HELIX with bring-your-own KB and Neo4j — design

Date: 2026-11-08 · Owner: Lakshan Karunathilake

Implementation plan: [`docs/superpowers/plans/2026-11-08-hosted-byo-connections.md`](../plans/2026-11-08-hosted-byo-connections.md)

## 1. Goal

Host only HELIX's own logic — the **web app** and the **API** — on a small server. Each
session creator brings their own **KB search server** (index retrieval) and, optionally,
their own **Neo4j** graph, and points their sessions at them.

Why: the full UMLS KB needs ~24 GB of RAM per index, and UMLS content and Neo4j Enterprise
both carry licences. With bring-your-own, the hosted part fits on a 2 vCPU / 4 GB machine,
and the licensed data stays with people who already hold the licences.

## 2. What exists today

* **KBs are already configured per session.** `KBConfig` (`eltool/config.py`) stores
  `endpoint_url`, `headers`, `request_template`, `response_path` and field mappings;
  `KBClient` (`eltool/kb_client.py`) calls any HTTP endpoint with a 15 s timeout.
* **Neo4j is one server-wide config.** `load_neo4j_settings()` (`eltool/graph_client.py`)
  reads `neo4j.yaml`, and `_get_graph_client()` (`api/main.py`) uses it for every session.
* **Only `llm_api_key` is write-only.** `GET /api/sessions/{name}` returns each KB's
  `headers` unmasked, so a creator's KB token is visible to anyone who opens the session.
* **Manual id lookup drops auth.** `lookup_entity` and the debug-gold path call
  `requests.get(_entity_url(...))` without the KB's headers.
* **The server fetches any URL a session names**, including `/api/kb/test`, and reads any
  server directory named in `context_folder`.
* **Sessions hold Mac-only paths.** `input_path` and `context_folder` are absolute
  `/Users/...` paths; KB endpoints are `http://127.0.0.1:<port>/query`.
* No accounts, `allow_origins=["*"]`, and the KB server runs Flask's development server.

## 3. Decisions

### 3.1 Two modes, one switch

`HELIX_HOSTED=1` turns on hosted mode. **When it is unset, behaviour is exactly as today** —
`./run.sh`, existing sessions and every existing test keep working.

| Behaviour | Local (default) | Hosted (`HELIX_HOSTED=1`) |
|---|---|---|
| Outbound URLs to private / loopback / link-local / reserved / multicast addresses | allowed | rejected, unless `host:port` is in `HELIX_URL_ALLOWLIST` |
| KB URL scheme | `http` or `https` | `https` only (allow-listed hosts may use `http`) |
| Neo4j URI scheme | any the driver accepts | encrypted only: `neo4j+s`, `bolt+s`, or the self-signed `neo4j+ssc` / `bolt+ssc` (allow-listed hosts exempt) |
| Server-wide `neo4j.yaml` | fallback when a session has no graph connection | never read |
| `context_folder` | any server directory | must be inside the session's own directory |
| CORS origins | `HELIX_CORS_ORIGINS`, default `*` | `HELIX_CORS_ORIGINS`, default none (same-origin) |

`HELIX_URL_ALLOWLIST` is a comma-separated list of `host:port` entries, e.g.
`kb:8765,demo-kb.internal:8765`. It exists for an optional demo KB on the server's own
Docker network.

Redirects are never followed on outbound KB calls.

### 3.2 Per-session graph connection

`SessionConfig` gains `graph: GraphConnection | None`, holding `uri`, `user`, `password`,
`database` and the schema mapping (`node_label`, `id_prop`, `rel_type`, `rel_prop`,
`name_prop`, `rel_include`, `rel_exclude`) with today's `GraphSchema` defaults.

Resolution order:
1. `session.graph`, if set.
2. Otherwise in local mode, the server `neo4j.yaml` (today's behaviour).
3. Otherwise a `GraphError`: "This session has no Neo4j connection configured."

Graph clients are pooled per connection (at most 32; the least recently used is closed).
`POST /api/graph/test` checks a connection before it is saved.

### 3.3 Write-only secrets

`llm_api_key`, every KB's `headers` and `graph.password` are **write-only**:
* `GET` returns them as `""`, plus `llm_api_key_set`, per-KB `headers_set` and
  `graph.password_set`.
* `PUT` with a blank value keeps the stored one — but only while it would go to the same
  place: a KB's headers are kept only if a stored KB has the same `name` **and**
  `endpoint_url`; the graph password only if `uri` and `user` are unchanged. Otherwise the
  secret is dropped, so editing the address cannot send a stored secret to a new host.
* `/api/kb/test` and `/api/graph/test` accept an optional `session_name`, so a test of a
  saved KB or graph can use its stored secret.

### 3.4 Context texts are uploaded

In hosted mode, creators upload a `.zip` of `<doc_id>.txt` files instead of naming a server
folder. It is extracted flat (by basename) into `sessions/<name>/context/`, which becomes
`context_folder`. Limits: 50 MB upload, 500 MB uncompressed, 100,000 members; members that
are not `.txt` are skipped; any member whose path escapes the target is rejected.

### 3.5 The KB contract and the KB kit

`docs/kb-contract.md` documents what HELIX calls on a KB: `POST /query`, `POST /query/dense`,
`POST /query/lexical`, `GET /entity/{id}`, `GET /health`, and the response fields.

`kb/server.py` gains optional bearer auth: when `KB_AUTH_TOKEN` is set, every route except
`/health` needs `Authorization: Bearer <token>` and returns 401 otherwise. It is served by
**waitress** (one process, 8 threads) so the index is loaded once.

`deploy/kb-kit/` gives creators a compose file for the KB server (plus optional Neo4j
Community and a Cloudflare Tunnel) with a README.

### 3.6 Hosted deployment

`deploy/hosted/` runs `web`, `api` and **Caddy** (automatic TLS + basic-auth gate). Only
80/443 are published. The API runs one uvicorn worker (saves are serialised by an
in-process lock). `sessions/` is a named volume, backed up nightly with 14 days kept.

The basic-auth gate is the **interim** access control: everyone past it is a trusted
collaborator until accounts land (§5).

### 3.7 Migrating existing sessions

`scripts/migrate_sessions_for_hosting.py` rewrites a copy of `sessions/` for the server:
`input_path` to the new sessions root, KB endpoints through a `--kb-map old=new` table, and
copies each `context_folder`'s `.txt` files into `sessions/<name>/context/`.

## 4. Accepted risks

* **DNS rebinding.** The URL guard resolves the host and checks the address before the
  request, but `requests` resolves again when it connects. Pinning the resolved IP is
  deferred; the basic-auth gate limits who can try.
* **Remote KB latency.** Auto-annotate makes one call per mention per KB; over a tunnel this
  is slow. No batching in this plan.

## 5. Out of scope (next plans)

* User accounts, creator vs annotator roles and session ownership.
* A batch query endpoint on the KB contract.
* Hosting a public demo KB.
