#!/bin/bash
# License audit: verify zero HyperAgents code in production paths.
#
# HyperAgents is CC BY-NC-SA 4.0 licensed -- commercial use prohibited.
# All Thompson sampling code is clean-room from Sutton & Barto (2018).
#
# Run: bash scripts/license-audit.sh
# Exit code: 0 = clean, 1 = violation found

set -euo pipefail

PROD_DIRS="titan shared conway hermes clawdbot perseus openjarvis"
EXIT_CODE=0

echo "=== License Audit: HyperAgents Compliance ==="
echo ""

# Check 1: No HyperAgents references in production Python code
# Exclude compliance comments that explicitly state "zero HyperAgents" / "no HyperAgents"
echo "Scanning for HyperAgents references in production code..."
MATCHES=$(grep -ri "hyperagent" --include="*.py" $PROD_DIRS 2>/dev/null | grep -iv "zero hyperagent\|no hyperagent\|not.*hyperagent" || true)
if [ -n "$MATCHES" ]; then
    echo "$MATCHES"
    echo "FAIL: HyperAgents references found in production code"
    EXIT_CODE=1
else
    echo "CLEAN: No HyperAgents code found (compliance comments excluded)"
fi

echo ""

# Check 2: No HyperAgents paper references in production code
echo "Scanning for HyperAgents paper references..."
if grep -ri "arXiv:2603.19461" --include="*.py" $PROD_DIRS 2>/dev/null; then
    echo "FAIL: HyperAgents paper references found in production code"
    EXIT_CODE=1
else
    echo "CLEAN: No HyperAgents paper refs in code"
fi

echo ""

# Check 3: Verify clean-room attribution exists
echo "Verifying clean-room attribution in adaptive_thresholds.py..."
if grep -q "Sutton & Barto" titan/adaptive_thresholds.py 2>/dev/null; then
    echo "CLEAN: Clean-room Sutton & Barto attribution present"
else
    echo "FAIL: Missing clean-room attribution"
    EXIT_CODE=1
fi

# Check 4: No HyperAgents-specific identifiers in production code
echo "Scanning for HyperAgents-specific identifiers..."
# These identifiers are unique to the HyperAgents codebase
HA_IDENTIFIERS="HyperAgentSwarm\|hyper_agent_config\|HyperAgentPolicy"
IDENT_MATCHES=$(grep -ri "$HA_IDENTIFIERS" --include="*.py" $PROD_DIRS 2>/dev/null || true)
if [ -n "$IDENT_MATCHES" ]; then
    echo "$IDENT_MATCHES"
    echo "FAIL: HyperAgents-specific identifiers found in production code"
    EXIT_CODE=1
else
    echo "CLEAN: No HyperAgents-specific identifiers found"
fi

echo ""
echo "=== License audit complete (exit code: $EXIT_CODE) ==="
exit $EXIT_CODE
