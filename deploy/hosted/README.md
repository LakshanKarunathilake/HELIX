# HELIX hosted server

Run HELIX on one server for a team. It runs only the web app and the API, behind Caddy, which
handles HTTPS and a username/password gate. Session creators bring their own KB servers and
Neo4j (see [`../kb-kit/README.md`](../kb-kit/README.md)).

The basic-auth gate is the interim access control: everyone past it is a trusted collaborator.

You need a server with Docker (Compose v2), a domain name, and about 2 vCPU / 4 GB RAM / 40 GB SSD.

## 1. DNS

Create an A record for your hostname (for example `helix.example.com`) pointing at the server's
public IP. Ports 80 and 443 must be reachable; Caddy gets the certificate by itself.

## 2. Get the code

```
git clone <repo-url> helix && cd helix
```

## 3. Configure

```
cp deploy/hosted/.env.example deploy/hosted/.env
docker run --rm caddy:2.8.4 caddy hash-password --plaintext '<pw>'
```

Edit `deploy/hosted/.env`:

* `HELIX_DOMAIN`: your hostname.
* `HELIX_BASIC_USER`, `HELIX_BASIC_HASH`: the login, and the hash printed above. Keep the single
  quotes around the hash; it contains `$` characters that Compose would otherwise swallow.
* `HELIX_URL_ALLOWLIST`: comma-separated `host:port` of KB servers that may use plain `http`
  (leave empty if every KB uses `https`).
* `ANTHROPIC_API_KEY`: for the LLM features.
* `BACKUP_DIR`: where backups go (step 6).

## 4. Start it

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env up -d --build
```

Open `https://<your-domain>` and sign in.

## 5. Move existing sessions (optional)

On the Mac that has the sessions, from the repo root:

```
python scripts/migrate_sessions_for_hosting.py --src sessions --dest /tmp/hosted-sessions \
  --kb-map http://127.0.0.1:8765=https://kb.example.com --server-root /app/sessions
```

Read its warnings and act on them: KB endpoints with no mapping still point at your laptop,
localhost graphs will not work on the server, and stored secrets should be re-entered in HELIX.
Then copy the result to the server and load it into the volume:

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env \
  cp <out>/. api:/app/sessions/
```

## 6. Nightly backup

`backup.sh` archives the sessions volume to `$BACKUP_DIR/sessions-<date>.tgz` and deletes
archives older than 14 days. Add it to cron (`crontab -e`):

```
0 3 * * * BACKUP_DIR=/var/backups/helix /path/to/helix/deploy/hosted/backup.sh
```

Restore by extracting an archive into `/app/sessions` of the `api` container.

## Smoke checklist (after the first deploy)

Replace `user:pass` with your login. For a local trial set `HELIX_DOMAIN=localhost` and use
`curl -k` (self-signed certificate).

1. `curl -sk -o /dev/null -w '%{http_code}' https://<domain>/api/sessions` returns `401`.
2. Same with `-u user:pass` returns `200`.
3. `curl -sk -u user:pass https://<domain>/api/info` returns `{"hosted":true}`.
4. Upload a 5 MB CSV: `curl -sk -u user:pass -F file=@big.csv https://<domain>/api/sessions/parse-file`
   returns `200`.
5. `docker compose -f deploy/hosted/docker-compose.yml ps` shows no published port other than 80
   and 443.
6. With your KB kit running, create a session in the browser: candidates load, a saved row
   appears in `GET /api/sessions/<name>/export`, and the KB headers come back masked.
7. `BACKUP_DIR=$(mktemp -d) deploy/hosted/backup.sh` writes one `sessions-<date>.tgz`, and
   `tar tzf` on it lists `session.json` files.

Stop it with `docker compose -f deploy/hosted/docker-compose.yml down` (add `-v` only if you
want to delete the sessions).
