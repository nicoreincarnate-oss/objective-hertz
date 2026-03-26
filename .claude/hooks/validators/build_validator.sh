#!/bin/bash
# Post-tool-use validator: runs ruff + compile check after Python file changes
set -euo pipefail

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/build_validator_$(date +%Y%m%d_%H%M%S).log"

echo "Build Validator Running..." | tee "$LOG_FILE"

ERRORS=0

# Python project — run ruff
if command -v ruff &>/dev/null; then
  echo "Running ruff..." | tee -a "$LOG_FILE"
  if ! ruff check shared perseus titan hermes clawdbot 2>>"$LOG_FILE"; then
    echo "FAIL: Ruff errors found" | tee -a "$LOG_FILE"
    ERRORS=$((ERRORS + 1))
  else
    echo "PASS: Ruff" | tee -a "$LOG_FILE"
  fi
fi

echo "" | tee -a "$LOG_FILE"
if [ $ERRORS -gt 0 ]; then
  echo "VALIDATION FAILED: $ERRORS issues found. See $LOG_FILE for details." | tee -a "$LOG_FILE"
  echo "Resolve these errors before continuing."
  exit 1
else
  echo "VALIDATION PASSED: All checks clean." | tee -a "$LOG_FILE"
  exit 0
fi
