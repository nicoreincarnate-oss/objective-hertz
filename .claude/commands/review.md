---
description: Review all current changes for bugs, security, and quality
---

# Review Command

## Purpose
Comprehensive review of all staged and unstaged changes in Perseus.

## Instructions
1. Run git diff to see all changes
2. Review each file for:
   - Logic errors and bugs
   - Security vulnerabilities (especially around payments, auth, email)
   - Performance issues (database queries, async patterns)
   - Missing error handling
   - Code style violations (PEP 8, ruff rules)
   - Missing tests
3. Perseus-specific checks:
   - State machine transitions (titan/state_machine.py)
   - Budget guard compliance (tools/budget_guard.py)
   - Deliverability rules (titan/deliverability.py)
   - Inter-daemon communication (shared/comms.py)
   - Safety checks (clawdbot/safety.py)

## Workflow
1. Get list of all changed files
2. Review each file systematically
3. Write findings to app_reviews/review_[timestamp].md
4. Categorize findings:
   - CRITICAL: Must fix before merge (security, data loss, payment bugs)
   - WARNING: Should fix, not blocking
   - INFO: Suggestion for improvement
5. Present summary with counts per category

## Rules
- Be thorough — check every changed file
- Don't auto-fix during review — report only
- Always check for credential leaks
- Flag any changes to payment or email flows as requiring manual review
