# HELIX hosted server

Run HELIX on one server for a team. It runs only the web app and the API, behind Caddy, which
handles HTTPS. Access control is HELIX's own: invite-only accounts (admin, reviewer, annotator)
that sign in with Google through Firebase. Session creators bring their own KB servers and
Neo4j (see [`../kb-kit/README.md`](../kb-kit/README.md)). The design is in
[`../../docs/superpowers/specs/2026-11-25-user-management-design.md`](../../docs/superpowers/specs/2026-11-25-user-management-design.md);
reviewing and the final export follow
[`../../docs/superpowers/specs/2026-11-26-reviewer-workflow-design.md`](../../docs/superpowers/specs/2026-11-26-reviewer-workflow-design.md).

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
* **Reviewers** judge the annotators' work on the **Review** page (below) and cannot annotate:
  the API refuses their annotation writes with 403 (row and batch save, feedback, reflection,
  Auto-annotate, graph optimise, snapshot). Their session list shows how many rows are decided
  and a **Review** button, and an annotation link opens the Review page instead. Answers a
  person saved before becoming a reviewer still count as annotator answers.
* **Admins** can both annotate and review; their own saved answers count in review like anyone
  else's. Only admins create, configure and delete sessions and manage users.
* **Decision log** (`GET /api/admin/decisions` as JSON, `GET /api/admin/decisions.csv`): an
  admin reads the whole log or any one session's with `?session=<name>`. A reviewer reads only
  a session they are a member of and must name it with `?session=<name>` (another session gives
  404, no `session=` gives 403). Annotators cannot read it.
* Rows written by a script (Auto-annotate, Auto-match) do not count towards agreement, review
  or the final export until the annotator opens the row and saves it.
* A session that has logged decisions or adjudications cannot be deleted. Deleting a session
  also removes its name from every invite, so a later session with the same name starts with
  no members and no history.

### The Review page

Admins, and reviewers who are members of the session, open it with **Review** in the session
list. It shows agreement for each pair of people and each KB (exact-set match, Cohen's kappa
and mean overlap), the **Export final** button, and the rows filtered by **To decide**,
**Agreed**, **Decided** or **All**. Answers are compared as sets of concept ids, so the order
of pipe-joined ids does not matter. Nothing is final until a reviewer or admin decides it.

| Row status | Meaning | Filter |
|---|---|---|
| Disagree | Two or more people answered and their answers differ | To decide |
| One annotator | Exactly one person has answered | To decide |
| Changed | Decided, then an answer changed, appeared or disappeared | To decide |
| Agreed | Two or more people gave the same answer; not yet confirmed | Agreed |
| Decided | Has a current decision (it can be decided again while it has an answer) | Decided |

Undecided rows that nobody has answered are not listed, only counted as waiting for annotators.
A decided row whose answers have all gone stays listed: as *Changed*, or as *Decided* if its
decision is a `legacy` one.

* **Accept or combine.** Open a row, then either press **Accept** on one person's whole answer
  (enabled only when it has a value for every KB), or tick concepts from one or more answers
  and press **Confirm**. A concept nobody chose can be added by its id (the page warns if the
  KB does not know it). Every KB needs a concept or `NIL`, and `NIL` is never combined with a
  concept. Concepts start ticked only when two or more people answered and all of them chose
  it; a decided row starts from its current decision.
* **How a decision is recorded.** The server works this out: `accepted` (equals the whole
  answer of one or more people), `combined` (every concept was proposed by someone for that
  KB), `override` (anything else, including `NIL` when nobody chose it) or `bulk_agreed`. An
  override is refused without a note.
* **Bulk confirm.** Under **Agreed**, tick rows and press **Confirm selected**. A row has a
  checkbox only when the shared answer has a value for every KB and every active member of the
  session with the `annotator` role has answered every KB; otherwise it shows how many are
  pending, or that the answer is incomplete. The server checks each row again when saving, and
  skips and reports any that no longer qualify.
* **Changes after a decision.** A decided row returns to **To decide** as *Changed* if an
  annotator's answer changes, appears or disappears (`legacy` decisions excepted, below). A row
  with no annotator answer cannot be decided. That includes a previously decided row whose
  answers have all gone: it shows as *Changed* and exports blank as `changed_since_review`. A
  *Changed* row counts as decided again if the answers return to exactly what they were at
  decision time. A decision sent from an out-of-date screen is refused.
* **Older decisions.** Decisions saved before this workflow are kept, shown as `legacy`, and
  never marked changed: such a row stays *Decided* and still exports its values after its
  answers change, even when they have all gone.
* **Added KBs.** A decision with no value for one of the session's current KBs (a KB added
  since) does not decide the row.

### Final export

**Export final** on the Review page (or `GET /api/sessions/<name>/export?final=1`) downloads one
line per input row: the input columns, one column per KB, then `_basis`, `_decided_by`,
`_needs_review` and `_review_reason`. Only decided rows have KB values, with `_basis` (as above,
or `legacy`) and `_decided_by` (the name of the person who decided). On every other row the KB
columns, `_basis` and `_decided_by` are blank, `_needs_review` is `1`, and `_review_reason`
says why:

| `_review_reason` | The row |
|---|---|
| `disagree` | has differing answers |
| `single_annotator` | has one person's answer |
| `awaiting_review` | has agreeing answers that nobody has confirmed |
| `changed_since_review` | was decided, and the answers changed afterwards |
| `script_only` | is undecided and has no answer, only script-written values |
| `no_decisions` | is undecided and has no answer |

Decision notes are kept in `helix.db`; this file does not include them.

### Limits to know about

* Sessions created before accounts keep their shared `annotated.csv`. In accounts mode each
  member opens such a session with an empty personal layer. An administrator downloads the
  legacy file as-is with `GET /api/sessions/<name>/export?legacy=1` (404 if the session has
  none); the other export routes serve per-user layers only (an administrator can name another
  user's with `?user=<id>`).
* Annotators work blind, from scratch. The Auto-annotate modal is on the admin-only Configure
  page and writes only into the administrator's own layer, where its picks are logged as script
  decisions (`auto_annotate`) that do not count towards agreement. An annotator-facing
  Auto-annotate is a possible follow-up.
* Countdown auto-accept saves (the Annotate page timer running out with no interaction) are
  logged as `human` decisions (study sessions also record the method, `auto_accept` for an
  accepted candidate), and they DO count towards agreement and review, including bulk-confirm
  eligibility.
* In the final export, a KB column, `_basis`, `_decided_by`, `_needs_review` or
  `_review_reason` with the same name as an input column overwrites that input column.
* The login cookie's `Secure` attribute follows `HELIX_HOSTED`: run with `HELIX_HOSTED=1`
  behind TLS (as this compose file does), or the cookie is also sent over plain HTTP.
* A re-created Google account (same email, new Google uid) is refused at sign-in. There is no
  unlink in the UI: an administrator has to clear that user's `firebase_uid` in `helix.db`
  directly (or invite a different address and untick **Active** on the old account).
* Review-mode (Mode C) sessions write their pre-annotations into the session's legacy
  `annotated.csv` (see the first point above), so pre-annotations are not shown per user in accounts mode.
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
memberships, the decision log and the review decisions) beside the per-session folders, so
every archive contains the accounts and the full history of both. The archives also contain
every stored secret, so the script makes `$BACKUP_DIR` mode `700` and writes the archives
readable by their owner only. Add it to cron (`crontab -e`):

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

### Before upgrading

**Back up `sessions/helix.db` before you upgrade.** The first time a version with this review
workflow opens the database, it adds three columns (`basis`, `source_users`, `based_on`) to the
table of review decisions; existing rows are kept. Run `backup.sh` before rebuilding, and
copy the archive to another name: a later run on the same day overwrites
`sessions-<date>.tgz`, and the script deletes `sessions-*.tgz` archives older than 14 days.

**The final export changes too.** Rows that the previous version exported as final just because
annotators agreed now export blank, with `_review_reason` `awaiting_review`, until a reviewer
or admin confirms them on the Review page, individually or in bulk.

The archive is written by a container running as root, so on a Linux server it belongs to root
with mode `600`: run the backup and the copy with `sudo`, as below (leave `sudo` out if you are
root). From the repo root, with your own backup directory in the three places. Each command runs
only if the one before it succeeded, so a failed backup stops the upgrade:

```
sudo env BACKUP_DIR=/var/backups/helix deploy/hosted/backup.sh &&
sudo cp -p "/var/backups/helix/sessions-$(date +%F).tgz" \
  "/var/backups/helix/pre-upgrade-$(date +%Y%m%d-%H%M%S).tgz" &&
git pull &&
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env up -d --build
```

The `pre-upgrade-*.tgz` name carries the date and time, so a repeat run does not overwrite an
earlier copy. A copy made after the new version has started holds the upgraded database, so keep
the earliest one. The script does not delete these copies; they contain the stored secrets, so
remove them once you no longer need them.

To restore a pre-upgrade copy, follow "Restoring a backup" below with the same `BACKUP_DIR`,
and set `ARCHIVE` to the copy's file name, `pre-upgrade-<date>-<time>.tgz`, instead of
`sessions-YYYY-MM-DD.tgz` (`sudo ls /var/backups/helix` lists the copies). This takes the
database, and the session files in the archive, back to that moment. Its last command,
`start api`, runs the version that is currently built, which adds the columns again; to return
to the earlier version, check out the commit you ran before and use the `up -d --build` command
above in place of `start api`.

### Restoring a backup

The archive holds the database as `helix.snapshot.db`. The restore below checks the archive
first and changes nothing if it is unusable (wrong date or `BACKUP_DIR`, corrupt archive, no
snapshot, failed integrity check). Only then does it move the current `helix.db`,
`helix.db-wal` and `helix.db-shm` aside (renamed to `helix.db.pre-restore`,
`helix.db.pre-restore-wal`, `helix.db.pre-restore-shm`, not deleted), put the snapshot in as
`helix.db`, and copy the sessions over. It refuses to start if any `helix.db.pre-restore*` file
from an earlier restore is still in the volume (move or delete them first), and it never copies
database files from the archive over the restored `helix.db`. From the repo root, edit
`BACKUP_DIR` and the archive's date (`YYYY-MM-DD`) in the first two lines:

```
BACKUP_DIR=/var/backups/helix
ARCHIVE=sessions-YYYY-MM-DD.tgz

docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env stop api

docker run --rm -i -e ARCHIVE="$ARCHIVE" \
  -v helix_sessions:/data -v "$BACKUP_DIR":/backup:ro python:3.11-alpine sh -s <<'EOF'
set -eu
cd /data
fail() { echo "restore: $1; nothing was changed" >&2; exit 1; }
for f in helix.db.pre-restore helix.db.pre-restore-wal helix.db.pre-restore-shm; do
  if [ -e "$f" ]; then fail "$f already exists in the volume; move or delete helix.db.pre-restore* first"; fi
done
work=$(mktemp -d)
[ -f "/backup/$ARCHIVE" ] || fail "/backup/$ARCHIVE not found"
tar xzf "/backup/$ARCHIVE" -C "$work" || fail "cannot extract $ARCHIVE"
[ -f "$work/helix.snapshot.db" ] || fail "$ARCHIVE has no helix.snapshot.db (made by an older backup.sh?)"
python3 -c "import sqlite3, sys; r = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchall(); sys.exit(r != [('ok',)])" \
  "$work/helix.snapshot.db" || fail "the snapshot in $ARCHIVE failed its integrity check"
for f in helix.db helix.db-wal helix.db-shm; do
  if [ -e "$f" ]; then mv -f "$f" "helix.db.pre-restore${f#helix.db}"; fi
done
mv "$work/helix.snapshot.db" helix.db
rm -rf "$work"/helix.db "$work"/helix.db-wal "$work"/helix.db-shm "$work"/helix.db.pre-restore*
cp -a "$work"/. .
rm -rf "$work"
echo "restored $ARCHIVE; the previous database is kept as helix.db.pre-restore"
EOF
```

If it prints a `restore:` message, nothing changed: fix the date or directory (or remove the
old `helix.db.pre-restore*` files) and run it again. Now check the restored database **before** starting `api` (a missing `helix.db` would
otherwise be silently replaced by a new empty one, which would also pass an integrity check).
This opens the file read-only, so it fails instead of creating a file if `helix.db` is missing:

```
docker run --rm -v helix_sessions:/data python:3.11-alpine python -c "import sqlite3; c = sqlite3.connect('file:/data/helix.db?mode=ro', uri=True); q = lambda s: c.execute(s).fetchall(); print(q('PRAGMA integrity_check'), 'users', q('SELECT count(*) FROM users')[0][0], 'decisions', q('SELECT count(*) FROM decisions')[0][0])"
```

Expect `[('ok',)]`, a non-zero number of users, and as many decisions as you had when the
backup was taken. If both counts are zero, or the command fails, something went wrong: put the
old database back before starting `api`. The same applies if the restore script itself died
after it began moving files (the verify then fails on a missing or partial `helix.db`). This
replaces `helix.db*` with the saved `helix.db.pre-restore*` files and does nothing if there are
none:

```
docker run --rm -v helix_sessions:/data python:3.11-alpine sh -c '
cd /data
[ -f helix.db.pre-restore ] || { echo "rollback: no helix.db.pre-restore in the volume" >&2; exit 1; }
rm -f helix.db helix.db-wal helix.db-shm
for f in helix.db.pre-restore*; do mv "$f" "helix.db${f#helix.db.pre-restore}"; done'
```

When the numbers are right, start `api`:

```
docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env start api
```

The `helix.db.pre-restore*` files stay in the volume until you delete them (`backup.sh` leaves
them out of its archives); remove them once you are satisfied with the restore, because the
next restore refuses to run while they exist. Sessions in the archive overwrite the
same-named folders in the volume; sessions that are not in the archive are left alone.

Archives made by the older `backup.sh` have no `helix.snapshot.db`, so the restore above stops
with a message instead of proceeding. For such an archive, extract it into a scratch folder by
hand and copy the `helix.db` (and any `helix.db-wal` / `helix.db-shm`) found there into the
volume after moving the current ones aside. That copy is consistent only if `api` was stopped
when the archive was made; otherwise it may be torn, so check it with the read-only command
above.

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
   answers on that row (under **All**).
7. `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env ps` shows no
   published port other than 80 and 443.
8. `BACKUP_DIR=$(mktemp -d) deploy/hosted/backup.sh` writes one `sessions-<date>.tgz`, and
   `tar tzf` on it lists `helix.snapshot.db` (and no `helix.db*` files) and the `session.json`
   files. The directory is mode `700`, no `.tmp` file is left behind, and the snapshot is gone
   from the volume again.

Stop it with `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env down`
(add `-v` only if you want to delete the sessions, the accounts and the decision log).
