---
name: run-tests
description: Run the Perseus test suite with quality checks
triggers:
  - run tests
  - check tests
  - test suite
  - verify tests pass
  - make quality
---

# Run Tests Skill (Perseus)

## When to Use
Agent should trigger this when:
- After building or modifying Python code
- Before committing changes
- When asked to verify something works
- During review process

## Instructions
1. Run ruff: `ruff check shared perseus titan hermes clawdbot tests`
2. Run tests: `PYTHONPATH=. python3 -m pytest tests/ -v --ignore=tests/openjarvis`
3. If failures:
   a. Parse error output
   b. Identify failing tests
   c. Report: test name, error message, file location
4. If pass: report count

## Output
- Ruff: clean / N issues
- Tests: Total / Pass / Fail / Skip
- Details on failures
