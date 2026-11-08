# KB contract

This is what HELIX calls on a knowledge-base (KB) server. `kb/server.py` implements it; any
server that follows it can be used instead.

HELIX is configured with one URL per KB, the **`/query` URL**. It derives the others from it:
`/query/dense` and `/query/lexical` by suffix, and `/entity/<id>` by replacing the trailing
`/query`.

All request and response bodies are JSON.

## Authentication

If the server sets `KB_AUTH_TOKEN`, every route except `GET /health` needs this header and
returns `401 {"error": "unauthorized"}` without it:

```
Authorization: Bearer <token>
```

HELIX sends whatever you put in the KB's `headers` setting, so add it there.
`/health` is always public.

## Routes

### `POST /query`

Search with the server's default strategy (dense).

Request:

```json
{"label": "<mention text>", "top_k": 10}
```

`label` is required and non-empty. `top_k` is 1 to 100 (default 10). Otherwise the server
returns `400 {"error": "..."}`.

Response `200`:

```json
{"results": [
  {"id": "D003920", "label": "Diabetes Mellitus", "score": 0.97,
   "matched_alias": "diabetes", "aliases": ["Diabetes Mellitus", "diabetes"],
   "semantic_groups": "DISO", "semantic_types": "T047", "definition": "...", "rank": 1}
]}
```

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Concept identifier. HELIX stores this. |
| `label` | string | Preferred name shown to annotators. |
| `score` | number or null | Similarity, higher is better. |
| `matched_alias` | string | The variant that matched. |
| `aliases` | string[] | All known names. |
| `semantic_groups`, `semantic_types`, `definition` | string | Empty string if unknown. |
| `rank` | integer | 1-based position. |

HELIX only requires `id`, `label` and `score`. The rest is optional and improves
disambiguation. Failures return `500 {"error": "..."}`.

### `POST /query/dense`

Same request and response as `/query`, always using dense (embedding) search.

### `POST /query/lexical`

Same request and response as `/query`, always using lexical (BM25) search.
Returns `503 {"error": "lexical index not initialised"}` if lexical search is disabled.

### `GET /entity/<id>`

Look up one concept by id. `<id>` is URL-escaped (composite ids may contain `|`).

Response `200`: the raw index metadata for the concept, a JSON object. Note the keys differ
from search results. HELIX reads these:

| Key | Type | How HELIX uses it |
| --- | --- | --- |
| `entity_id` | string | Becomes the id. Falls back to the id that was requested. |
| `primary_alias` | string | Becomes the label. Falls back to the first alias, then to the id. |
| `all_aliases` | string[] | Alias list. Falls back to `aliases` (the server also adds `aliases` as a copy of `all_aliases`). |
| `semantic_groups`, `semantic_types`, `definition` | string | Optional, empty string if missing. |

Any other keys are ignored. `404 {"error": "'<id>' not found"}` means the id is not in the
index. HELIX shows that as "not in the index" rather than an error. Other failures return
`500 {"error": "..."}`.

### `GET /health`

Public. `200` with `{"status": "ok", "index": {...}, "lexical": {...}|null, "cache": {...}}`
when ready. `503 {"status": "not_ready"}` can appear if the index is not usable, but while the
server first starts it loads the index before opening its port, so connection errors (a tunnel
shows 502 or 530) during loading are normal.

### `POST /cache/clear`

Clears the server's result caches. Returns `{"ok": true}`. Needs the bearer token if one is set.
HELIX does not call it; it is for operators.

## HELIX "Add KB" settings

| Setting | Value |
| --- | --- |
| Endpoint URL | `https://<host>/query` |
| Method | `POST` |
| request_template | `{"label": "{label}", "top_k": {top_k}}` |
| response_path | `results` |
| id_field | `id` |
| label_field | `label` |
| score_field | `score` |
| headers | `{"Authorization": "Bearer <token>"}` (only if the server has `KB_AUTH_TOKEN`) |

A hosted HELIX only accepts `https` KB endpoints, except for hosts on its allow-list.
