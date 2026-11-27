#!/usr/bin/env bash
# Archive the HELIX sessions volume and keep 14 days of backups.
# Usage: BACKUP_DIR=/var/backups/helix deploy/hosted/backup.sh
set -euo pipefail
# The archives hold every stored secret (LLM keys, KB headers, Neo4j passwords) and
# helix.db (the user table, invitations and the decision log).
umask 077

if [ -z "${BACKUP_DIR:-}" ]; then
  echo "backup.sh: BACKUP_DIR is not set (directory to write sessions-<date>.tgz into)" >&2
  exit 1
fi
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

name="sessions-$(date +%F).tgz"
# helix_sessions is the volume of the compose project named "helix".
VOLUME=helix_sessions
SNAPSHOT=helix.snapshot.db

# helix.db runs in WAL mode and the API writes to it while it is up, so tarring the live file
# can capture a torn database. Instead SQLite's online backup API writes a consistent copy
# (helix.snapshot.db, in the volume), which is integrity-checked and archived in place of
# helix.db / helix.db-wal / helix.db-shm. The snippet exits non-zero unless the snapshot's
# integrity_check says "ok"; then no archive is written or replaced.
SNAPSHOT_PY='
import os, sqlite3, sys
d = "/app/sessions"
src, snap = os.path.join(d, "helix.db"), os.path.join(d, "helix.snapshot.db")
if not os.path.exists(src):
    sys.exit("backup.sh: " + src + " not found")
s, t = sqlite3.connect(src), sqlite3.connect(snap)
s.backup(t)
t.execute("PRAGMA journal_mode=DELETE")   # one self-contained file, no -wal/-shm
result = t.execute("PRAGMA integrity_check").fetchall()
t.close(); s.close()
os.chmod(snap, 0o600)
if result != [("ok",)]:
    sys.exit("backup.sh: integrity_check on the snapshot failed: " + repr(result))
'

# Remove the snapshot from the volume and any half-written archive, on success and on failure.
tmp="$BACKUP_DIR/$name.tmp"
cleanup() {
  rm -f "$tmp"
  docker run --rm -v "$VOLUME":/data alpine:3.20 rm -f "/data/$SNAPSHOT" \
    || echo "backup.sh: could not remove $SNAPSHOT from the $VOLUME volume" >&2
}
trap cleanup EXIT

# Take the snapshot inside the running api container (the process that holds helix.db).
# If api is not running there are no writers, so a one-off container opens the same volume
# and takes the snapshot with the same code.
api=$(docker ps -q --filter label=com.docker.compose.project=helix \
        --filter label=com.docker.compose.service=api | head -n 1)
if [ -n "$api" ]; then
  docker exec -i "$api" python -c "$SNAPSHOT_PY"
else
  docker run --rm -v "$VOLUME":/app/sessions python:3.11-alpine python -c "$SNAPSHOT_PY"
fi

# Write to a .tmp name and rename on success, so a failed run never leaves a
# truncated archive that looks valid. The top-level entries are listed by name (everything
# except the live database files and any helix.db.pre-restore* left by a restore) rather than
# excluded by pattern.
docker run --rm -v "$VOLUME":/data:ro -v "$BACKUP_DIR":/backup alpine:3.20 \
  sh -c "umask 077 && cd /data && tar czf '/backup/$name.tmp' \$(find . -mindepth 1 -maxdepth 1 ! -name helix.db ! -name helix.db-wal ! -name helix.db-shm ! -name 'helix.db.pre-restore*')"
mv -f "$tmp" "$BACKUP_DIR/$name"

find "$BACKUP_DIR" -maxdepth 1 -name 'sessions-*.tgz' -mtime +14 -delete
