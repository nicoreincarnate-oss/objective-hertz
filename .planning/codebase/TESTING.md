# Testing Patterns

**Analysis Date:** 2026-03-29

## Test Framework & Config

**Runner:**
- pytest >= 8 with pytest-asyncio >= 0.24
- Config: `pyproject.toml` `[tool.pytest.ini_options]`

**Configuration:**
```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "live: requires running inference engine",
    "cloud: requires cloud API keys",
    "nvidia: requires NVIDIA GPU",
    "apple: requires Apple Silicon",
    "slow: long-running test",
]
```

**Assertion Library:** Built-in `assert` statements (no third-party assertion library).

**Run Commands:**
```bash
PYTHONPATH=. python3 -m pytest tests/ -v          # Run all tests
PYTHONPATH=. python3 -m pytest tests/ -v --cov    # With coverage
PYTHONPATH=. python3 -m pytest tests/test_X.py -v # Single file
ruff check .                                       # Lint check
```

**Additional dev tools:**
- `ruff >= 0.4` for linting (rules: E, F, I, B, UP; ignores E501, E741, UP042)
- `mypy` configured for `shared`, `titan`, `hermes`, `clawdbot`, `tools`, `conway`, `perseus` (excludes openjarvis)
- `respx >= 0.22` available for HTTP mocking (listed in dev deps)
- `pre-commit >= 3.0` listed in dev deps

## Test File Organization

**Location:** All tests in `tests/` directory at repo root. Two sub-trees:
- `tests/*.py` — 80+ files for Perseus/Titan/Hermes/Conway daemon tests
- `tests/openjarvis/` — 332 files organized by openjarvis submodule (agents, channels, cli, core, etc.)
- `tests/helpers/` — Shared test helper modules (moved from `shared/`)

**Naming:**
- Files: `test_<feature>.py` (e.g., `test_e2e_pipeline.py`, `test_shadow_mode.py`)
- Functions: `test_<what_it_tests>()` (e.g., `test_email_send_checks_shadow_mode()`)
- Classes: `Test<Component>` used in openjarvis tests (e.g., `TestSimpleAgent`)

**Total test files:** ~412 (80 daemon + 332 openjarvis)
**Total lines (daemon tests):** ~12,043

## Conftest & Fixtures

**Root conftest:** `tests/conftest.py`

**Key fixtures:**
```python
@pytest.fixture
def fake_db():
    """Provide a fresh in-memory DB (FakeDB class) for each test."""
    return FakeDB()

@pytest.fixture
def patch_db(fake_db):
    """Patch shared.db module with fake_db methods using unittest.mock.patch."""
    # Patches: execute, fetch_one, fetch_all, fetch_val, insert_task,
    #          emit_event, get_config, set_config, init_pool, close_pool
    yield fake_db
```

**Module isolation guard (critical):**
The conftest implements `pytest_collectstart`/`pytest_collectreport` and `pytest_runtest_setup`/`pytest_runtest_teardown` hooks to save and restore `sys.modules` for a list of protected modules. This prevents `sys.modules` injection in one test file from poisoning subsequent test files.

Protected modules include: `titan.*`, `shared.*`, `hermes.*`, `multipart.*`.

## Test Types

### 1. Structural/String-Matching Tests (most common in daemon tests)
Tests that read source files as text and assert patterns exist:
```python
# tests/test_shadow_mode.py:10-14
def test_email_send_checks_shadow_mode():
    code = Path("titan/pipeline/email_send.py").read_text()
    assert "shadow_mode" in code, "email_send.py missing shadow_mode check"
    assert "SHADOW:" in code, "email_send.py missing SHADOW log prefix"
    assert "shadow_email_send" in code, "email_send.py missing shadow_email_send event"
```
These verify code structure (feature flags present, guard clauses exist) without executing the code. Fast but brittle to refactoring.

### 2. Unit Tests with sys.modules Injection
The dominant pattern for testing daemon code that depends on DB/LLM:
```python
# tests/test_scout.py:10-38
_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.get_config = AsyncMock(return_value={})
# ... more attrs ...
sys.modules.setdefault("shared.db", _fake_db)
sys.modules.setdefault("shared.llm_client", _fake_llm_mod)

from perseus.scout import _select_topics_for_cycle  # Import AFTER faking
```

### 3. Integration Tests (e2e pipeline)
`tests/test_e2e_pipeline.py` (657 lines) traces a lead through all 10 Titan stages with comprehensive mocking. Creates a full `_make_fake_db()` with query tracking:
```python
def _make_fake_db():
    mod = types.ModuleType("shared.db")
    mod._queries = []  # Tracks all (operation, query, params) tuples
    mod._rows = {}     # Configurable return values keyed by query substring
    mod._config = {}   # system_config key-value store
```

### 4. OpenJarvis Unit Tests (class-based)
```python
# tests/openjarvis/agents/test_simple.py
class TestSimpleAgent:
    def test_basic_run(self):
        engine = _make_mock_engine()
        agent = SimpleAgent(engine, "test-model")
        result = agent.run("Hello")
        assert isinstance(result, AgentResult)
```
These follow standard pytest class grouping, use `MagicMock` for engine dependencies.

### 5. Compliance/Security Tests
`tests/test_compliance.py` (357 lines) — tests email compliance gates, unsubscribe links, CAN-SPAM/GDPR requirements. Uses module reloading pattern:
```python
def load_compliance_module():
    sys.modules.pop("titan.compliance", None)
    sys.modules["shared.db"] = fake_db
    module = importlib.import_module("titan.compliance")
    return module
```

## Mock Patterns

**Primary mocking approach — sys.modules replacement:**
```python
_fake_db = types.ModuleType("shared.db")
_fake_db.execute = AsyncMock()
_fake_db.fetch_one = AsyncMock(return_value=None)
sys.modules["shared.db"] = _fake_db
```
This is used in ~47 test files with 284 total `sys.modules` injections. It works because imports at module level in production code resolve against `sys.modules`.

**Secondary — unittest.mock.patch (preferred for new code):**
```python
# tests/conftest.py patch_db fixture
with patch("shared.db.execute", side_effect=fake_db.execute), \
     patch("shared.db.fetch_one", side_effect=fake_db.fetch_one):
    yield fake_db
```

**AsyncMock usage:**
All DB and LLM mocks use `unittest.mock.AsyncMock` since the production code is async.

**What gets mocked (always):**
- `shared.db` — all database operations
- `shared.llm_client` — LLM calls (via `llm.generate`)
- `shared.comms` — A2A communication, event bus
- `shared.config` — configuration (when specific values needed)
- `tools.firecrawl_client` — web scraping
- `tools.instantly_client` — email sending API

**What is NOT mocked:**
- Pure logic functions (validators, parsers, state machine transitions)
- File I/O for skill loading (uses real filesystem paths)
- JSON parsing logic

## Fixtures and Factories

**FakeDB (conftest):**
In-memory database with tables dict, auto-incrementing IDs, dedupe support on `insert_task`. Provides the most commonly needed DB operations.

**Test data construction:**
Tests build lead/client dicts inline:
```python
lead = {
    "id": 1,
    "business_name": "Test Dental",
    "contact_name": "Dr. Smith",
    "email": "test@example.com",
    "industry": "dental",
    "lead_score": 80,
    "language": "en",
    "country": "US",
    "city": "Austin",
    "research_summary": "Local dentist without website",
}
```
No shared factory functions or fixtures for test data — each test file constructs its own.

**Fixture location:** `tests/conftest.py` (root), `tests/openjarvis/conftest.py` (openjarvis sub-tree).

## Test Coverage by Module

| Module | Test Files | Coverage Level | Notes |
|--------|-----------|---------------|-------|
| **titan/pipeline/** | test_e2e_pipeline, test_shadow_mode, test_email_*, test_build_site_gate, test_close_deal_demo_gate, test_follow_up_*, test_lead_research_structured, test_discovery_source_selection | High | Most pipeline stages tested via e2e + individual |
| **titan/** (other) | test_state_machine, test_titan_memory, test_training, test_negotiation (via revenue_expansion) | Medium | State machine and memory well-tested |
| **shared/** | test_budget_gate, test_llm_local_routing, test_magma, test_schema_guards, test_system_config_seeds | Medium | Core shared modules covered |
| **perseus/** | test_scout, test_self_audit, test_sleep_cycle, test_backprop, test_health_and_cleanup | Medium | Key Perseus features tested |
| **hermes/** | test_hermes_dashboard, test_hermes_channel_delivery, test_hermes_health_api, test_hermes_insights, test_hermes_operator_chat, test_hermes_review_a2a | Medium | Dashboard and alert paths covered |
| **conway/** | test_conway_runtime, test_conway_survival, test_payment_idempotency | Low-Medium | Wallet/x402 have known bugs per CONCERNS.md |
| **clawdbot/** | test_clawdbot_activation, test_clawdbot_runtime_capabilities, test_site_build_a2a, test_site_builder_brief, test_site_quality | Medium | Site builder and A2A tested |
| **openjarvis/** | 332 test files across agents, channels, cli, core, etc. | High | Comprehensive coverage of framework |
| **intel/** | None | Zero | No tests for intel directory |
| **tools/** | test_instantly_client, test_payment_idempotency | Low | Most tool clients untested |

## Test Gaps (Critical for Intel Integration)

**No tests for `intel/` directory:**
The `intel/` directory (agent-dna, ai-scout, anti-slop, deerflow, hyperagents, etc.) has zero test coverage. Any new intel integration features need tests from scratch.

**No tests for `shared/skill_loader.py` in isolation:**
Skill loading is tested only indirectly through pipeline tests. No unit tests for `find_skill()`, `load_skill()`, `execute_skill()`, or `execute_skill_or_fallback()`.

**No tests for `tools/firecrawl_client.py` behavior:**
Only mocked in other tests. No tests verifying the client itself handles errors, retries, rate limits.

**No tests for `tools/payment_router.py` routing logic:**
Only `test_payment_idempotency.py` tests payment paths. Routing between Stripe/Wise untested.

**Missing async integration tests:**
Most tests use `asyncio.run()` one-shot calls. No tests verify concurrent pipeline execution, connection pool behavior under load, or event loop interaction between daemons.

## Quality Gate Readiness

**Ruff config:**
```toml
[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = ["tools/browser-use", "tools/firecrawl"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]   # errors, pyflakes, isort, bugbear, pyupgrade
ignore = ["E501", "E741", "UP042"]      # line length, ambiguous var names, PEP 695

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["E402", "B011", "B017", "E741", "B007", "B905", "F841"]
```
Tests have relaxed rules: late imports (E402), assert usage (B011, B017), unused variables (F841).

**Mypy config:**
```toml
[tool.mypy]
python_version = "3.11"
packages = ["shared", "titan", "hermes", "clawdbot", "tools", "conway", "perseus"]
exclude = "(^tools/browser-use/|^tools/firecrawl/|^openjarvis/)"
ignore_missing_imports = true
```
Mypy covers daemon packages but excludes `openjarvis/` entirely (`ignore_errors = true` override).

**Pre-commit:** Listed in dev dependencies (`pre-commit >= 3.0`) but no `.pre-commit-config.yaml` found at repo root.

## Writing New Tests (Prescriptive Guide)

**For new intel integration features:**

1. Create test file at `tests/test_<feature>.py`
2. Use the sys.modules injection pattern for DB/LLM isolation:
```python
import sys
import types
from unittest.mock import AsyncMock

_fake_db = types.ModuleType("shared.db")
_fake_db.execute = AsyncMock()
_fake_db.fetch_one = AsyncMock(return_value=None)
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="{}"))

sys.modules["shared.db"] = _fake_db
sys.modules["shared.llm_client"] = _fake_llm

# NOW import the module under test
from mymodule import my_function
```

3. For async functions, use either pattern:
```python
# Pattern A: asyncio.run (matches existing codebase)
def test_my_feature():
    result = asyncio.run(my_async_function(args))
    assert result == expected

# Pattern B: async test (preferred for new code, works with asyncio_mode="auto")
async def test_my_feature():
    result = await my_async_function(args)
    assert result == expected
```

4. For structural/guard tests:
```python
def test_feature_has_error_handling():
    code = Path("path/to/module.py").read_text()
    assert "emit_pipeline_error" in code, "Module must emit pipeline errors"
```

5. Run with: `PYTHONPATH=. python3 -m pytest tests/test_<feature>.py -v`

---

*Testing analysis: 2026-03-29*
