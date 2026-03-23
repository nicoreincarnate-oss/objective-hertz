#!/bin/bash
# Sync local Perseus soul + skills into the official Hermes Agent home.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
HERMES_HOME="${HOME}/.hermes"
SKILLS_SRC="${ROOT_DIR}/hermes/skills"
SKILLS_DEST="${HERMES_HOME}/skills"

mkdir -p "$SKILLS_DEST"

if [ -f "${ROOT_DIR}/soul/soul_hermes.md" ]; then
    cp "${ROOT_DIR}/soul/soul_hermes.md" "${HERMES_HOME}/SOUL.md"
    echo "  ✓ Synced Hermes soul"
fi

if [ -d "$SKILLS_SRC" ]; then
    for skill_dir in "$SKILLS_SRC"/*; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        rm -rf "${SKILLS_DEST}/${skill_name}"
        cp -R "$skill_dir" "${SKILLS_DEST}/${skill_name}"
        echo "  ✓ Synced skill: ${skill_name}"
    done
fi
