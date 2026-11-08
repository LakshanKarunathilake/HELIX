#!/usr/bin/env bash
# Archive the HELIX sessions volume and keep 14 days of backups.
# Usage: BACKUP_DIR=/var/backups/helix deploy/hosted/backup.sh
set -euo pipefail

if [ -z "${BACKUP_DIR:-}" ]; then
  echo "backup.sh: BACKUP_DIR is not set (directory to write sessions-<date>.tgz into)" >&2
  exit 1
fi
mkdir -p "$BACKUP_DIR"

# helix_sessions is the volume of the compose project named "helix".
docker run --rm -v helix_sessions:/data:ro -v "$BACKUP_DIR":/backup alpine:3.20 \
  tar czf "/backup/sessions-$(date +%F).tgz" -C /data .

find "$BACKUP_DIR" -maxdepth 1 -name 'sessions-*.tgz' -mtime +14 -delete
