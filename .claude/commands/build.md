---
description: Execute implementation from an approved plan
---

# Build Command

## Purpose
Implement a feature or task based on an approved plan from specs/.

## Instructions
1. Read the most recent plan from specs/ (or specified plan)
2. Execute steps in order, respecting dependencies
3. After each major step, verify it works before proceeding
4. If a step fails, attempt to fix before moving on
5. Log progress and any deviations from plan

## Workflow
1. Load plan from specs/
2. For each step in the plan:
   a. Announce what you're building
   b. Implement the step
   c. Run relevant validation:
      - `ruff check` on modified Python files
      - `python3 -m py_compile` on each changed file
      - `PYTHONPATH=. python3 -m pytest tests/ -x -q` if tests affected
   d. If validation fails: fix and retry
   e. If validation passes: move to next step
3. After all steps: run `make quality` (full lint + typecheck + tests)
4. Write build report to logs/build_[timestamp].md

## Rules
- Follow the plan — don't improvise unless blocked
- If blocked, document the blocker and ask for guidance
- Commit logical units of work (don't batch everything)
- Run tests after each significant change
- NEVER modify .env files — flag missing env vars instead
