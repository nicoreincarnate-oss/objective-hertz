---
description: Write tests for new or changed Perseus code
---

# Test Writer Agent

## Purpose
Write comprehensive pytest tests for code that was recently built or modified.

## Instructions
1. Identify changed files (via git diff or provided list)
2. For each changed file:
   a. Understand what the code does
   b. Identify edge cases
   c. Write pytest tests covering:
      - Happy path
      - Error cases
      - Edge cases (empty inputs, None, boundary values)
      - Async patterns (use pytest-asyncio if needed)
3. Run tests: `PYTHONPATH=. python3 -m pytest tests/ -v`
4. If tests fail due to code bugs (not test bugs), note the bug

## Test Standards
- Each test should test ONE thing
- Test names: `test_[what]_[condition]_[expected]`
- Use arrange-act-assert pattern
- Mock external deps (Stripe, Instantly, Ollama, Claude API)
- Use `unittest.mock.AsyncMock` for async functions
- Put tests in tests/ directory, named `test_[module].py`

## Output
- Test files written
- Test run results
- Any bugs discovered
