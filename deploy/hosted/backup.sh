#!/usr/bin/env bash
# Archive the HELIX sessions volume and keep 14 days of backups.
# Usage: BACKUP_DIR=/var/backups/helix deploy/hosted/backup.sh
set -euo pipefail
# The archives hold every stored secret (LLM keys, KB headers, Neo4j passwords).
umask 077

if [ -z "${BACKUP_DIR:-}" ]; then
  echo "backup.sh: BACKUP_DIR is not set (directory to write sessions-<date>.tgz into)" >&2
  exit 1
fi
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

name="sessions-$(date +%F).tgz"
# helix_sessions is the volume of the compose project named "helix".
# Write to a .tmp name and rename on success, so a failed run never leaves a
# truncated archive that looks valid.
docker run --rm -v helix_sessions:/data:ro -v "$BACKUP_DIR":/backup alpine:3.20 \
  sh -c "umask 077 && tar czf '/backup/$name.tmp' -C /data ."
mv -f "$BACKUP_DIR/$name.tmp" "$BACKUP_DIR/$name"

find "$BACKUP_DIR" -maxdepth 1 -name 'sessions-*.tgz' -mtime +14 -delete
