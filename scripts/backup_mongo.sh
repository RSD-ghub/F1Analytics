#!/usr/bin/env bash
# Back up the databases that cannot be rebuilt, and keep the last few copies.
#
# What and why: f1_prediction holds the locked forecasts, which cannot be
# honestly recreated (any replacement would be written after the race).
# f1_core holds accounts and Bernie conversations. f1_scoring is rebuildable
# but small, and restoring it saves a full re-reconcile. f1_ingestion is left
# out: it is large and can be re-ingested from FastF1.
#
# Usage:
#   scripts/backup_mongo.sh                  # back up, keep the newest 30
#   BACKUP_DIR=/Volumes/Backup/f1 KEEP=60 scripts/backup_mongo.sh
#
# Settings (environment):
#   MONGO_URI      default mongodb://localhost:27017 (native Mongo, as on the Mac)
#   BACKUP_DIR     default ~/f1-backups. Put it on another disk or a synced
#                  folder: a backup on the same disk as the database is not one.
#   KEEP           how many backups to keep, default 30
#   DATABASES      default "f1_prediction f1_core f1_scoring"
#   MONGO_CONTAINER  default f1_mongodb; used when mongodump is not installed,
#                  with MONGO_USER / MONGO_PASSWORD from the environment
#
# Restore one database:
#   mongorestore --uri "$MONGO_URI" --gzip --archive=<dir>/f1_prediction.archive.gz
#
# Exits non-zero if any dump fails, so a scheduler (cron, launchd) can report it.
set -euo pipefail

MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/f1-backups}"
KEEP="${KEEP:-30}"
DATABASES="${DATABASES:-f1_prediction f1_core f1_scoring}"
MONGO_CONTAINER="${MONGO_CONTAINER:-f1_mongodb}"

stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
target="$BACKUP_DIR/f1-$stamp"
mkdir -p "$target"

dump() {
  local db="$1" out="$2"
  if command -v mongodump >/dev/null 2>&1; then
    mongodump --quiet --uri "$MONGO_URI" --db "$db" --gzip --archive="$out"
  elif command -v docker >/dev/null 2>&1 \
      && docker ps --format '{{.Names}}' | grep -qx "$MONGO_CONTAINER"; then
    local auth=()
    if [[ -n "${MONGO_USER:-}" ]]; then
      auth=(-u "$MONGO_USER" -p "${MONGO_PASSWORD:-}" --authenticationDatabase admin)
    fi
    docker exec "$MONGO_CONTAINER" mongodump --quiet ${auth[@]+"${auth[@]}"} \
      --db "$db" --gzip --archive > "$out"
  else
    echo "error: mongodump is not installed and container '$MONGO_CONTAINER' is not running." >&2
    echo "       On a Mac: brew install mongodb-database-tools" >&2
    return 1
  fi
}

failed=0
for db in $DATABASES; do
  out="$target/$db.archive.gz"
  if dump "$db" "$out" && [[ -s "$out" ]]; then
    echo "backed up $db ($(du -h "$out" | cut -f1))"
  else
    echo "error: backup of $db failed or is empty" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  # Keep the partial folder for inspection, but never let it count as a good
  # backup that pushes an older complete one out of the rotation.
  mv "$target" "$target.FAILED"
  exit 1
fi

# Rotate: keep the newest $KEEP complete backups. Names sort by time.
complete=()
while IFS= read -r dir; do complete+=("$dir"); done < <(
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'f1-*' ! -name '*.FAILED' | sort
)
excess=$(( ${#complete[@]} - KEEP ))
if (( excess > 0 )); then
  for dir in "${complete[@]:0:excess}"; do
    rm -rf -- "$dir"
    echo "removed old backup $(basename "$dir")"
  done
fi

echo "backup complete: $target"
