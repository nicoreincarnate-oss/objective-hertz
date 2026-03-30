---
phase: 00A-p0-bug-fixes-skill-hardening
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/conway/test_wallet_roundtrip.py
  - tests/conway/test_keystore_security.py
  - tests/conway/test_survival_jsonb.py
  - tests/conway/__init__.py
autonomous: true
requirements: [FIX-01, FIX-02, FIX-03]

must_haves:
  truths:
    - "Wallet INSERT column order matches conway_wallets schema (agent_name, chain, public_address, keystore_ref)"
    - "Keystore encryption uses env-var password, never agent name"
    - "Empty CONWAY_KEYSTORE_PASSWORD raises RuntimeError"
    - "Survival tier JSONB writes use Jsonb() wrapper when psycopg available"
    - "Survival tier value round-trips correctly through system_config"
  artifacts:
    - path: "tests/conway/test_wallet_roundtrip.py"
      provides: "FIX-01 regression tests"
      contains: "test_insert_column_order"
    - path: "tests/conway/test_keystore_security.py"
      provides: "FIX-02 regression tests"
      contains: "test_encrypt_uses_env_password"
    - path: "tests/conway/test_survival_jsonb.py"
      provides: "FIX-03 regression tests"
      contains: "test_jsonb_wrapper_used"
  key_links:
    - from: "tests/conway/test_wallet_roundtrip.py"
      to: "conway/wallet.py"
      via: "imports WalletManager, mocks shared.db"
      pattern: "from conway.wallet import"
    - from: "tests/conway/test_keystore_security.py"
      to: "conway/wallet.py"
      via: "imports _get_keystore_password, mocks Account"
      pattern: "_get_keystore_password"
    - from: "tests/conway/test_survival_jsonb.py"
      to: "conway/survival.py"
      via: "imports SurvivalMonitor, mocks shared.db.execute"
      pattern: "from conway.survival import"
---

<objective>
Verify that the three Conway P0 bugs (FIX-01 column order, FIX-02 keystore password, FIX-03 JSONB writes) are actually fixed with comprehensive regression tests. The March 2026 audit flagged these as "appears fixed" but no tests confirm the fixes.

Purpose: These bugs affect real USDC wallets on Base L2. Tests prove the fixes work and prevent regression.
Output: Three test files covering wallet column order, keystore password enforcement, and JSONB write correctness.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@conway/wallet.py
@conway/survival.py
@scripts/migrations/007-conway-tables.sql
@tests/conftest.py

<interfaces>
<!-- Key types and contracts the executor needs. -->

From conway/wallet.py:
```python
class AgentWallet:
    def __init__(self, agent_name: str, address: str, private_key: str): ...
    @property
    def address(self) -> str: ...
    async def get_balance(self) -> Decimal: ...

class WalletManager:
    def __init__(self, keystore_dir: str | None = None): ...
    async def get_or_create_wallet(self, agent_name: str) -> AgentWallet: ...
    def _create_new_wallet(self, agent_name: str) -> AgentWallet: ...
    def _load_from_keystore(self, agent_name: str, address: str, keystore_ref: str) -> AgentWallet: ...

def _get_keystore_password() -> str: ...  # raises RuntimeError if CONWAY_KEYSTORE_PASSWORD empty
```

From conway/survival.py:
```python
class SurvivalMonitor:
    def __init__(self, wallet_manager: WalletManager, ledger: EconomicLedger): ...
    async def check_and_enforce(self, agent_name: str) -> dict[str, Any]: ...
    async def _apply_tier_change(self, agent_name: str, old_tier: str, new_tier: str) -> None: ...
```

From shared/db.py:
```python
async def execute(query: str, params: tuple = ()) -> None: ...
async def fetch_one(query: str, params: tuple = ()) -> dict[str, Any] | None: ...
async def set_config(key: str, value: Any) -> None: ...
```

conway_wallets schema (007-conway-tables.sql):
```sql
CREATE TABLE IF NOT EXISTS conway_wallets (
    agent_name  TEXT PRIMARY KEY,
    chain       TEXT NOT NULL DEFAULT 'base',
    public_address TEXT NOT NULL,
    keystore_ref   TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

system_config schema (init-db.sql):
```sql
CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    is_customized BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT NOW()
);
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Conway wallet round-trip and keystore security tests (FIX-01 + FIX-02)</name>
  <files>tests/conway/__init__.py, tests/conway/test_wallet_roundtrip.py, tests/conway/test_keystore_security.py</files>
  <read_first>
    - conway/wallet.py (lines 170-278 — get_or_create_wallet, _create_new_wallet, _load_from_keystore, _get_keystore_password)
    - scripts/migrations/007-conway-tables.sql (conway_wallets schema — column order is agent_name, chain, public_address, keystore_ref)
    - tests/conftest.py (FakeDB pattern for mocking shared.db)
  </read_first>
  <action>
Create `tests/conway/__init__.py` (empty file).

Create `tests/conway/test_wallet_roundtrip.py` with these tests:

1. `test_insert_column_order` — Mock `shared.db.execute` and `shared.db.fetch_one` (return None on first call so creation path triggers). Call `await WalletManager(tmp_path).get_or_create_wallet("titan")`. Capture the args passed to `execute`. Assert the SQL contains `INSERT INTO conway_wallets (agent_name, chain, public_address, keystore_ref)` and the params tuple is `("titan", "base", <address>, "titan.json")` in that exact order. Mock `eth_account.Account.create()` to return a fake account with `.address = "0x" + "a" * 40` and `.key.hex() = "0x" + "b" * 64`. Mock `eth_account.Account.encrypt()` to return `{"version": 3}`.

2. `test_get_or_create_idempotent` — First call creates (fetch_one returns None), second call returns cached (no second execute call). Verify `execute` called exactly once.

3. `test_wallet_address_format` — Create wallet, assert `wallet.address` starts with "0x" and has length 42.

Create `tests/conway/test_keystore_security.py` with these tests:

1. `test_get_keystore_password_raises_when_unset` — Patch `os.environ` to not contain `CONWAY_KEYSTORE_PASSWORD`. Call `_get_keystore_password()`. Assert raises `RuntimeError` with message containing "CONWAY_KEYSTORE_PASSWORD".

2. `test_get_keystore_password_raises_when_empty` — Set `CONWAY_KEYSTORE_PASSWORD=""`. Assert raises `RuntimeError`.

3. `test_get_keystore_password_returns_value` — Set `CONWAY_KEYSTORE_PASSWORD="strong-secret-123"`. Assert returns `"strong-secret-123"`.

4. `test_encrypt_uses_env_password` — Set `CONWAY_KEYSTORE_PASSWORD="test-pw-456"`. Mock `eth_account.Account.create()` and `eth_account.Account.encrypt()`. Call `WalletManager(tmp_path)._create_new_wallet("hermes")`. Assert `Account.encrypt` was called with the private key and `"test-pw-456"` (not `"hermes"`).

5. `test_decrypt_uses_env_password` — Set `CONWAY_KEYSTORE_PASSWORD="test-pw-789"`. Write a fake keystore JSON to `tmp_path/hermes.json`. Mock `Account.decrypt()` to return `bytes.fromhex("bb" * 32)`. Call `WalletManager(tmp_path)._load_from_keystore("hermes", "0x" + "aa" * 20, "hermes.json")`. Assert `Account.decrypt` was called with the keystore dict and `"test-pw-789"` (not `"hermes"`).

All tests use `pytest.mark.asyncio` for async tests. Use `unittest.mock.patch` and `unittest.mock.AsyncMock` for mocking. Set env vars via `monkeypatch.setenv` / `monkeypatch.delenv`.
  </action>
  <verify>
    <automated>cd /Users/majovega/Desktop/Projects/objective-hertz && PYTHONPATH=. python3 -m pytest tests/conway/test_wallet_roundtrip.py tests/conway/test_keystore_security.py -v 2>&1 | tail -30</automated>
  </verify>
  <acceptance_criteria>
    - grep -c "def test_" tests/conway/test_wallet_roundtrip.py returns 3
    - grep -c "def test_" tests/conway/test_keystore_security.py returns 5
    - grep "agent_name.*chain.*public_address.*keystore_ref" tests/conway/test_wallet_roundtrip.py finds the column order assertion
    - grep "CONWAY_KEYSTORE_PASSWORD" tests/conway/test_keystore_security.py returns multiple matches
    - grep -c "Account.encrypt" tests/conway/test_keystore_security.py returns at least 1 (verifying encrypt call uses env password)
    - grep -c "Account.decrypt" tests/conway/test_keystore_security.py returns at least 1
    - PYTHONPATH=. python3 -m pytest tests/conway/test_wallet_roundtrip.py tests/conway/test_keystore_security.py -v exits 0
  </acceptance_criteria>
  <done>8 tests pass proving: wallet INSERT column order matches schema, keystore password comes from env var (not agent name), empty/missing env var raises RuntimeError, encrypt and decrypt both use env-var password.</done>
</task>

<task type="auto">
  <name>Task 2: Survival tier JSONB write tests (FIX-03)</name>
  <files>tests/conway/test_survival_jsonb.py</files>
  <read_first>
    - conway/survival.py (lines 137-167 — _apply_tier_change method, especially lines 149-154 where Jsonb wrapper is used)
    - conway/survival.py (lines 20-25 — Jsonb import with try/except fallback)
    - scripts/init-db.sql (system_config table — value column is JSONB NOT NULL)
    - tests/conftest.py (FakeDB pattern)
  </read_first>
  <action>
Create `tests/conway/test_survival_jsonb.py` with these tests:

1. `test_jsonb_wrapper_used` — Patch `conway.survival._JsonbType` to a mock class (simulating psycopg available). Patch `shared.db.execute` with `AsyncMock`. Patch `shared.db.set_config` with `AsyncMock`. Create a `SurvivalMonitor` with mocked wallet_manager (whose `get_or_create_wallet` returns a wallet with `get_balance` returning `Decimal("1.0")` — which maps to `low_compute` tier). Call `await monitor.check_and_enforce("titan")`. Capture the args to the first `execute` call (the INSERT INTO system_config). Assert the second parameter in the params tuple was wrapped by the mock `_JsonbType` class (i.e., `_JsonbType` was called with `"low_compute"`).

2. `test_jsonb_fallback_when_psycopg_unavailable` — Patch `conway.survival._JsonbType` to `None`. Same setup as above. Assert the second parameter in the params tuple is the raw string `"low_compute"` (not wrapped).

3. `test_tier_value_correct_for_each_balance` — Parametrize with `[(Decimal("15.0"), "normal"), (Decimal("5.0"), "low_compute"), (Decimal("0.3"), "critical"), (Decimal("0"), "dead")]`. For each, mock wallet balance, call `check_and_enforce`, assert `result["tier"]` matches expected tier name.

4. `test_tier_change_emits_event` — Mock execute, trigger a tier change (first call = unknown -> new tier). Assert the second execute call (INSERT INTO events) contains `'survival_tier_change'` in args.

5. `test_critical_tier_triggers_alert` — Patch `shared.comms.send_alert` with `AsyncMock`. Set balance to `Decimal("0.3")` (critical). Call `check_and_enforce`. Assert `send_alert` was called with a message containing "CRITICAL" and "balance below".

Use `pytest.mark.asyncio`, `unittest.mock.patch`, `unittest.mock.AsyncMock`, `unittest.mock.MagicMock`. Mock the `WalletManager` and `EconomicLedger` — do not import real ones.
  </action>
  <verify>
    <automated>cd /Users/majovega/Desktop/Projects/objective-hertz && PYTHONPATH=. python3 -m pytest tests/conway/test_survival_jsonb.py -v 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - grep -c "def test_" tests/conway/test_survival_jsonb.py returns at least 5 (with parametrize expanding to more)
    - grep "_JsonbType" tests/conway/test_survival_jsonb.py returns multiple matches (testing both wrapper and fallback paths)
    - grep "survival_tier_change" tests/conway/test_survival_jsonb.py returns at least 1
    - grep "CRITICAL" tests/conway/test_survival_jsonb.py returns at least 1
    - PYTHONPATH=. python3 -m pytest tests/conway/test_survival_jsonb.py -v exits 0
  </acceptance_criteria>
  <done>5+ tests pass proving: JSONB wrapper is used when psycopg available, raw string fallback works when psycopg unavailable, tier calculation is correct for all balance ranges, tier changes emit events, critical tier triggers alert.</done>
</task>

</tasks>

<verification>
```bash
cd /Users/majovega/Desktop/Projects/objective-hertz
PYTHONPATH=. python3 -m pytest tests/conway/ -v
ruff check tests/conway/
```
All Conway bug regression tests pass. Ruff reports no lint errors.
</verification>

<success_criteria>
- All 13+ tests in tests/conway/ pass
- FIX-01: Test proves INSERT column order matches schema (agent_name, chain, public_address, keystore_ref)
- FIX-02: Tests prove keystore uses env-var password, never agent name, and empty password raises
- FIX-03: Tests prove JSONB wrapper used when available, fallback when not, and tier values round-trip correctly
- ruff check clean on all new test files
</success_criteria>

<output>
After completion, create `.planning/phases/00A-p0-bug-fixes-skill-hardening/00A-01-SUMMARY.md`
</output>
