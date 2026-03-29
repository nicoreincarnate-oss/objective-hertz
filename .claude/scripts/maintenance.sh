#!/bin/bash
# Perseus Automated Maintenance Script
# Run via: just maintain
set -euo pipefail

echo "========================================="
echo " PERSEUS MAINTENANCE — $(date)"
echo "========================================="
echo ""

ERRORS=0
WARNINGS=0

# 1. Check daemon status
echo "--- Daemon Status ---"
for daemon in perseus titan clawdbot; do
  if [ -f "logs/pids/$daemon.pid" ] && kill -0 $(cat "logs/pids/$daemon.pid") 2>/dev/null; then
    echo "  $daemon: RUNNING (PID: $(cat logs/pids/$daemon.pid))"
  else
    echo "  $daemon: STOPPED"
    WARNINGS=$((WARNINGS + 1))
  fi
done
echo ""

# 2. Check Docker services
echo "--- Docker Services ---"
for svc in postgres qdrant mem0; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "$svc"; then
    echo "  $svc: UP"
  else
    echo "  $svc: DOWN"
    WARNINGS=$((WARNINGS + 1))
  fi
done
echo ""

# 3. Check Python dependencies
echo "--- Dependencies ---"
if command -v pip-audit &>/dev/null; then
  echo "Running pip-audit..."
  pip-audit 2>/dev/null || { echo "WARNING: pip-audit found issues"; WARNINGS=$((WARNINGS + 1)); }
fi
echo ""

# 4. Code quality
echo "--- Code Quality ---"
echo "Running ruff..."
if command -v ruff &>/dev/null; then
  ruff check shared perseus titan hermes clawdbot 2>/dev/null || { echo "WARNING: ruff found issues"; WARNINGS=$((WARNINGS + 1)); }
fi
echo ""

echo "Checking for TODO/FIXME/HACK..."
TODO_COUNT=$(grep -rl "TODO\|FIXME\|HACK\|XXX" --include="*.py" perseus/ titan/ hermes/ clawdbot/ shared/ 2>/dev/null | wc -l || echo "0")
echo "Files with TODO/FIXME: $TODO_COUNT"
echo ""

# 5. Security check
echo "--- Security Check ---"
echo "Checking for leaked secrets..."
LEAK_PATTERNS='(password|secret|api_key|apikey|token|private_key)\s*=\s*["\x27][^"\x27]+'
if grep -rIl --include="*.py" -E "$LEAK_PATTERNS" perseus/ titan/ hermes/ clawdbot/ shared/ tools/ 2>/dev/null; then
  echo "WARNING: Potential secrets found in source files!"
  WARNINGS=$((WARNINGS + 1))
else
  echo "No leaked secrets detected."
fi
echo ""

# 6. Database check
echo "--- Database ---"
if docker exec perseus-postgres pg_isready 2>/dev/null; then
  echo "Postgres: OK"
  TASK_COUNT=$(docker exec perseus-postgres psql -U perseus -d perseus -t -c "SELECT count(*) FROM task_queue WHERE status='pending'" 2>/dev/null || echo "?")
  echo "Pending tasks: $TASK_COUNT"
else
  echo "Postgres: DOWN"
  WARNINGS=$((WARNINGS + 1))
fi
echo ""

# 7. Disk and logs
echo "--- Disk & Logs ---"
df -h / | tail -1
echo ""
echo "Log sizes:"
for f in logs/*.log; do
  if [ -f "$f" ]; then
    SIZE=$(du -h "$f" | cut -f1)
    echo "  $f: $SIZE"
    # Warn on large logs
    if [ "$(du -k "$f" | cut -f1)" -gt 10240 ]; then
      echo "  WARNING: Log file > 10MB"
      WARNINGS=$((WARNINGS + 1))
    fi
  fi
done
echo ""

# 8. Git status
echo "--- Git Status ---"
if git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "Branch: $(git branch --show-current)"
  UNCOMMITTED=$(git status --porcelain | wc -l)
  echo "Uncommitted changes: $UNCOMMITTED files"
fi
echo ""

# Summary
echo "========================================="
echo " MAINTENANCE SUMMARY"
echo "========================================="
echo "Errors:   $ERRORS"
echo "Warnings: $WARNINGS"
echo "Completed: $(date)"
echo "========================================="

exit $ERRORS
