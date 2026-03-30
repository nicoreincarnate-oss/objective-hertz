# Phase 7: Adaptive Thresholds (Self-Modifying Expansion)

**Goal:** Titan's expansion engine uses learnable thresholds that evolve from pipeline outcomes instead of hardcoded values.
**Requirements:** ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04, ADAPT-05, ADAPT-06, ADAPT-07, ADAPT-08
**Depends on:** Phase 4 (middleware telemetry for training data), Phase 2 (quality scores for evaluation)
**Feature flag:** `ENABLE_BANDIT_EXPANSION`

---

## Context

Titan's expansion engine (`titan/expansion.py:40-92`) uses 5 hardcoded thresholds to detect revenue bottlenecks:

| Metric | Hardcoded Threshold | Line |
|--------|-------------------|------|
| reply_rate_14d | < 1.5% | Line 44 |
| interest_rate_14d | < 12.0% | Line 54 |
| proposal_backlog | >= 3 | Line 64 |
| closed_uninvoiced | >= 2 | Line 74 |
| discovered_missing_email_7d | >= 10 | Line 84 |

These thresholds are guesses. Adaptive thresholds replace them with Thompson sampling bandits that learn optimal values from pipeline outcomes (lead conversion as binary signal). Implementation is clean-room from Sutton & Barto (2018, Chapter 2.7) — zero HyperAgents code.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Expansion thresholds | `titan/expansion.py:44-92` | 5 hardcoded values |
| Pipeline outcomes | `stage_metrics` table | Created in Phase 4 |
| Quality scores | `quality_scores` table | Created in Phase 2 |
| Adaptive module | `titan/adaptive_thresholds.py` | Does not exist |
| meta_evaluations table | None | Does not exist |

---

## Tasks

### Task 1: Thompson sampling bandit (ADAPT-01)
**File:** `titan/adaptive_thresholds.py` (new)
**What:**

Clean-room implementation from Sutton & Barto (2018), Chapter 2.7:

```python
import numpy as np
from dataclasses import dataclass

@dataclass
class BetaBandit:
    """Thompson sampling with Beta distribution.

    Reference: Sutton & Barto, Reinforcement Learning (2018), Ch. 2.7
    Clean-room implementation — zero HyperAgents code.
    """
    name: str
    alpha: float  # successes + prior
    beta: float   # failures + prior

    def sample(self) -> float:
        """Draw from Beta(alpha, beta) distribution."""
        return np.random.beta(self.alpha, self.beta)

    def update(self, reward: float) -> None:
        """Update posterior with binary outcome.

        reward: 1.0 = conversion (success), 0.0 = no conversion (failure)
        """
        self.alpha += reward
        self.beta += (1.0 - reward)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence_interval(self) -> tuple[float, float]:
        """95% credible interval."""
        from scipy.stats import beta as beta_dist
        return (
            beta_dist.ppf(0.025, self.alpha, self.beta),
            beta_dist.ppf(0.975, self.alpha, self.beta),
        )

class AdaptiveThresholds:
    """Manages a set of Thompson sampling bandits for expansion thresholds."""

    def __init__(self):
        self._bandits: dict[str, BetaBandit] = {}

    def get_threshold(self, name: str) -> float:
        """Sample current threshold from bandit posterior."""
        if name not in self._bandits:
            self._load_from_db(name)
        return self._bandits[name].sample()

    def update(self, name: str, outcome: float) -> None:
        """Update bandit with pipeline outcome."""
        if name in self._bandits:
            self._bandits[name].update(outcome)
            self._save_to_db(name)
```

Implements `ThresholdProvider` Protocol from Phase 0b.

**Acceptance criteria:**
- [ ] Thompson sampling from Beta distribution
- [ ] Clean-room: only Sutton & Barto reference, no HyperAgents code
- [ ] `isinstance(AdaptiveThresholds(), ThresholdProvider)` passes

### Task 2: DB-stored thresholds (ADAPT-02)
**File:** `titan/adaptive_thresholds.py` (extend) + `scripts/migrations/023-adaptive-thresholds.sql` (new)
**What:**

```sql
CREATE TABLE IF NOT EXISTS adaptive_thresholds (
    id SERIAL PRIMARY KEY,
    threshold_name TEXT UNIQUE NOT NULL,
    alpha DECIMAL(10,4) NOT NULL,
    beta DECIMAL(10,4) NOT NULL,
    current_value DECIMAL(10,4),
    total_updates INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Seed with current hardcoded priors (Task 4)
INSERT INTO adaptive_thresholds (threshold_name, alpha, beta, current_value)
VALUES
    ('reply_rate_threshold', 10, 2, 1.5),
    ('interest_rate_threshold', 10, 2, 12.0),
    ('proposal_backlog_threshold', 10, 2, 3.0),
    ('uninvoiced_threshold', 10, 2, 2.0),
    ('missing_email_threshold', 10, 2, 10.0)
ON CONFLICT DO NOTHING;
```

```python
async def _load_from_db(self, name: str) -> None:
    """Load bandit state from DB."""
    row = await fetch_one(
        "SELECT alpha, beta FROM adaptive_thresholds WHERE threshold_name = %s",
        (name,),
    )
    if row:
        self._bandits[name] = BetaBandit(name, float(row["alpha"]), float(row["beta"]))

async def _save_to_db(self, name: str) -> None:
    """Persist bandit state to DB."""
    b = self._bandits[name]
    await execute(
        """UPDATE adaptive_thresholds
           SET alpha = %s, beta = %s, current_value = %s,
               total_updates = total_updates + 1, last_updated = NOW()
           WHERE threshold_name = %s""",
        (b.alpha, b.beta, b.mean, name),
    )
```

**Acceptance criteria:**
- [ ] 5 hardcoded thresholds replaced with DB-stored bandits
- [ ] Bandit state persists across restarts
- [ ] Migration seeds with current priors

### Task 3: Pipeline outcome training signal (ADAPT-03)
**File:** `titan/adaptive_thresholds.py` (extend)
**What:**

```python
async def collect_training_signal() -> list[tuple[str, float]]:
    """Collect pipeline outcome data as binary training signal.

    Signal: lead conversion (1.0) or non-conversion (0.0)
    Source: leads table — status transitions over last 7 days
    """
    conversions = await fetch_all(
        """SELECT l.status, l.updated_at
           FROM leads l
           WHERE l.updated_at > NOW() - INTERVAL '7 days'
           AND l.status IN ('converted', 'lost', 'stale')"""
    )

    signals = []
    for lead in conversions:
        outcome = 1.0 if lead["status"] == "converted" else 0.0
        # Map to all active thresholds
        for threshold_name in THRESHOLD_NAMES:
            signals.append((threshold_name, outcome))

    return signals
```

Training runs daily via Perseus scheduler.

**Acceptance criteria:**
- [ ] Lead conversion used as binary training signal
- [ ] Training data collected from last 7 days of pipeline outcomes
- [ ] Daily training job added to Perseus scheduler

### Task 4: Warm-start with current priors (ADAPT-04)
**What:**

Seed bandits with Beta(10, 2) distribution — strong prior belief that current thresholds are roughly correct:
- Mean = 10/(10+2) = 0.833 — high confidence in current values
- After ~50 observations, data dominates the prior
- Avoids weeks of random decisions during cold-start

The migration (Task 2) handles the seeding.

**Acceptance criteria:**
- [ ] Bandits start with Beta(10, 2) distribution
- [ ] Prior washes out after ~50 observations
- [ ] No random threshold values during cold-start

### Task 5: Concurrent A/B experiments (ADAPT-05)
**File:** `titan/adaptive_thresholds.py` (extend)
**What:**

```python
class ExperimentManager:
    """Run multiple shadow experiments simultaneously."""

    async def create_experiment(
        self, name: str, threshold_name: str,
        variant_alpha: float, variant_beta: float,
    ) -> str:
        """Create a shadow experiment with alternative priors."""
        ...

    async def assign_lead(self, lead_id: str) -> dict[str, str]:
        """Assign a lead to control or variant for each active experiment."""
        # Random assignment: 50/50 split
        ...

    async def record_outcome(self, lead_id: str, outcome: float) -> None:
        """Record outcome for all experiments this lead participated in."""
        ...

    async def evaluate_experiments(self) -> list[dict]:
        """Evaluate all experiments with sufficient data.

        Returns experiments where variant significantly outperforms control.
        """
        ...
```

**Acceptance criteria:**
- [ ] 2+ shadow experiments run simultaneously
- [ ] 50/50 lead assignment to control/variant
- [ ] Statistical evaluation after sufficient sample size

### Task 6: meta_evaluations table (ADAPT-06)
**File:** `scripts/migrations/023-adaptive-thresholds.sql` (extend)
**What:**

```sql
CREATE TABLE IF NOT EXISTS meta_evaluations (
    id SERIAL PRIMARY KEY,
    threshold_name TEXT NOT NULL,
    old_value DECIMAL(10,4),
    new_value DECIMAL(10,4),
    change_reason TEXT,  -- 'bandit_update', 'experiment_winner', 'manual_override'
    confidence DECIMAL(4,3),
    sample_size INTEGER,
    conversion_rate_before DECIMAL(6,4),
    conversion_rate_after DECIMAL(6,4),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_meta_evaluations_threshold ON meta_evaluations (threshold_name, created_at DESC);
```

Every threshold change logged with rationale.

**Acceptance criteria:**
- [ ] `meta_evaluations` table logs every criteria change
- [ ] Change includes old value, new value, reason, confidence
- [ ] History queryable for trend analysis

### Task 7: Feature flag (ADAPT-07)
**File:** `titan/expansion.py` (modify)
**What:**

Replace hardcoded thresholds with adaptive lookups:

```python
async def _detect_revenue_bottlenecks(metrics: dict) -> list[dict]:
    if feature_flag("ENABLE_BANDIT_EXPANSION"):
        thresholds = AdaptiveThresholds()
        reply_threshold = await thresholds.get_threshold("reply_rate_threshold")
        interest_threshold = await thresholds.get_threshold("interest_rate_threshold")
        # ... etc
    else:
        # Original hardcoded values
        reply_threshold = 1.5
        interest_threshold = 12.0
        # ... etc
```

**Acceptance criteria:**
- [ ] `ENABLE_BANDIT_EXPANSION=false` → hardcoded thresholds (existing behavior)
- [ ] `ENABLE_BANDIT_EXPANSION=true` → Thompson sampling bandits
- [ ] Instant rollback without code changes

### Task 8: License compliance + Tests (ADAPT-08)
**Files:**
- `tests/titan/test_adaptive_thresholds.py` (new)
- `scripts/license-audit.sh` (new)

**What:**

License audit:
```bash
#!/bin/bash
# Verify zero HyperAgents code in production
echo "Scanning for HyperAgents references..."
grep -ri "hyperagent" --include="*.py" titan/ shared/ conway/ hermes/ clawdbot/ perseus/ openjarvis/ || echo "CLEAN: No HyperAgents code found"
grep -ri "arXiv:2603.19461" --include="*.py" titan/ shared/ || echo "CLEAN: No HyperAgents paper refs in code"
echo "License audit complete."
```

Tests:
- Test bandit convergence: after 100 simulated outcomes, thresholds shift toward conversion-correlated values
- Test warm-start: Beta(10,2) prior produces reasonable initial values
- Test DB persistence: save/load round-trip
- Test experiment creation and evaluation
- Test meta_evaluations logging
- Test feature flag on/off
- Test license audit script passes

**Acceptance criteria:**
- [ ] Bandit converges after 100 simulated outcomes
- [ ] Zero HyperAgents code in production paths
- [ ] All tests pass with `PYTHONPATH=. pytest tests/titan/test_adaptive_thresholds.py -v`
- [ ] `ruff check titan/adaptive_thresholds.py titan/expansion.py` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] Bandit converges after 100 simulated outcomes (thresholds shift toward conversion-correlated values)
- [ ] Historical pipeline data validates adaptive > hardcoded (backtest)
- [ ] `meta_evaluations` table logs every criteria change with rationale
- [ ] Concurrent experiments: 2+ shadow experiments run simultaneously
- [ ] Zero HyperAgents code or artifacts in production paths (license audit)

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Thompson sampling diverges with sparse data | Medium | Medium | Warm-start with strong priors; require 50+ observations before trusting |
| HyperAgents code accidentally included | Low | Critical | License audit script in CI; grep-based verification |
| Adaptive thresholds make expansion too aggressive | Low | Medium | Meta-evaluations table tracks all changes; feature flag for instant rollback |
| numpy/scipy dependency conflicts | Low | Low | Already used by MAGMA; pin versions |

---

*Plan created: 2026-03-29*
