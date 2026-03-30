# Phase 2: Anti-Slop Quality Gate

**Goal:** Every outbound text (email, site copy, alert) scored for quality before dispatch. Slop detected and rewritten.
**Requirements:** SLOP-01, SLOP-02, SLOP-03, SLOP-04, SLOP-05, SLOP-06, SLOP-07, SLOP-08
**Depends on:** Phase 0b (contracts)
**Feature flag:** `ENABLE_ANTI_SLOP`

---

## Context

Anti-slop is the highest revenue-impact integration: email quality directly affects open rates, reply rates, and conversions. The system needs a 5-dimension quality scorer using Haiku (cheap, fast), a best-of-N rewrite loop, secret detection, and configurable thresholds per content type.

The `SlopScorer` Protocol (Phase 0b) defines the interface. Quality scores are stored in a standalone `quality_scores` table for trend analysis.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Email compose | `titan/pipeline/email_compose.py:193-198` | LLM generates email, no quality gate |
| Email send | `titan/pipeline/email_send.py:84-134` | Has micro-review personas (informal quality check) |
| Site builder | `clawdbot/site_builder.py` | Generates site copy, no quality gate |
| Anti-slop module | `shared/anti_slop.py` | Does not exist |
| quality_scores table | None | Does not exist |

---

## Tasks

### Task 1: 5-dimension quality scorer (SLOP-01)
**File:** `shared/anti_slop.py` (new)
**What:**

```python
class AntiSlopScorer:
    """Scores content quality across 5 dimensions. Implements SlopScorer Protocol."""

    DIMENSIONS = ["clarity", "specificity", "authenticity", "value_density", "slop_score"]

    # Slop patterns: overused AI phrases
    SLOP_PATTERNS = [
        "I hope this email finds you well",
        "In today's fast-paced",
        "leverage", "synergy", "game-changer", "cutting-edge",
        "I wanted to reach out", "Just circling back",
        "unlock the full potential", "take it to the next level",
        "deep dive", "low-hanging fruit", "move the needle",
        "at the end of the day", "touch base",
        "revolutionary", "transformative", "paradigm shift",
        # ... 60+ patterns from intel/anti-slop/REFERENCE.md
    ]

    async def score(self, content: str, context: str = "email") -> dict[str, float]:
        """Score content on 5 dimensions using Haiku."""
        # 1. Run slop pattern detection (regex, no LLM needed)
        # 2. Call Haiku with scoring rubric
        # 3. Return dict of dimension: score (0.0-1.0)

    def get_threshold(self, context: str) -> float:
        """Get minimum acceptable score for this context type."""
```

Scoring approach:
- **Slop score**: Regex-based count of slop patterns, normalized to 0-1. No LLM needed.
- **Clarity, specificity, authenticity, value_density**: Haiku evaluates with structured rubric, returns JSON scores.
- Cost: ~$0.002 per scoring call (Haiku with ~500 input tokens)

Implements `SlopScorer` Protocol from `shared/contracts.py`.

**Acceptance criteria:**
- [ ] 5-dimension scoring returns float scores 0.0-1.0 per dimension
- [ ] Slop pattern detection catches 80%+ of patterns from reference doc
- [ ] Haiku used for evaluation (not Sonnet/Opus)
- [ ] `isinstance(AntiSlopScorer(), SlopScorer)` passes

### Task 2: Email pipeline quality gate (SLOP-02)
**File:** `titan/pipeline/email_compose.py` (modify)
**What:**

Insert quality gate between email composition and send:

```python
# After LLM generates email body (line ~198):
if feature_flag("ENABLE_ANTI_SLOP"):
    scorer = AntiSlopScorer()
    scores = await scorer.score(email_body, context="email")

    # Store scores
    await record_quality_score(lead_id, "email", scores)

    # Check threshold
    threshold = scorer.get_threshold("email")
    if scores["slop_score"] > threshold:  # high slop = bad
        # Trigger rewrite (Task 3)
        email_body = await rewrite_loop(email_body, scores, max_iterations=3)
```

**Acceptance criteria:**
- [ ] Quality gate runs on every email before send when flag enabled
- [ ] Emails below threshold trigger rewrite
- [ ] Quality gate adds < 2 seconds to pipeline (Haiku is fast)
- [ ] Flag off = no quality gate, existing behavior preserved

### Task 3: Best-of-N rewrite loop (SLOP-03)
**File:** `shared/anti_slop.py` (extend)
**What:**

```python
async def rewrite_loop(
    content: str,
    initial_scores: dict[str, float],
    context: str = "email",
    max_iterations: int = 3,
) -> str:
    """Rewrite content to reduce slop. Returns highest-scoring version."""
    versions: list[tuple[str, dict[str, float]]] = [(content, initial_scores)]

    for i in range(max_iterations):
        # Generate rewrite with feedback
        rewrite = await llm.generate(
            prompt=f"Rewrite this to be more specific and less generic:\n\n{content}\n\nIssues: {_format_issues(versions[-1][1])}",
            model="fast",  # Haiku
        )
        scores = await scorer.score(rewrite, context)
        versions.append((rewrite, scores))

        # Check good-enough threshold (Task 4)
        if _is_good_enough(scores, context):
            break

    # Return highest-scoring version (NOT latest)
    return max(versions, key=lambda v: _composite_score(v[1]))[0]
```

Key behaviors:
- Tracks ALL versions with scores
- Returns the **highest-scoring** version, not necessarily the latest
- Uses Haiku for both rewrites and scoring (cost-efficient)
- Maximum 3 iterations

**Acceptance criteria:**
- [ ] Best-of-N returns highest-scoring version (not latest)
- [ ] Maximum 3 iterations enforced
- [ ] Each iteration scored independently

### Task 4: Good-enough threshold (SLOP-04)
**File:** `shared/anti_slop.py` (extend)
**What:**

```python
# Thresholds per context (SLOP-08 combined here)
THRESHOLDS = {
    "email": {"min_composite": 0.7, "max_slop": 0.2},      # Strict
    "site_copy": {"min_composite": 0.75, "max_slop": 0.15}, # Strict
    "alert": {"min_composite": 0.5, "max_slop": 0.4},       # Relaxed
    "internal": {"min_composite": 0.3, "max_slop": 0.6},    # Very relaxed
}

def _is_good_enough(scores: dict[str, float], context: str) -> bool:
    """Check if scores meet the good-enough threshold for this context."""
    t = THRESHOLDS.get(context, THRESHOLDS["email"])
    composite = _composite_score(scores)
    return composite >= t["min_composite"] and scores.get("slop_score", 1.0) <= t["max_slop"]
```

**Acceptance criteria:**
- [ ] Good-enough threshold skips unnecessary rewrite iterations
- [ ] Configurable per context type (email=strict, alert=relaxed)
- [ ] High-quality first drafts skip rewrite entirely

### Task 5: ClawdBot site copy gate (SLOP-05)
**File:** `clawdbot/site_builder.py` (modify)
**What:**

Add quality gate before Netlify deploy:
- Score all generated site copy sections with `AntiSlopScorer(context="site_copy")`
- Rewrite any section that fails threshold
- Log quality scores for each section

**Acceptance criteria:**
- [ ] Site copy scored before deploy when flag enabled
- [ ] Failed sections rewritten automatically
- [ ] Quality scores logged per site section

### Task 6: Secret detection regex (SLOP-06)
**File:** `shared/anti_slop.py` (extend)
**What:**

```python
SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "API key (OpenAI/Anthropic format)"),
    (r"[A-Z0-9]{20}:[A-Za-z0-9+/=]{40,}", "AWS-style credential"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"xoxb-[0-9]+-[a-zA-Z0-9]+", "Slack bot token"),
    (r"(?:password|passwd|pwd)\s*[:=]\s*\S+", "Password in plaintext"),
    (r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "Credit card number"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b.*(?:password|token|key)", "Credential+email combo"),
    # ... 25+ patterns covering: API keys, tokens, passwords, PII, SSN, private keys
]

def detect_secrets(content: str) -> list[dict]:
    """Scan content for leaked secrets. Returns list of findings."""
    findings = []
    for pattern, description in SECRET_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            findings.append({"pattern": description, "count": len(matches)})
    return findings
```

- Run on ALL outgoing content (emails, site copy, alerts)
- If secrets detected: BLOCK the content, log alert, return error
- Never send content with detected secrets

**Acceptance criteria:**
- [ ] Secret detection catches all 25+ regex patterns
- [ ] Content with detected secrets is blocked (not sent)
- [ ] Alert sent to operator on secret detection

### Task 7: quality_scores table (SLOP-07)
**Files:**
- `scripts/migrations/018-quality-scores.sql` (new)
- `shared/anti_slop.py` (extend with DB functions)

**What:**

```sql
CREATE TABLE IF NOT EXISTS quality_scores (
    id SERIAL PRIMARY KEY,
    content_type TEXT NOT NULL,  -- 'email', 'site_copy', 'alert'
    reference_id TEXT,           -- lead_id, site_id, etc.
    clarity DECIMAL(4,3),
    specificity DECIMAL(4,3),
    authenticity DECIMAL(4,3),
    value_density DECIMAL(4,3),
    slop_score DECIMAL(4,3),
    composite DECIMAL(4,3),
    rewrite_count INTEGER DEFAULT 0,
    model_used TEXT DEFAULT 'haiku',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_quality_scores_type ON quality_scores (content_type, created_at DESC);
CREATE INDEX idx_quality_scores_ref ON quality_scores (reference_id);
```

Functions:
- `record_quality_score(reference_id, content_type, scores, rewrite_count)` — async INSERT
- `get_quality_trend(content_type, days=30)` — avg scores over time for trend analysis

**Acceptance criteria:**
- [ ] `quality_scores` table created via migration
- [ ] Every scored content gets a row
- [ ] Trend query works: avg scores per day for last 30 days

### Task 8: Tests
**Files:**
- `tests/shared/test_anti_slop.py` (new)
- `tests/titan/test_email_quality_gate.py` (new)

**What:**
- Test 5-dimension scoring with known slop content (scores low) and clean content (scores high)
- Test best-of-N returns highest scorer
- Test good-enough threshold skips unnecessary rewrites
- Test secret detection catches API keys, passwords, tokens
- Test secret detection blocks content
- Test quality_scores DB insert and trend query
- Test `isinstance(AntiSlopScorer(), SlopScorer)` passes
- Test feature flag on/off behavior

**Acceptance criteria:**
- [ ] 80%+ slop detection rate on 20-sample test corpus
- [ ] Cost per 1000 emails scored under $2 (calculated from Haiku pricing)
- [ ] All tests pass with `PYTHONPATH=. pytest tests/shared/test_anti_slop.py tests/titan/test_email_quality_gate.py -v`

---

## Success Criteria (from ROADMAP.md)

- [ ] 80%+ slop detection rate on 20-sample test corpus
- [ ] Best-of-N returns highest-scoring version (not latest)
- [ ] Secret detection catches all 25+ regex patterns
- [ ] Cost per 1000 emails scored under $2 (Haiku)
- [ ] quality_scores table populated with per-content scores

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Haiku scoring disagrees with human judgment | Medium | Medium | Calibrate rubric on 20 sample emails first, adjust weights |
| Rewrite loop makes emails worse | Low | High | Best-of-N returns highest scorer; original always in candidate pool |
| Quality gate slows pipeline too much | Low | Medium | Haiku is fast (~500ms); good-enough threshold skips rewrites |
| Secret regex false positives | Medium | Low | Allowlist for known-safe patterns (e.g., example emails in templates) |

---

*Plan created: 2026-03-29*
