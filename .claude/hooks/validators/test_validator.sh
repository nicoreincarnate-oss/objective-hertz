#!/bin/bash
# Post-tool-use validator: runs pytest after significant changes
set -euo pipefail

LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/test_validator_$(date +%Y%m%d_%H%M%S).log"

echo "Test Validator Running..." | tee "$LOG_FILE"

# Perseus Python tests
if [ -d "tests" ]; then
  echo "Running pytest..." | tee -a "$LOG_FILE"
  if ! PYTHONPATH=. python3 -m pytest tests/ -q --ignore=tests/openjarvis 2>>"$LOG_FILE"; then
    echo "FAIL: Tests failed" | tee -a "$LOG_FILE"
    exit 1
  fi
  echo "PASS: All tests passed" | tee -a "$LOG_FILE"
fi

echo "Test validation complete." | tee -a "$LOG_FILE"
exit 0
