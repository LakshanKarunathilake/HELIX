# HELIX hosted server

Run HELIX on one server for a team. It runs only the web app and the API, behind Caddy, which
handles HTTPS. Access control is HELIX's own: invite-only accounts (admin, reviewer, annotator)
that sign in with Google through Firebase. Session creators bring their own KB servers and
Neo4j (see [`../kb-kit/README.md`](../kb-kit/README.md)). The design is in
[`../../docs/superpowers/specs/2026-11-25-user-management-design.md`](../../docs/superpowers/specs/2026-11-25-user-management-design.md).

The API runs a single uvicorn worker, and it must stay that way: the SQLite database and the
in-process locks that guard writes assume one process.

You need a server with Docker (Compose v2), a domain name, and about 2 vCPU / 4 GB RAM / 40 GB SSD.

## 1. DNS

Create an A record for your hostname (for example `helix.example.com`) pointing at the server's
public IP. Ports 80 and 443 must be reachable; Caddy gets the certificate by itself.

## 2. Get the code

```
git clone <repo-url> helix && cd helix
```

## 3. Firebase and accounts

Sign-in is Google only, through Firebase, so the server needs internet access and you need a
Firebase project. One-off setup in the [Firebase console](https://console.firebase.google.com):

1. Create a project (Analytics is not needed).
2. **Authentication -> Sign-in method**: enable the **Google** provider.
3. **Authentication -> Settings -> Authorized domains**: add your `HELIX_DOMAIN` hostname.
4. **Project settings -> Your apps**: register a Web app and copy its config (`apiKey`,
   `authDomain`, `projectId`). `authDomain` is normally `<project>.firebaseapp.com`; the Google
   popup login runs through it.

Then configure HELIX:

```
cp deploy/hosted/.env.example deploy/hosted/.env
```

Edit `deploy/hosted/.env`:

* `HELIX_DOMAIN`: your hostname. The compose file also derives `HELIX_PUBLIC_URL`
  (`https://<HELIX_DOMAIN>`) from it; invitation sign-in links use that URL.
* `HELIX_FIREBASE_PROJECT_ID`, `HELIX_FIREBASE_API_KEY`, `HELIX_FIREBASE_AUTH_DOMAIN`: the
  three values from the Firebase web config. They are public web identifiers, not secrets:
  `GET /api/info` hands them to the browser. The compose file refuses to start without them.
  The API verifies sign-ins against the project id.
* `HELIX_URL_ALLOWLIST`: comma-separated `host:port` of KB servers that may use plain `http`
  (leave empty if every KB uses `https`).
* `ANTHROPIC_API_KEY`: for the LLM features.
* `BACKUP_DIR`: where backups go (step 7).

The compose file turns accounts on for you (`HELIX_AUTH=1`); there is no shared password in
front of the site any more.

## 4. Start it

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env up -d --build
```

## 5. Create the first administrator

Nobody can sign in until an administrator exists. Create it from the command line:

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env \
  exec api python -m eltool.users create-admin --email you@example.com --name "You"
```

`--name` is optional (default: the part of the email before `@`). The command refuses an email
that already has an account. The administrator row has no Google identity yet: open
`https://<your-domain>` and sign in with Google using exactly that email address, and the
account is linked at that first sign-in. The email given to `create-admin` must be exactly the
Google email the person signs in with, and a verified Google account; any other address is
refused.

### Inviting people

Administrators use the **Users** page: enter the email, pick a role (`annotator`, `reviewer`
or `admin`) and the sessions the person may open, then create the invite. HELIX sends no
email: copy the sign-in URL shown after creating the invite and send it yourself. The person
signs in with that Google account and is created with the invited role and sessions. Only the
`google.com` sign-in provider is accepted. A pending invite can be revoked from the same
page. Sessions of an existing user are edited on the Users page too.

If someone loses their Google account, invite the new address and untick **Active** for the
old account on the Users page. Inviting an address that already has an account does not create
an invite: it only adds the chosen sessions to that account and leaves its role unchanged
(roles are changed on the Users page).

### Who sees what

* **Annotators** see only the sessions they are members of, and only their own annotations.
  Their layer is `annotations/<user_id>.csv` in the session folder.
* **Reviewers and admins** use the **Review** page of a session: every user's decisions per
  row, pairwise agreement, and an adjudication box. Only admins create, configure and delete
  sessions, manage users, and download the decision log (`GET /api/admin/decisions.csv`).
* **Final export** (Review page, or `GET /api/sessions/<name>/export?final=1`): a row gets a
  value when two human annotators made the same decision in every KB column, or when a
  reviewer adjudicated it. Every other row has blank KB columns, `_needs_review` set to `1`,
  and `_review_reason` set to one of `disagree`, `single_annotator`, `script_only` or
  `no_decisions`.
* Rows written by a script (Auto-annotate, Auto-match) do not count towards agreement or the
  final export until the annotator opens the row and saves it.
* A session that has logged decisions cannot be deleted.

### Limits to know about

* Sessions created before accounts keep their shared `annotated.csv`. In accounts mode each
  member opens such a session with an empty personal layer. The export routes only serve a
  user's own layer (an administrator can name another user's with `?user=<id>`), never the
  legacy `annotated.csv`; that file stays in the session folder of the sessions volume, so copy
  it out of the volume if you need it.
* Review-mode (Mode C) sessions write their pre-annotations into that same legacy
  `annotated.csv`, so pre-annotations are not shown per user in accounts mode.
* Sign-in needs internet access from the browser and from the API (to fetch Google's token
  signing keys), and a Firebase project.
* Only the `google.com` provider is accepted. There is no self sign-up, and no email sending.

## 6. Move existing sessions (optional)

On the Mac that has the sessions, from the repo root:

```
python scripts/migrate_sessions_for_hosting.py --src sessions --dest /tmp/hosted-sessions \
  --kb-map http://127.0.0.1:8765=https://kb.example.com --server-root /app/sessions
```

Read its warnings and act on them: KB endpoints with no mapping still point at your laptop,
localhost graphs will not work on the server, and stored secrets should be re-entered in HELIX.
Migrated sessions have no memberships: annotators see nothing until an administrator grants
access on the Users page. Their existing `annotated.csv` files are not shown per user in
accounts mode (see "Limits to know about" above). Copy the result to the server and load it
into the volume:

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env \
  cp <out>/. api:/app/sessions/
```

## 7. Nightly backup

`backup.sh` archives the whole sessions volume to `$BACKUP_DIR/sessions-<date>.tgz` and deletes
archives older than 14 days. The volume holds `helix.db` (the user table, invitations,
memberships and the decision log) beside the per-session folders, so every archive contains
the accounts and the full decision history. The archives also contain every stored secret, so
the script makes `$BACKUP_DIR` mode `700` and writes the archives readable by their owner only.
Add it to cron (`crontab -e`):

```
0 3 * * * BACKUP_DIR=/var/backups/helix /path/to/helix/deploy/hosted/backup.sh
```

`helix.db` is a WAL-mode SQLite database that the API writes to while it runs, so the script
does not tar the live file. It first asks the running `api` container to write a consistent
copy with SQLite's online backup (`helix.snapshot.db`, in the volume), runs `PRAGMA
integrity_check` on it, and archives the snapshot in place of `helix.db`, `helix.db-wal` and
`helix.db-shm`. If the check fails the script exits non-zero and writes no archive. The
snapshot is removed from the volume afterwards, on failure too. It is safe to run while people
are annotating; no particular time of night is needed. If `api` is not running the snapshot is
taken from a one-off `python:3.11-alpine` container instead (nothing is writing then).

### Restoring a backup

The archive holds the snapshot as `helix.snapshot.db`; restoring renames it to `helix.db`. Stop
`api` first, so nothing has `helix.db` open, and remove any stale `helix.db-wal` and
`helix.db-shm`, which would otherwise be mixed with the restored file. From the repo root:

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env stop api

docker run --rm -v helix_sessions:/data -v /var/backups/helix:/backup:ro alpine:3.20 sh -c \
  "cd /data && rm -f helix.db helix.db-wal helix.db-shm \
   && tar xzf /backup/sessions-<date>.tgz && mv -f helix.snapshot.db helix.db"

docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env start api

docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env \
  exec -T api python -c "import sqlite3; print(sqlite3.connect('/app/sessions/helix.db').execute('PRAGMA integrity_check').fetchall())"
```

Use your own `BACKUP_DIR` for `/var/backups/helix` and the archive's date for `<date>`. The last
command should print `[('ok',)]`. Sessions in the archive overwrite the same-named folders in
the volume; sessions that are not in the archive are left alone.

## Smoke checklist (after the first deploy)

Replace `<domain>` with your hostname. For a local trial set `HELIX_DOMAIN=localhost` and use
`curl -k` (self-signed certificate). Steps marked **manual** need a person with a Google
account; the rest are commands.

1. `curl -sk -o /dev/null -w '%{http_code}' https://<domain>/api/sessions` returns `401` while
   signed out.
2. `curl -sk https://<domain>/api/info` shows `"auth":true` and a `"firebase"` block holding your
   `apiKey`, `authDomain` and `projectId`.
3. **Manual.** Sign in with Google as the bootstrap administrator (step 5) at
   `https://<domain>`, using the exact Google email that `create-admin` was run with. The
   session list opens.
4. **Manual.** As that administrator, create a session from an uploaded CSV (with your KB kit
   running; saving returns to the session list without an error, which also moves the upload
   from the container's `/tmp` into the sessions volume, a different filesystem). On the
   Users page invite a second Google account as `annotator` with that session. That person
   signs in with the copied URL and sees only that session.
5. **Manual.** Sign in with a Google account that has no invite: it is refused with "This
   Google account is not authorised for HELIX".
6. **Manual.** Two annotators open the same session and save the same row. Two files exist
   under `annotations/` in the sessions volume (check with
   `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env exec api ls /app/sessions/<session>/annotations`),
   named by user id, and the session's Review page (as a reviewer or administrator) shows both
   decisions.
7. `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env ps` shows no
   published port other than 80 and 443.
8. `BACKUP_DIR=$(mktemp -d) deploy/hosted/backup.sh` writes one `sessions-<date>.tgz`, and
   `tar tzf` on it lists `helix.snapshot.db` (and no `helix.db*` files) and the `session.json`
   files. The directory is mode `700`, no `.tmp` file is left behind, and the snapshot is gone
   from the volume again.

Stop it with `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env down`
(add `-v` only if you want to delete the sessions, the accounts and the decision log).
