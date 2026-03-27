---
description: CLI-teaching prompt for Perseus test operations
---

# Perseus Test CLI

## Purpose
Teach the agent to run Perseus tests efficiently.

## Commands
```bash
# Full test suite
PYTHONPATH=. python3 -m pytest tests/ -v

# Quick run (stop on first failure)
PYTHONPATH=. python3 -m pytest tests/ -x -q

# Specific test file
PYTHONPATH=. python3 -m pytest tests/test_compliance.py -v

# Tests by name pattern
PYTHONPATH=. python3 -m pytest tests/ -k "budget" -v

# Ignore openjarvis tests
PYTHONPATH=. python3 -m pytest tests/ -v --ignore=tests/openjarvis

# With coverage
PYTHONPATH=. python3 -m pytest tests/ --cov=perseus --cov=titan --cov=hermes --cov=clawdbot

# Ruff linting
ruff check shared perseus titan hermes clawdbot tests

# Ruff auto-fix
ruff check --fix shared perseus titan hermes clawdbot

# Type checking
python3 -m mypy shared perseus titan hermes clawdbot

# Compile check single file
python3 -m py_compile path/to/file.py

# Full quality gate
make quality
```

## Patterns
- Run specific failing test first (faster feedback)
- Use -x to stop on first failure when debugging
- Use -q for quiet output during batch runs
- Run full suite before committing
- Always use PYTHONPATH=. prefix
