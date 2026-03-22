#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: ./scripts/restore-from-backup.sh /absolute/path/to/backup.sql.gz" >&2
    exit 1
fi

BACKUP_FILE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="${POSTGRES_CONTAINER_NAME:-perseus-postgres}"
POSTGRES_USER="${POSTGRES_USER:-perseus}"
POSTGRES_DB="${POSTGRES_DB:-perseus}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for restore" >&2
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    echo "Postgres container '$CONTAINER_NAME' is not running" >&2
    exit 1
fi

gzip -dc "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" psql -U "$POSTGRES_USER" "$POSTGRES_DB"

echo "Restore completed from: $BACKUP_FILE"
