# Hosted HELIX with bring-your-own KB and Neo4j — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host only the web app and API, with each session pointing at the creator's own KB server and Neo4j, safely.

**Architecture:** One switch, `HELIX_HOSTED=1`, turns on hosted behaviour (outbound URL guard, encrypted-only Neo4j, no server-wide `neo4j.yaml`, uploads instead of server folders). Neo4j moves from one server config into each session, pooled per connection. Secrets become write-only. A KB kit and a Caddy-fronted compose bundle cover both sides of the deployment.

**Tech Stack:** Python 3.11, FastAPI, pydantic v2, requests, neo4j driver 5, Flask + waitress (KB), React/TypeScript/Vite, Docker Compose, Caddy 2.

**Spec:** [`docs/superpowers/specs/2026-11-08-hosted-byo-connections-design.md`](../specs/2026-11-08-hosted-byo-connections-design.md)

## Global Constraints

- With `HELIX_HOSTED` unset, behaviour is unchanged: `./run.sh`, existing `sessions/*/session.json` (no `graph` field, `http://127.0.0.1:<port>` KBs) and the existing 70 tests all keep working.
- Env vars, exactly: `HELIX_HOSTED` (`"1"` = hosted), `HELIX_URL_ALLOWLIST` (comma-separated `host:port`), `HELIX_CORS_ORIGINS` (comma-separated origins), `KB_AUTH_TOKEN`, `KB_THREADS` (default `8`).
- Hosted Neo4j schemes: `neo4j+s`, `bolt+s`, `neo4j+ssc`, `bolt+ssc`. Hosted KB scheme: `https` (allow-listed hosts may use `http`).
- Context upload limits: 50 MB upload, 500 MB uncompressed, 100,000 members.
- Graph client pool: at most 32 clients, least recently used closed first.
- Only new runtime dependency: `waitress>=3.0` (KB side).
- Tests: `python -m pytest tests -q` using the project's conda Python (`/Users/lakshankarunathilake/miniconda3/bin/python`). Web check: `npm run build --prefix web` (runs `tsc`).
- Commit messages: Conventional Commits (`feat(hosting): …`), ending with the `Co-Authored-By` trailer the session specifies.

## Review Focus

1. **A stored secret sent to a new host.** Someone edits a session's KB `endpoint_url` or graph `uri` and leaves the secret blank: the stored token/password must **not** be reused for the new address. → Task 4, `test_blank_secret_not_reused_when_endpoint_changes`.
2. **A KB that redirects to an internal address** (302 → `http://169.254.169.254/`) must not be followed. → Task 2, `test_redirects_are_not_followed`.
3. **A hostile context zip** (`../evil.txt`, a zip bomb) must be rejected before anything is written outside the session. → Task 5, `test_traversal_member_rejects_upload`, `test_uncompressed_limit`.
4. **Uploads through nginx.** nginx's default 1 MB body limit makes input-file and zip uploads fail with 413 in Docker; they must work up to 50 MB. → Task 5 sets `client_max_body_size`, Task 8 smoke test uploads a 5 MB file.
5. **Old sessions in local mode.** A pre-existing `session.json` with no `graph` and a `127.0.0.1` KB must load, query and use the server `neo4j.yaml` as before. → Task 3, `test_legacy_session_uses_server_neo4j_yaml_locally`.

---

### Task 1: Hosting switch, outbound URL guard, CORS

**Files:**
- Create: `eltool/hosting.py`
- Modify: `api/main.py:55-61` (CORS)
- Test: `tests/test_hosting.py`

**Interfaces:**
- Produces:
  - `hosted_mode() -> bool`
  - `url_allowlist() -> set[str]` — lower-cased `host:port`
  - `cors_origins() -> list[str]` — `HELIX_CORS_ORIGINS` split on commas; unset → `["*"]` locally, `[]` hosted
  - `class UnsafeURLError(ValueError)`
  - `check_outbound_url(url: str) -> None` — http(s) KB URLs
  - `check_neo4j_uri(uri: str) -> None`

Rules for both checks:
- Bad scheme raises in either mode.
- Locally, nothing else is checked (no DNS lookups).
- Hosted:
  - an allow-listed `host:port` passes; default ports are 443 / 80 / 7687
  - otherwise the host must resolve (`socket.getaddrinfo`), and **every** address must be public: reject when `ipaddress` reports private, loopback, link-local, reserved, multicast or unspecified
  - the hosted scheme rules from Global Constraints apply

- [ ] **Step 1: Write the failing tests** in `tests/test_hosting.py`. Monkeypatch env with `monkeypatch.setenv`, and DNS with `monkeypatch.setattr("eltool.hosting.socket.getaddrinfo", fake)`, where `fake` returns the addresses each case names.
  - `test_local_allows_loopback_http`: `check_outbound_url("http://127.0.0.1:8765/query")` does not raise.
  - `test_unknown_scheme_rejected_in_both_modes`: `ftp://x/query` raises `UnsafeURLError`, both locally and hosted.
  - `test_hosted_rejects_private_and_metadata`: hosted, `https://127.0.0.1/query`, `https://169.254.169.254/` and `https://[::1]/q` each raise.
  - `test_hosted_rejects_if_any_address_private`: host resolves to `["93.184.216.34", "10.0.0.5"]` → raises.
  - `test_hosted_accepts_public_https`: resolves to `93.184.216.34` → no raise.
  - `test_hosted_requires_https`: `http://kb.example.org/query` with a public address → raises.
  - `test_allowlisted_internal_host_passes`: `HELIX_URL_ALLOWLIST="kb:8765"`, `http://kb:8765/query` resolving to `172.18.0.2` → no raise.
  - `test_unresolvable_host_rejected`: `fake` raises `socket.gaierror` → `UnsafeURLError`.
  - `test_neo4j_schemes`: hosted, `neo4j+s://a.databases.neo4j.io` passes (public address); `bolt://a.databases.neo4j.io` raises; `bolt+ssc://1.2.3.4:7687` passes; `neo4j+s://10.0.0.5` raises.
  - `test_cors_origins`: unset+local → `["*"]`; unset+hosted → `[]`; `"https://a.org, https://b.org"` → `["https://a.org", "https://b.org"]`.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_hosting.py -q`. Expected: `ModuleNotFoundError: eltool.hosting`.
- [ ] **Step 3: Implement `eltool/hosting.py`** with the interfaces above. Read the env vars on every call, not at import, so tests can change them.
- [ ] **Step 4: Wire CORS.** In `api/main.py`, replace `allow_origins=["*"]` with `allow_origins=cors_origins()`.
- [ ] **Step 5: Run the tests.** Run `python -m pytest tests -q`. Expected: all pass (70 existing + 10 new).
- [ ] **Step 6: Commit** — `feat(hosting): hosted-mode switch, outbound URL guard and CORS origins`.

### Task 2: Guarded KB calls; entity lookup sends the KB's headers

**Files:**
- Modify: `eltool/kb_client.py`
- Modify: `api/main.py` — `_entity_url` (~566), `lookup_entity` (~606-646), the debug-gold block (~334-345), `test_kb` (~1066)
- Test: `tests/test_kb_client_guard.py`; `tests/test_entity_lookup.py` keeps passing unchanged

**Interfaces:**
- Consumes: `check_outbound_url`, `UnsafeURLError` (Task 1).
- Produces:
  - `entity_url(base: str, entity_id: str) -> str` — moved from `api/main._entity_url`, same behaviour. Keep `_entity_url = entity_url` in `api/main.py` so the existing test import works.
  - `fetch_entity(cfg: KBConfig, entity_id: str, timeout: float = 10.0) -> dict | None` — sends `cfg` headers, runs the guard, doesn't follow redirects. Returns `None` on 404; raises `requests.HTTPError` on other errors.
  - `KBClient.query` now runs `check_outbound_url` first and passes `allow_redirects=False`. A 3xx response raises `ValueError("KB redirected to <location>; redirects are not followed")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_kb_client_guard.py`. Monkeypatch `eltool.kb_client.requests.post` and `.get` with fakes that record their kwargs and return a stub response.
  - `test_query_disables_redirects`: kwargs include `allow_redirects=False`.
  - `test_redirects_are_not_followed`: the stub returns 302 with `Location: http://169.254.169.254/` → `ValueError` matching `"redirects are not followed"`.
  - `test_hosted_private_kb_blocked_before_request`: hosted, endpoint `https://127.0.0.1/query` → `UnsafeURLError`, and the fake was never called.
  - `test_fetch_entity_sends_headers`: headers `'{"Authorization": "Bearer t"}'` → the recorded headers equal `{"Authorization": "Bearer t"}`, and the URL is `…/entity/C1`.
  - `test_fetch_entity_404_is_none`: returns `None`.
  - `test_lookup_entity_route_uses_kb_headers`: via `TestClient`, using a session fixture like `tests/test_api_provenance.py` with KB headers set. `GET /api/sessions/s/kb/UMLS/entity/C1` → `found: true`, and the fake saw the header.
  - `test_lookup_entity_unsafe_url_is_400`: hosted with a loopback KB → status 400.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_kb_client_guard.py -q`. Expected: FAIL (`fetch_entity` is missing; no `allow_redirects` kwarg).
- [ ] **Step 3: Implement the Interfaces in `eltool/kb_client.py`.** Then switch `lookup_entity` and the debug-gold `_kb_entity` to `fetch_entity`. Map errors as follows:
  - `UnsafeURLError` → HTTP 400
  - `requests.RequestException` → 502, as today
  - in `test_kb`, an `UnsafeURLError` is returned as `{"ok": false, "error": …}`, like other errors there
- [ ] **Step 4: Run the tests.** Run `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit** — `feat(hosting): guard KB requests, no redirects, entity lookup sends KB headers`.

### Task 3: Per-session Neo4j connection

**Files:**
- Modify: `eltool/config.py` — add `GraphConnection` and `SessionConfig.graph`
- Modify: `eltool/graph_client.py` — `from_connection`, `ping`, the guard in `_driver_lazy`, `connection_timeout=10`
- Create: `eltool/graph_pool.py`
- Modify: `api/main.py` — replace `_GRAPH_CLIENTS` / `_get_graph_client` (~807-825); the four graph routes (~838, 856, 916, 957); `CreateSessionRequest` + `create_session`; add `POST /api/graph/test`
- Modify: `web/src/types/index.ts`, `web/src/api/client.ts`, `web/src/pages/Configure.tsx` (graph section ~458-476)
- Test: `tests/test_graph_connection.py`

**Interfaces:**
- Consumes: `check_neo4j_uri`, `hosted_mode` (Task 1).
- Produces:
  - `class GraphConnection(BaseModel)`:
    - fields: `uri: str`, `user: str = "neo4j"`, `password: str = ""`, `database: str | None = None`, `node_label = "Concept"`, `id_prop = "cui"`, `rel_type = "RELATES_TO"`, `rel_prop = "rel"`, `name_prop = "concept"`, `rel_include: list[str] = []`, `rel_exclude: list[str] = []`
    - validator: the `uri` scheme is one of `bolt`, `bolt+s`, `bolt+ssc`, `neo4j`, `neo4j+s`, `neo4j+ssc`
  - `SessionConfig.graph: GraphConnection | None = None`
  - `Neo4jGraphClient.from_connection(conn: GraphConnection) -> Neo4jGraphClient` — must **not** call `load_neo4j_settings()`
  - `Neo4jGraphClient.ping() -> int` — `MATCH (n:<node_label>) RETURN count(n) AS c`
  - `_driver_lazy` calls `check_neo4j_uri(self.uri)` first and re-raises `UnsafeURLError` as `GraphError`
  - `connection_from_settings(settings: dict) -> GraphConnection` — maps the `load_neo4j_settings()` dict, including its `schema` block
  - `class GraphClientPool(max_size: int = 32)` with `.get(conn: GraphConnection) -> Neo4jGraphClient` — key `conn.model_dump_json()`, LRU, `.close()` on eviction
  - `resolve_graph_client(cfg: SessionConfig, pool: GraphClientPool) -> Neo4jGraphClient` — the order in spec §3.2; hosted with no graph raises `GraphError("This session has no Neo4j connection configured.")`
  - API `POST /api/graph/test`:
    - body `{"graph": {...}, "session_name": str | None}`
    - returns `{"ok": true, "nodes": int}` or `{"ok": false, "error": str}`
    - `session_name` is accepted but unused until Task 4
  - TS: `GraphConnection` type; `testGraph(graph, sessionName?)` in `client.ts`

- [ ] **Step 1: Write the failing tests** in `tests/test_graph_connection.py`:
  - `test_from_connection_ignores_neo4j_yaml`: monkeypatch `eltool.graph_client.load_neo4j_settings` to raise → `from_connection(GraphConnection(uri="neo4j+s://x"))` succeeds, and `.schema.node_label == "Concept"`.
  - `test_bad_scheme_rejected`: `GraphConnection(uri="http://x")` → `ValidationError`.
  - `test_pool_reuses_and_evicts`: with `GraphClientPool(max_size=2)`, the same connection twice gives the identical object. A third distinct connection evicts the first; spy on `Neo4jGraphClient.close` to confirm it was called once.
  - `test_session_graph_wins`: `resolve_graph_client` returns a client whose `.uri` is the session's.
  - `test_legacy_session_uses_server_neo4j_yaml_locally`: load a legacy `session.json` dict (no `graph`, KB `http://127.0.0.1:8765/query`) through `SessionConfig.model_validate`. With `load_neo4j_settings` stubbed to return `uri="bolt://localhost:7687"`, the resolved client's `.uri` is that.
  - `test_hosted_without_graph_errors`: hosted + `graph=None` → `GraphError` matching `"no Neo4j connection"`.
  - `test_hosted_plain_bolt_refused_on_connect`: hosted, `from_connection(GraphConnection(uri="bolt://10.0.0.5:7687"))._driver_lazy()` → `GraphError`, and `neo4j.GraphDatabase.driver` is never called (monkeypatch it to fail the test).
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_graph_connection.py -q`. Expected: FAIL on import (`GraphConnection` is missing).
- [ ] **Step 3: Implement** `GraphConnection`, `SessionConfig.graph`, `from_connection`, `ping`, the guard, `connection_timeout=10`, and `eltool/graph_pool.py`.
- [ ] **Step 4: Switch the API over.** Replace `_get_graph_client()` with `resolve_graph_client(cfg, _GRAPH_POOL)` in all four graph routes. Add `graph: dict | None = None` to `CreateSessionRequest` and pass it into `SessionConfig`. Add `POST /api/graph/test`.
- [ ] **Step 5: Update the web graph section.** Add these fields:
  - URI, user, password and database
  - a collapsed "Schema" group with the seven schema fields, pre-filled with their defaults
  - a "Test connection" button that shows `nodes` or the error

  Replace the `neo4j.yaml` help text with: "Leave URI empty to use the server's neo4j.yaml (local installs only)." An empty URI sends `graph: null`.
- [ ] **Step 6: Run the checks.** Run `python -m pytest tests -q` and `npm run build --prefix web`. Expected: all pass; the build exits 0.
- [ ] **Step 7: Commit** — `feat(graph): per-session Neo4j connection with pooled clients and test endpoint`.

### Task 4: Write-only secrets

**Files:**
- Create: `eltool/secrets.py`
- Modify: `api/main.py` — `get_session` (~182), `update_session` (~192), `TestKBRequest` / `test_kb` (~1061), `/api/graph/test`
- Modify: `web/src/types/index.ts`, `web/src/pages/Configure.tsx` (KB headers input ~827, graph password)
- Test: `tests/test_secrets.py`

**Interfaces:**
- Consumes: `GraphConnection` (Task 3), `KBClient` (Task 2).
- Produces:
  - `mask_session(cfg: SessionConfig) -> dict` — `model_dump()` with:
    - `llm_api_key=""` plus `llm_api_key_set`
    - each KB `headers=""` plus `headers_set` (true unless the stored value is `""` or `"{}"`)
    - `graph.password=""` plus `graph.password_set`
  - `fill_kb_secret(kb: dict, existing: SessionConfig | None) -> dict` — a blank `headers` takes the stored KB's headers **only if** a stored KB has the same `name` **and** the same `endpoint_url`. Otherwise it becomes `"{}"`. Pops `headers_set`.
  - `fill_graph_secret(graph: dict | None, existing: SessionConfig | None) -> dict | None` — a blank `password` takes the stored one **only if** `uri` and `user` are unchanged. Pops `password_set`.
  - `merge_secrets(body: dict, existing: SessionConfig) -> dict` — pops `llm_api_key_set`; keeps today's rule for a blank `llm_api_key`; applies `fill_kb_secret` to every KB and `fill_graph_secret` to `graph`.
  - `TestKBRequest.session_name: str | None = None`; when set, `test_kb` runs `fill_kb_secret` against that session. `/api/graph/test` does the same with `fill_graph_secret`.
  - TS: `KBConfig.headers_set?: boolean`; `GraphConnection.password_set?: boolean`.

- [ ] **Step 1: Write the failing tests** in `tests/test_secrets.py`. Use a `TestClient` fixture like `tests/test_api_provenance.py`, with a session whose KB headers are `'{"Authorization": "Bearer t"}'` and whose graph password is `"pw"`.
  - `test_get_masks_all_secrets`: response KB `headers == ""` and `headers_set is True`; `graph.password == ""` and `password_set is True`; `llm_api_key == ""`.
  - `test_put_blank_keeps_secrets`: PUT the masked GET body back → `session.json` on disk still holds `Bearer t` and `pw`.
  - `test_blank_secret_not_reused_when_endpoint_changes`: PUT with KB `endpoint_url` changed and blank headers → stored headers become `"{}"`. Likewise, PUT with graph `uri` changed and a blank password → stored password `""`.
  - `test_kb_test_uses_stored_headers`: `POST /api/kb/test` with `session_name` and blank headers → the fake `requests.post` sees `Authorization: Bearer t`.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_secrets.py -q`. Expected: FAIL (`headers` is returned unmasked).
- [ ] **Step 3: Implement `eltool/secrets.py` and wire the API.** `get_session` returns `mask_session(cfg)`, and `update_session` calls `merge_secrets(body, existing)` before `SessionConfig(**body)`. This replaces the inline `llm_api_key` handling.
- [ ] **Step 4: Update the web Configure page.** When `headers_set` / `password_set` is true, show the placeholder "stored — leave blank to keep". Send `session_name` with test calls on an existing session.
- [ ] **Step 5: Run the checks.** Run `python -m pytest tests -q` and `npm run build --prefix web`. Expected: all pass.
- [ ] **Step 6: Commit** — `feat(hosting): write-only KB headers and Neo4j password`.

### Task 5: Context text upload; hosted folder restriction; upload size

**Files:**
- Create: `eltool/context_upload.py`
- Modify: `api/main.py` — `create_session` (~140) and `update_session`, plus the new route
- Modify: `docker/nginx.conf` — add `client_max_body_size 60m;` to the `server` block
- Modify: `web/src/api/client.ts`, `web/src/pages/Configure.tsx` (context section)
- Test: `tests/test_context_upload.py`

**Interfaces:**
- Consumes: `hosted_mode` (Task 1).
- Produces:
  - `class ContextUploadError(ValueError)`
  - `extract_context_zip(zip_path: Path, dest: Path, *, max_total_bytes: int = 500_000_000, max_members: int = 100_000) -> int`
    - validate every member before writing anything
    - any member whose normalised path is absolute or contains `..` → raise
    - a sum of `file_size` over `max_total_bytes` → raise; more than `max_members` → raise
    - write only `*.txt`, flat, by basename; return the count written
  - API `POST /api/sessions/{name}/context`:
    - multipart `file`; more than 50 MB → 413
    - extracts into `session_dir(name) / "context"`, sets `cfg.context_folder` to it, saves
    - returns `{"files": int}`
  - `_check_context_folder(folder: str | None, name: str) -> None` — hosted and `folder` not inside `session_dir(name).resolve()` → `HTTPException(400, "In hosted mode, upload context texts instead of naming a server folder.")`. Called in `create_session` and `update_session`.
  - TS: `uploadContext(sessionName: string, file: File): Promise<{files: number}>`.

- [ ] **Step 1: Write the failing tests** in `tests/test_context_upload.py`. Build zips in `tmp_path` with `zipfile`.
  - `test_extracts_txt_flat`: members `a.txt`, `sub/b.txt` and `img.png` → returns 2; `dest` holds `a.txt` and `b.txt` only.
  - `test_traversal_member_rejects_upload`: members `a.txt` and `../evil.txt` → `ContextUploadError`; nothing is written to `dest` or its parent.
  - `test_uncompressed_limit`: `max_total_bytes=10` with an 11-byte member → raises.
  - `test_member_limit`: `max_members=1` with 2 members → raises.
  - `test_upload_route_sets_context_folder`: `POST` the zip → `{"files": 1}`. Then `GET /api/sessions/s/rows/0` for a row whose `doc_id_col` matches the uploaded stem returns that text as context.
  - `test_hosted_rejects_server_folder`: hosted, `PUT` with `context_folder="/etc"` → 400.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_context_upload.py -q`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** the module, the route, `_check_context_folder` and the nginx limit.
- [ ] **Step 4: Update the web Configure page.** Add an "Upload .zip of `<doc_id>`.txt files" input beside the folder field; hide the folder field when the API reports hosted mode. Add `GET /api/info` → `{"hosted": bool}` and read it in Configure. On create, upload the zip after `createSession` succeeds.
- [ ] **Step 5: Run the checks.** Run `python -m pytest tests -q` and `npm run build --prefix web`. Expected: all pass.
- [ ] **Step 6: Commit** — `feat(hosting): upload context texts, restrict server folders, raise nginx body limit`.

### Task 6: KB kit — bearer auth, waitress, contract doc, creator compose

**Files:**
- Create: `kb/auth.py`, `docs/kb-contract.md`, `deploy/kb-kit/docker-compose.yml`, `deploy/kb-kit/.env.example`, `deploy/kb-kit/README.md`
- Modify: `kb/server.py` (`main()`, ~342), `requirements-kb.txt`, `requirements.txt`
- Test: `tests/test_kb_auth.py`

**Interfaces:**
- Produces:
  - `install_bearer_auth(app: Flask, token: str | None) -> None` — a no-op when `token` is falsy. Otherwise a `before_request` that skips `/health` and returns `401 {"error": "unauthorized"}` unless `Authorization == f"Bearer {token}"`, compared with `hmac.compare_digest`.
  - `kb/server.py main()`: `install_bearer_auth(app, os.environ.get("KB_AUTH_TOKEN"))`, then `waitress.serve(app, host=…, port=…, threads=int(os.environ.get("KB_THREADS", "8")))` in place of `app.run`.

- [ ] **Step 1: Write the failing tests** in `tests/test_kb_auth.py`, using a bare `Flask` app with `/health` and `/query` routes. Do not import `kb/server.py`, which loads torch.
  - `test_no_token_is_open`: `/query` → 200.
  - `test_token_required`: token `"t"` → `/query` gives 401 with no header, 401 with `Bearer x`, and 200 with `Bearer t`.
  - `test_health_is_public`: token set → `/health` gives 200 with no header.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_kb_auth.py -q`. Expected: `ModuleNotFoundError: kb.auth` (import with `sys.path` set to the repo root, like the other tests).
- [ ] **Step 3: Implement** `kb/auth.py`, wire `main()`, and add `waitress>=3.0` to both requirements files.
- [ ] **Step 4: Write `docs/kb-contract.md`.** Document:
  - each route from spec §3.5, with request and response JSON taken from `kb/server.py`'s docstring, `_result_to_eltool` and `get_entity`
  - the bearer header
  - the HELIX "Add KB" settings to use
- [ ] **Step 5: Write the kit.** `deploy/kb-kit/docker-compose.yml` has three services:
  - `kb`: build `docker/Dockerfile.kb` with context `../..`; port bound to `127.0.0.1:8765`; env `KB_AUTH_TOKEN`; index/adapter mounts as in the root compose
  - `neo4j`: `neo4j:5-community`, under `profiles: [graph]`
  - `cloudflared`: `cloudflare/cloudflared:2024.10.0` with `tunnel --no-autoupdate run --token ${CF_TUNNEL_TOKEN}`, under `profiles: [tunnel]`

  The README covers:
  - generating a token
  - `docker compose --profile tunnel up -d`
  - the values to paste into HELIX (`https://<tunnel-host>/query`, headers `{"Authorization": "Bearer <token>"}`)
  - Neo4j over the internet: Cloudflare Tunnel cannot carry Bolt for a server-side client. Use AuraDB, or Neo4j with a TLS certificate on its Bolt connector reached as `neo4j+s://` (`neo4j+ssc://` for self-signed, e.g. through `ngrok tcp 7687`).
- [ ] **Step 6: Run the checks.** Run `python -m pytest tests -q` and `docker compose -f deploy/kb-kit/docker-compose.yml --env-file deploy/kb-kit/.env.example config -q`. Expected: tests pass; compose exits 0.
- [ ] **Step 7: Commit** — `feat(kb): bearer-token auth, waitress server, KB contract and creator kit`.

### Task 7: Session migration script

**Files:**
- Create: `scripts/migrate_sessions_for_hosting.py`
- Test: `tests/test_migrate_sessions.py`

**Interfaces:**
- Produces:
  - `migrate(src: Path, dest: Path, kb_map: dict[str, str]) -> list[str]` — returns warnings; raises `FileExistsError` if `dest` exists. It copies `src` to `dest`, then for each `*/session.json`:
    - `input_path` → `dest/<name>/<basename of old input_path>`
    - each KB `endpoint_url` whose prefix matches a `kb_map` key gets that prefix replaced; an unmapped one adds a warning
    - `context_folder` (if set): copy its files (`*.txt` and extension-less) into `dest/<name>/context/` and point at it; a missing folder → warning and `None`
    - a non-empty `llm_api_key` → warning
  - CLI: `--src`, `--dest`, and `--kb-map OLD=NEW` (repeatable); prints warnings and exits 0.

- [ ] **Step 1: Write the failing tests** — `test_rewrites_paths_and_endpoints`, `test_copies_context_texts`, `test_unmapped_endpoint_warns`, `test_missing_context_folder_warns`, `test_refuses_existing_dest`. Each builds a fake `sessions/` tree in `tmp_path` that mirrors a real `session.json`, and asserts the rewritten JSON and copied files.
- [ ] **Step 2: Run them to see them fail.** Run `python -m pytest tests/test_migrate_sessions.py -q`. Expected: FAIL on import.
- [ ] **Step 3: Implement `migrate` and the CLI.** Load and save each session through `SessionConfig`, so the output validates.
- [ ] **Step 4: Run the tests.** Run `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Dry run on real data.** Run `python scripts/migrate_sessions_for_hosting.py --src sessions --dest /tmp/helix-sessions-migrated --kb-map http://127.0.0.1:8765=https://kb.example.org`. Expected: exits 0; warnings list the other KB ports and any LLM keys. Delete the output afterwards.
- [ ] **Step 6: Commit** — `feat(hosting): script to migrate local sessions for the server`.

### Task 8: Hosted deployment bundle

**Files:**
- Create: `deploy/hosted/docker-compose.yml`, `deploy/hosted/Caddyfile`, `deploy/hosted/.env.example`, `deploy/hosted/backup.sh`, `deploy/hosted/README.md`
- Modify: `README.md` — one line pointing to `deploy/hosted/README.md` and `deploy/kb-kit/README.md`

**Interfaces:**
- Consumes: every env var from Global Constraints; `/api/info` (Task 5).
- Produces:
  - **Compose** (`name: helix`):
    - `api`: build `docker/Dockerfile.api`; env `HELIX_HOSTED=1`, `HELIX_URL_ALLOWLIST`, `ANTHROPIC_API_KEY`; volume `sessions:/app/sessions`; no published ports
    - `web`: build `docker/Dockerfile.web`; no published ports
    - `caddy`: `caddy:2.8.4`; ports `80:80` and `443:443`; volumes `./Caddyfile:/etc/caddy/Caddyfile:ro` and `caddy_data:/data`
  - **Caddyfile**: site `{$HELIX_DOMAIN}` with `basic_auth { {$HELIX_BASIC_USER} {$HELIX_BASIC_HASH} }` and `reverse_proxy web:80`.
  - **`backup.sh`**:
    - `docker run --rm -v helix_sessions:/data:ro -v "$BACKUP_DIR":/backup alpine:3.20 tar czf /backup/sessions-$(date +%F).tgz -C /data .`
    - then delete `sessions-*.tgz` older than 14 days
    - uses `set -euo pipefail`
  - **README**:
    - server size: 2 vCPU / 4 GB / 40 GB SSD
    - setup: DNS A record → `.env` (including `caddy hash-password`) → `docker compose up -d --build`
    - loading Task 7's migrated sessions into the volume (`docker compose cp`)
    - a nightly cron line for `backup.sh`
    - the smoke checklist below

- [ ] **Step 1: Validate the compose file.** Run `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env.example config -q`. Expected: exits 0.
- [ ] **Step 2: Local smoke test with `HELIX_DOMAIN=localhost`.** Run `docker compose -f deploy/hosted/docker-compose.yml up -d --build`, then check:
  - `curl -sk -o /dev/null -w '%{http_code}' https://localhost/api/sessions` → `401`
  - with `-u user:pass` → `200`
  - `curl -sk -u user:pass https://localhost/api/info` → `{"hosted":true}`
  - uploading a 5 MB CSV via `POST /api/sessions/parse-file` → `200`
  - `docker compose ps` shows no published port other than 80/443
- [ ] **Step 3: End-to-end with a real KB.** With the KB kit running behind a tunnel, create a session in the browser and confirm:
  - candidates load
  - a saved row appears in `GET /api/sessions/<name>/export`
  - the KB headers come back masked
- [ ] **Step 4: Test the backup.** Run `BACKUP_DIR=$(mktemp -d) deploy/hosted/backup.sh`. Expected: one `sessions-<date>.tgz` whose `tar tzf` lists `session.json` files.
- [ ] **Step 5: Tear down.** Run `docker compose -f deploy/hosted/docker-compose.yml down` (keep the volumes).
- [ ] **Step 6: Commit** — `feat(deploy): hosted bundle with Caddy TLS + basic auth and nightly session backups`.
