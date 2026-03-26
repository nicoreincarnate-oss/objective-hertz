---
description: Systematically reproduce and document a bug in Perseus
---

# Reproduce Bug

## Purpose
Given a bug description, systematically reproduce it and document findings.

## Variables
- $BUG: Description of the bug

## Instructions
1. Read the bug description carefully
2. Identify which daemon(s) are affected
3. Identify the expected behavior vs actual behavior
4. Find the relevant code area
5. Attempt to reproduce:
   a. Check daemon status first
   b. Check relevant logs
   c. Set up the conditions described
   d. Execute the triggering action
   e. Observe the result
6. If reproduced:
   a. Document exact reproduction steps
   b. Identify root cause
   c. Suggest fix
7. If NOT reproduced:
   a. Document what was tried
   b. Ask for more information

## Output
Write findings to specs/bug_[name].md with:
- Daemon(s) affected
- Steps to reproduce
- Expected vs actual behavior
- Root cause analysis
- Suggested fix
- Affected files
