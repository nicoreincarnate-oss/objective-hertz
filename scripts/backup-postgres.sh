#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$ROOT_DIR/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/perseus-postgres-$TIMESTAMP.sql.gz"
CONTAINER_NAME="${POSTGRES_CONTAINER_NAME:-perseus-postgres}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
POSTGRES_USER="${POSTGRES_USER:-perseus}"
POSTGRES_DB="${POSTGRES_DB:-perseus}"

mkdir -p "$BACKUP_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for backups" >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "Postgres container '$CONTAINER_NAME' is not running" >&2
    exit 1
fi

docker exec "$CONTAINER_NAME" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$BACKUP_FILE"
find "$BACKUP_DIR" -type f -name 'perseus-postgres-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete

echo "Backup created: $BACKUP_FILE"
