# Phase 1: Agent DNA

**Goal:** Universal engineering principles injected into every daemon's LLM calls via opt-in system parameter.
**Requirements:** DNA-01, DNA-02, DNA-03, DNA-04, DNA-05
**Depends on:** Phase 0a (signed manifests), Phase 0b (contracts)
**Feature flag:** `ENABLE_DNA_PROFILES`

---

## Context

Agent DNA provides a two-layer architecture: a universal engineering DNA document (shared principles) plus per-daemon DNA profiles (role boundaries, permitted tools, escalation triggers). DNA is injected as a system-prompt prefix on LLM calls via the existing `system` parameter in `shared/llm_client.py:67 generate()`.

The skill loader (hardened in Phase 0a) verifies signatures on DNA profile files. The `DNAProvider` Protocol (defined in Phase 0b) constrains the loader's interface.

### Current State

| Component | File | Status |
|-----------|------|--------|
| LLM generate() | `shared/llm_client.py:67-77` | Has `system` param — DNA prepends to this |
| Soul files | `soul/` | 4 existing files (soul_agent.md, soul_copy.md, soul_hermes.md, soul_values.md) |
| DNA profiles | `soul/dna/` | Does not exist |
| DNA loader | `shared/agent_dna.py` | Does not exist |
| Daemon LLM calls | All 5 daemon.py files | Call `llm.generate()` directly |

---

## Tasks

### Task 1: Universal engineering DNA document (DNA-01)
**File:** `soul/engineering_dna.md` (new)
**What:**
Create the universal DNA document with 7 principle categories:
1. **Security** — Never expose credentials, validate all inputs, principle of least privilege
2. **Compliance** — CAN-SPAM for emails, GDPR awareness, immutable audit trail
3. **Budget** — Respect $800/mo cap, auto-downgrade models when balance low, track per-call cost
4. **Quality** — No fabricated claims, no slop phrases, cite actual research data
5. **Resilience** — Retry with backoff, graceful degradation, never crash the daemon loop
6. **Decisions** — Log reasoning for non-obvious choices, escalate to operator when uncertain
7. **Learning** — Record outcomes for future improvement, flag patterns worth remembering

Format: Markdown, each principle is 2-3 sentences max. Total document under 500 tokens when rendered for LLM injection.

**Acceptance criteria:**
- [ ] `soul/engineering_dna.md` exists with 7 principle categories
- [ ] Token count under 500 (measured with tiktoken or Anthropic tokenizer)
- [ ] Signed with skill signer from Phase 0a

### Task 2: Per-daemon DNA profiles (DNA-02)
**Files:** `soul/dna/{daemon}.yaml` for each daemon (new directory + 5 files)
**What:**

Create YAML profiles for each daemon:

```yaml
# soul/dna/titan.yaml
name: titan
role: "Revenue pipeline engine"
boundaries:
  - "Never modify wallet balances directly"
  - "Never send emails without quality gate approval"
  - "Never fabricate company details or reviews"
permitted_tools:
  - firecrawl_scrape
  - email_compose
  - email_send
  - lead_discovery
  - qdrant_search
escalation_triggers:
  - "Email bounce rate exceeds 5%"
  - "Cost per email exceeds $0.10"
  - "Lead data seems fabricated"
memory_domains:
  - "titan_pipeline"
  - "titan_leads"
```

Create similar profiles for: `perseus`, `hermes`, `clawdbot`, `conway`

**Acceptance criteria:**
- [ ] 5 YAML profiles exist in `soul/dna/`
- [ ] Each has: name, role, boundaries, permitted_tools, escalation_triggers, memory_domains
- [ ] All signed with Phase 0a signer

### Task 3: DNA loader with opt-in injection (DNA-03)
**File:** `shared/agent_dna.py` (new)
**What:**

```python
class AgentDNA:
    """Loads and injects DNA into LLM calls. Implements DNAProvider Protocol."""

    def __init__(self, daemon_name: str):
        self._daemon = daemon_name
        self._universal_dna: str = ""
        self._profile: dict = {}
        self._loaded = False

    def load(self) -> None:
        """Load universal DNA + daemon-specific profile. Verify signatures."""
        # Load soul/engineering_dna.md (verify signature via skill_loader)
        # Load soul/dna/{daemon}.yaml (verify signature)
        # Parse YAML profile
        # Cache in memory

    def get_dna(self, daemon_name: str) -> str:
        """Return DNA string for injection into LLM system parameter."""
        # Combine universal DNA + profile boundaries
        # Truncate to 500-token cap
        # Return formatted string

    def get_token_budget(self) -> int:
        return 500

    def get_boundaries(self) -> list[str]:
        """Return role boundaries for this daemon."""

    def check_action(self, action: str, tool: str) -> bool:
        """Check if an action/tool is permitted by this daemon's DNA."""
```

- Token cap: 500 tokens max for combined DNA injection
- Injection scanner: Run content through `openjarvis/security/injection_scanner.py` before injecting
- Implements `DNAProvider` Protocol from `shared/contracts.py`

**Acceptance criteria:**
- [ ] `AgentDNA` implements `DNAProvider` Protocol (passes `isinstance` check)
- [ ] DNA content capped at 500 tokens
- [ ] Injection scanner validates DNA content before use
- [ ] Unsigned DNA files are rejected (delegated to skill_loader)

### Task 4: Circuit breaker (DNA-04)
**File:** `shared/agent_dna.py` (extend)
**What:**

Add circuit breaker that disables DNA injection if error rates spike:

```python
class DNACircuitBreaker:
    """Disables DNA if error rate exceeds baseline + 10%."""

    def __init__(self, baseline_error_rate: float = 0.05):
        self._baseline = baseline_error_rate
        self._window: deque[bool] = deque(maxlen=100)  # last 100 calls
        self._tripped = False

    def record(self, success: bool) -> None:
        self._window.append(success)
        error_rate = 1 - (sum(self._window) / len(self._window))
        if error_rate > self._baseline + 0.10:
            self._tripped = True
            logger.warning(f"DNA circuit breaker TRIPPED: error rate {error_rate:.1%}")

    def is_open(self) -> bool:
        return self._tripped

    def reset(self) -> None:
        self._tripped = False
        self._window.clear()
```

- Tracks last 100 LLM call outcomes
- Trips if error rate exceeds baseline + 10%
- When tripped: DNA injection silently skipped, calls proceed without DNA
- Auto-reset after 50 consecutive successes

**Acceptance criteria:**
- [ ] Circuit breaker trips at baseline + 10% error rate
- [ ] DNA injection stops when breaker is open
- [ ] Auto-reset after 50 consecutive successes
- [ ] Breaker state logged to observability metrics (Phase 0b)

### Task 5: Inject DNA into all 5 daemons (DNA-05)
**Files:** `shared/llm_client.py`, all 5 daemon.py files
**What:**

#### 5a. Add `use_dna` parameter to `generate()`
- Add optional `use_dna: bool = False` and `daemon_name: str = ""` parameters
- When `use_dna=True` and `ENABLE_DNA_PROFILES` feature flag is on:
  - Load DNA via `AgentDNA(daemon_name).get_dna(daemon_name)`
  - Prepend to `system` parameter: `f"{dna_text}\n\n---\n\n{system}"`
  - Check circuit breaker before injecting
- When flag is off or `use_dna=False`: no change to behavior

#### 5b. Update daemon LLM calls
- Each daemon's main LLM calls get `use_dna=True, daemon_name="titan"` (etc.)
- Not every LLM call needs DNA — only the main reasoning/generation calls
- Simple formatting or parsing calls remain without DNA

**Acceptance criteria:**
- [ ] All 5 daemons include DNA in LLM calls when `ENABLE_DNA_PROFILES=true`
- [ ] DNA profiles validated against `DNAProvider` contract
- [ ] Behavioral boundary test: Titan with DNA refuses wallet modification prompt
- [ ] Feature flag disabled by default — existing behavior unchanged

### Task 6: Tests
**Files:** `tests/shared/test_agent_dna.py` (new)
**What:**
- Test DNA loading and token cap enforcement
- Test circuit breaker trip/reset logic
- Test DNA injection in generate() with feature flag on/off
- Test boundary check: Titan DNA rejects non-permitted tool
- Test injection scanner catches malicious DNA content
- Test `isinstance(AgentDNA("titan"), DNAProvider)` passes

**Acceptance criteria:**
- [ ] `PYTHONPATH=. pytest tests/shared/test_agent_dna.py -v` passes
- [ ] `ruff check shared/agent_dna.py soul/` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] All 5 daemons include DNA in LLM calls when flag enabled
- [ ] DNA profiles validated against contract interface
- [ ] Circuit breaker disables DNA if error rate exceeds baseline + 10%
- [ ] Behavioral boundary test: Titan with DNA refuses wallet modification prompt

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| DNA adds latency to every LLM call | Low | Medium | DNA loaded once at startup, cached in memory |
| DNA content confuses LLM reasoning | Medium | Medium | Circuit breaker auto-disables if errors spike |
| 500-token cap too restrictive | Low | Low | Adjustable per profile; monitor via observability |

---

*Plan created: 2026-03-29*
