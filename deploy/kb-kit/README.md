# HELIX KB kit

Run your own KB search server and let a hosted HELIX use it. Your index never leaves your
machine; HELIX only sends search queries to it.

You need Docker, your FAISS index folder and your adapter folder.

## 1. Configure

```
cp .env.example .env
```

Edit `.env`:

* `INDEX_HOST_DIR`, `INDEX_PREFIX`, `ADAPTER_HOST_DIR`: where your index and adapter are.
* `KB_AUTH_TOKEN`: make one with `openssl rand -hex 32` and paste it in. Keep it secret.

## 2. Get a public address (Cloudflare Tunnel)

1. In Cloudflare Zero Trust, create a tunnel and copy its token into `CF_TUNNEL_TOKEN` in `.env`.
2. In the tunnel's Public Hostname, point your hostname (for example `kb.example.com`) at
   `http://kb:8765`.

## 3. Start it

```
docker compose --profile tunnel up -d
```

The first start loads the index, which can take a few minutes. Errors or a failed connection
during that time are expected. Watch progress with `docker compose logs -f kb`, then check:

```
curl https://<tunnel-host>/health
```

## 4. Add it in HELIX

In HELIX, open Add KB and paste these values:

| Setting | Value |
| --- | --- |
| Endpoint URL | `https://<tunnel-host>/query` |
| Method | `POST` |
| request_template | `{"label": "{label}", "top_k": {top_k}}` |
| response_path | `results` |
| id_field | `id` |
| label_field | `label` |
| score_field | `score` |
| headers | `{"Authorization": "Bearer <token>"}` |

Use the same token as `KB_AUTH_TOKEN`. Details of what HELIX calls are in
[`docs/kb-contract.md`](../../docs/kb-contract.md).

## Stop / update

```
docker compose --profile tunnel down
docker compose --profile tunnel up -d --build
```

## Optional: Neo4j graph

`docker compose --profile graph up -d` starts Neo4j Community (set `NEO4J_PASSWORD` first).
Its ports are published on this machine only (`127.0.0.1`). To reach it from the internet you
must expose Bolt on purpose, with TLS, as described below.

Cloudflare Tunnel cannot carry Bolt (Neo4j's protocol) for a server-side client, so the tunnel
above will not work for Neo4j. Pick one:

* **AuraDB**: use a managed Neo4j and its `neo4j+s://` address. Simplest.
* **Your own Neo4j with TLS**: give Neo4j's Bolt connector a TLS certificate and use
  `neo4j+s://<host>:7687`. If the certificate is self-signed, use `neo4j+ssc://` instead,
  for example when exposing the port through `ngrok tcp 7687`.

Hosted HELIX accepts only `neo4j+s`, `bolt+s`, `neo4j+ssc` and `bolt+ssc` addresses.
Community edition has only the default `neo4j` database.
