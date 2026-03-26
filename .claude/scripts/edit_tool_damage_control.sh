#!/bin/bash
# Damage Control: Pre-tool-use hook for edit/write operations
# Protects Perseus configuration files and secrets
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERNS_FILE="$SCRIPT_DIR/../hooks/patterns.yaml"
TARGET_FILE="$1"

if [ ! -f "$PATTERNS_FILE" ]; then
  exit 0
fi

# Check zero access paths
for path in .ssh .env .env. secrets/ credentials/ "*.pem" "*.key" id_rsa id_ed25519; do
  if echo "$TARGET_FILE" | grep -q "$path"; then
    echo "BLOCKED: Cannot edit file in zero-access path: $path"
    echo "File: $TARGET_FILE"
    echo "This file contains sensitive data. Edit manually if needed."
    exit 1
  fi
done

# Check readonly paths
for path in ".claude/settings.local.json" ".claude/hooks/" ".claude/scripts/bash_tool_damage_control.sh" ".claude/scripts/edit_tool_damage_control.sh" "scripts/init-db.sql"; do
  if echo "$TARGET_FILE" | grep -q "$path"; then
    echo "BLOCKED: Cannot edit readonly file: $path"
    echo "File: $TARGET_FILE"
    exit 1
  fi
done

exit 0
