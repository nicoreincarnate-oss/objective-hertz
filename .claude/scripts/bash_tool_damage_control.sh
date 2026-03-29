#!/bin/bash
# Damage Control: Pre-tool-use hook for bash commands
# Reads patterns.yaml and blocks/asks for dangerous commands
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATTERNS_FILE="$SCRIPT_DIR/../hooks/patterns.yaml"
COMMAND="$1"

if [ ! -f "$PATTERNS_FILE" ]; then
  echo "WARNING: patterns.yaml not found at $PATTERNS_FILE"
  exit 0
fi

# Check blocked commands
check_pattern() {
  local pattern="$1"
  local action="$2"

  if echo "$COMMAND" | grep -qiE "$pattern"; then
    if [ "$action" = "block" ]; then
      echo "BLOCKED: Command matches dangerous pattern: $pattern"
      echo "Command: $COMMAND"
      echo "This command has been blocked by damage control."
      exit 1
    elif [ "$action" = "ask" ]; then
      echo "WARNING: Command matches sensitive pattern: $pattern"
      echo "Command: $COMMAND"
      echo "Please confirm you want to run this command."
      exit 2
    fi
  fi
}

# Parse patterns and check
while IFS= read -r line; do
  if echo "$line" | grep -q 'pattern:'; then
    pattern=$(echo "$line" | sed 's/.*pattern: *"\{0,1\}\(.*\)"\{0,1\}/\1/' | sed 's/^"//' | sed 's/"$//')
    read -r action_line
    action=$(echo "$action_line" | sed 's/.*action: *//')
    check_pattern "$pattern" "$action"
  fi
done < "$PATTERNS_FILE"

exit 0
