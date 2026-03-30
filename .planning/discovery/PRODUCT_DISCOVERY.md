# Objective Hertz — Product Discovery Report

**Date:** 2026-03-29
**Scope:** 6 active integrations (Quantum scoped out)
**Perspective:** System design implications for an autonomous daemon-to-daemon architecture
**Audience:** Operator (human), consumed by Perseus/Titan for planning

---

## 1. Capability Impact Map

### Integration 1: RLMs (Recursive Language Models)

**What changes:** Titan's email composition stage stops working from flat database rows and starts querying full research context recursively. The email_compose stage receives a context variable containing everything lead_discovery and lead_research produced, then writes code to extract the exact details it needs (review quotes, pricing gaps, competitor names) rather than working from pre-compressed columns.

**Before:** `lead_discovery -> DB row -> lead_research -> DB row -> email_compose` (each arrow loses context)
**After:** `lead_discovery -> DB row + Mem0 store -> lead_research -> DB row + Mem0 store -> email_compose -> recursive Mem0 query -> personalized email`

**System behavior change:**
- Emails reference specific business details instead of generic industry talking points
- Cost per email goes up slightly (recursive sub-calls to Haiku) but conversion should increase enough to offset
- Pipeline latency increases by 2-5 seconds per lead for recursive context retrieval
- Mem0/Qdrant storage grows proportionally with lead volume (each lead gets full research artifact stored)

**Affected daemons:** Titan (primary), ClawdBot (site personalization could use same pattern)

### Integration 2: HyperAgents (Evolving Evaluation Criteria)

**What changes:** Titan's expansion engine (expansion.py) and the nightly sleep cycle stop using fixed thresholds for skill evaluation and start evolving criteria based on observed outcomes. When a skill that was adopted later produces high-value revenue, the meta-agent notices and adjusts evaluation weights to favor that pattern.

**Before:** `if conversion_rate > 0.05: adopt` (fixed forever)
**After:** Meta-agent reviews past adopt/reject decisions, discovers that conversion_rate alone misses skills that produce fewer but higher-value leads, self-modifies to include avg_deal_value and lead_quality_score

**System behavior change:**
- Self-improvement velocity accelerates over time (improvements to the improvement mechanism compound)
- Requires Docker isolation for meta-agent code execution (safety boundary)
- Evaluation criteria drift must be logged and auditable (agent_decisions table)
- Risk: criteria could evolve toward local optima. Periodic human review of evaluation state needed.

**Affected daemons:** Titan (expansion), Perseus (sleep cycle/backprop)

### Integration 3: Anti-Slop (Quality Gate)

**What changes:** A quality gate inserts between every content-generation step and its delivery step. Emails get scored on naturalness, specificity, conciseness, and authenticity by a fresh LLM call with zero composition context. Content that fails gets recomposed with the failure feedback. Same pattern applies to ClawdBot site copy.

**Before:** Titan composes email -> sends email (no quality check)
**After:** Titan composes email -> fresh Haiku scores it on 4 dimensions -> if score < 0.7, recompose with feedback -> send

**System behavior change:**
- Email quality floor established (no AI slop ships)
- Pipeline latency increases by 1-2 seconds per email (Haiku scoring call)
- Recomposition adds another 3-5 seconds when triggered
- Cost increase: ~$0.001 per quality check (Haiku), negligible at current volumes
- Open rates and reply rates become trackable against quality scores (feedback loop for threshold tuning)

**Affected daemons:** Titan (email pipeline), ClawdBot (site copy), Hermes (briefing quality)

### Integration 4: DeerFlow Memory (Persistent Daemon Facts)

**What changes:** Each daemon gets a memory.json (or Postgres daemon_memory table) that persists across restarts. On startup, the daemon loads its top 15 facts and recent context into its system prompt. Facts are extracted automatically from conversations and pipeline outcomes. Deduplication prevents fact bloat.

**Before:** Perseus restarts -> re-reads Postgres task_queue -> has no idea what it was doing or why
**After:** Perseus restarts -> loads memory.json -> knows "I was running restaurant leads in Portland, conversion rate was 3.2%, I decided to try dental next because competitor analysis showed lower saturation"

**System behavior change:**
- Daemon restarts become warm instead of cold (planning context survives)
- Memory extraction runs async (no pipeline latency impact)
- Memory file grows over time; needs pruning strategy (confidence decay, max facts cap)
- Each daemon's system prompt gets 500-1000 tokens longer (memory injection)
- Cross-daemon memory possible but not recommended initially (domain separation)

**Affected daemons:** All 5 (Perseus, Titan, Hermes, ClawdBot, Conway)

### Integration 5: Agent DNA (Universal Engineering Principles)

**What changes:** A soul/engineering_dna.md file gets loaded into every LLM call's system prompt via shared/llm_client.py. Every daemon, skill, and sub-agent automatically inherits compliance gates, budget checks, quality requirements, security practices, and decision logging. New daemons or skills cannot opt out.

**Before:** Each daemon has its own ad-hoc checks. Some check budget_guard, some don't. Some log decisions, some don't. Adding a new daemon requires manually wiring every safety mechanism.
**After:** `shared/llm_client.py` prepends DNA to every system prompt. A new daemon inherits everything automatically.

**System behavior change:**
- Every LLM call gets ~300-500 tokens of DNA overhead
- Cost increase: ~5-8% on token usage across the system
- Compliance coverage goes from partial to universal
- New daemons/skills get safety for free (no integration work)
- DNA file becomes a single point of configuration for engineering standards
- Risk: DNA bloat over time. Needs periodic compression audit.

**Affected daemons:** All (via shared/llm_client.py)

### Integration 6: PQC (Post-Quantum Cryptography)

**What changes:** Conway's wallet private keys get wrapped in ML-KEM-768 post-quantum encryption (hybrid approach: PQ layer on top of existing AES-256). API keys in .env can optionally be encrypted at rest with the same mechanism. Transaction signing uses ML-DSA-65 digital signatures.

**Before:** Wallet keys encrypted with standard AES. Vulnerable to harvest-now-decrypt-later attacks.
**After:** Wallet keys encrypted with AES-256(ML-KEM-768(key)). If quantum breaks ML-KEM-768, AES still protects. If classical breaks AES, ML-KEM-768 still protects.

**System behavior change:**
- Key operations (encrypt/decrypt) add ~50ms latency (PQ algorithms are slower than classical)
- Binary dependency: liboqs requires cmake and C compiler at build time
- Key sizes increase (ML-KEM-768 public key is 1,184 bytes vs 32 bytes for X25519)
- Migration path needed: dual-key period where both old and new encryption coexist
- No runtime behavior change for other daemons (Conway handles this internally)

**Affected daemons:** Conway (primary), shared/config.py (if .env encryption adopted)

---

## 2. Autonomy Assessment

Ranked by how much each integration moves OH closer to operating without human intervention:

### Tier 1: High Autonomy Impact

**DeerFlow Memory** — Currently the biggest autonomy blocker is that daemons lose planning context on restart. A daemon that forgets what it was doing requires human re-orientation. Persistent memory means daemons can self-recover after crashes, restarts, and deploy cycles. This is the single largest autonomy unlock.

**Agent DNA** — Without DNA, every new capability requires human wiring of safety mechanisms. With DNA, the system self-enforces compliance, budget, quality, and security across any capability it acquires. This is the difference between "autonomous but brittle" and "autonomous and safe."

### Tier 2: Medium Autonomy Impact

**HyperAgents** — Self-improving evaluation criteria mean the system's judgment improves without human tuning. Currently, threshold adjustments require operator intervention. With evolving criteria, Titan learns what "good" means from its own outcomes.

**Anti-Slop** — Quality gates allow the system to self-correct output quality without human review. Currently, bad emails would ship unnoticed until open rates tank. With anti-slop, the system catches and fixes its own mistakes in real-time.

### Tier 3: Lower Autonomy Impact (But High Value)

**RLMs** — Improves output quality significantly but doesn't change the system's ability to operate independently. A daemon with better emails is still the same daemon operationally.

**PQC** — Security hardening. Doesn't change autonomy but prevents catastrophic key compromise that would require human incident response.

---

## 3. Reliability Improvements

Each integration mapped to the failure modes it eliminates:

| Integration | Failure Mode Eliminated | Current Impact | Post-Integration |
|-------------|------------------------|----------------|-----------------|
| DeerFlow Memory | Cold restart amnesia | Daemon forgets planning context, re-does work | Warm restart, picks up where it left off |
| Agent DNA | Missing safety gate on new capability | New skill bypasses budget_guard or compliance | All capabilities inherit safety automatically |
| Anti-Slop | AI-sounding emails tank open rates | No detection until metrics degrade (days) | Real-time detection and recomposition (seconds) |
| PQC | Wallet key compromise via quantum | Undetectable until funds stolen | Keys protected against future quantum attacks |
| HyperAgents | Static criteria miss evolving patterns | Manual threshold tuning required | Criteria self-adjust based on outcomes |
| RLMs | Generic emails from context loss | Research context compressed to DB columns | Full context available at composition time |

**Net reliability gain:** The combination of DeerFlow Memory + Agent DNA eliminates the two most common failure modes: daemons losing state and daemons lacking safety coverage. These two alone would reduce the operator's incident response load by an estimated 60-70%.

---

## 4. Quality Improvements

Ranked by measurable output quality impact:

### Tier 1: Direct Output Quality

**Anti-Slop** — Immediate, measurable quality improvement. Every email and site copy gets scored on 4 dimensions. Quality floor goes from "whatever the model produces" to "minimum 0.7 across naturalness, specificity, conciseness, authenticity." Measurable via open rates, reply rates, quality score distributions.

**RLMs** — Emails go from generic ("I noticed your business could benefit from a professional website") to specific ("Your 4.2-star rating on Google has 3 reviews mentioning slow online ordering — a custom site with integrated ordering could address that directly"). Measurable via reply rates and specificity scores.

### Tier 2: Indirect Quality Through Better Process

**Agent DNA** — Quality gates become universal rather than per-daemon. Every LLM call inherits quality requirements. This prevents quality regression when new capabilities are added.

**HyperAgents** — Quality criteria evolve based on what actually works. If emails with specific review quotes get 3x more replies, the evaluation criteria learn to weight specificity higher. Quality improves through better self-selection.

### Tier 3: Infrastructure Quality

**DeerFlow Memory** — Better daemon context means better decisions. A daemon that remembers "dental niches convert 2x better than restaurants in this market" makes higher-quality targeting choices.

**PQC** — No direct output quality improvement, but prevents the catastrophic quality event of a security breach.

---

## 5. Self-Improvement Loop

The 6 integrations form a reinforcing cycle when connected:

```
                    ┌─────────────────────────┐
                    │    HyperAgents          │
                    │  (evolving criteria)     │
                    └──────────┬──────────────┘
                               │ criteria improve
                               ▼ what "good" means
┌──────────────┐     ┌─────────────────────┐     ┌──────────────┐
│ DeerFlow     │────▶│   Titan Pipeline    │────▶│  Anti-Slop   │
│ Memory       │     │  (lead → email)     │     │  (quality    │
│ (remembers   │     │                     │     │   gate)      │
│  what works) │     └─────────┬───────────┘     └──────┬───────┘
└──────┬───────┘               │                        │
       │                       │ full context            │ quality scores
       │                       ▼                        │
       │              ┌─────────────────┐               │
       │              │    RLMs         │               │
       │              │ (recursive      │               │
       │              │  context query) │               │
       │              └─────────────────┘               │
       │                                                │
       └────────────────────────────────────────────────┘
              memory stores quality outcomes
              for next cycle's improvement
```

**The loop works like this:**

1. **RLMs** give Titan full context for email composition (better raw material)
2. **Anti-Slop** scores the output and either passes or recomposes (quality floor)
3. **DeerFlow Memory** stores quality scores and outcomes per lead/niche/template (learning)
4. **HyperAgents** uses stored outcomes to evolve what "quality" means (meta-learning)
5. **Agent DNA** ensures every step in this loop follows compliance, budget, and security rules (safety rails)
6. **PQC** protects the wallet keys and API secrets that make the whole system run (infrastructure protection)

**Key insight:** Without DeerFlow Memory, the loop has no persistent state — it re-learns from scratch every restart. Without HyperAgents, the loop improves outputs but never improves its own standards. Without Anti-Slop, there's no quality signal to feed back into the loop. All three are needed for the loop to be genuinely self-improving.

**Agent DNA** is the safety rail that prevents the self-improvement loop from evolving in unsafe directions (optimizing conversion by sending spam, for instance). DNA enforces that improvement happens within compliance and budget constraints.

---

## 6. Monitoring Needs

### Per-Integration Observability

| Integration | Metric | Alert Threshold | Dashboard Panel |
|-------------|--------|-----------------|-----------------|
| **RLMs** | Recursive query depth per email | >5 (runaway recursion) | Avg depth, p95 latency, cost per recursive chain |
| **RLMs** | Mem0 storage size per lead | >10MB (bloat) | Total storage, growth rate, oldest unqueried contexts |
| **HyperAgents** | Criteria drift distance | >0.3 cosine distance from baseline per week | Criteria evolution timeline, adopt/reject ratio trend |
| **HyperAgents** | Meta-agent generation count | >50 without improvement (stagnation) | Improvement velocity curve, best score per generation |
| **Anti-Slop** | Quality score distribution | Mean naturalness <0.7 (systemic quality drop) | Score histograms by dimension, recomposition rate |
| **Anti-Slop** | Recomposition rate | >40% (composition model degrading) | % emails recomposed, before/after score delta |
| **DeerFlow Memory** | Facts per daemon | >200 (unbounded growth) | Fact count, category distribution, avg confidence |
| **DeerFlow Memory** | Memory load time on restart | >5 seconds (startup delay) | p50/p95 load time, fact count vs load time correlation |
| **Agent DNA** | DNA token overhead per call | >600 tokens (bloat) | Token count, % of total prompt, DNA file size trend |
| **Agent DNA** | DNA coverage audit | Any daemon missing DNA injection | Coverage report: which calls include DNA, which don't |
| **PQC** | Key encryption/decryption latency | >200ms (performance regression) | p50/p95 crypto operation time, algorithm benchmarks |
| **PQC** | Migration status | Any wallet still on classical-only | Migration progress: dual-key count, fully-migrated count |

### New Hermes Dashboard Panels

The War Room dashboard (hermes/web/frontend/) needs 3 new panels:

1. **Quality Pipeline** — Real-time anti-slop scores for emails in flight, recomposition events, quality trends over time
2. **Self-Improvement Velocity** — HyperAgents generation chart, criteria evolution log, improvement rate
3. **Daemon Memory Health** — Per-daemon fact count, memory load times, oldest/newest facts, confidence distribution

### Alerting Rules (Telegram via Hermes)

- CRITICAL: PQC migration incomplete after 7 days
- CRITICAL: Anti-slop mean score drops below 0.5 (systemic quality failure)
- WARNING: HyperAgents criteria drift exceeds 0.3 per week
- WARNING: Daemon memory exceeds 200 facts without pruning
- INFO: RLM recursive depth exceeds 3 on any single email (cost investigation)

---

## 7. User Journey (Daemon Perspective)

A lead entering the enhanced pipeline, traced through every integration touchpoint:

### Stage 1: Lead Discovery (Titan)

Titan's lead_discovery.py finds "Mario's Pizzeria" in Portland via Google Maps scraping.

**DeerFlow Memory active:** Titan's system prompt includes the fact: "Portland restaurant leads have 4.1% conversion rate, dental leads have 8.7%. Prioritize dental." But Mario's came up in a discovery batch, so it proceeds.

**Agent DNA active:** lead_discovery checks budget_guard before making the Firecrawl API call. Logs the discovery decision to agent_decisions.

**RLM setup:** Full discovery output (reviews, photos found, menu analysis, competitor sites) stored to Mem0 with reference ID linked to the lead's DB row.

### Stage 2: Lead Research (Titan)

lead_research.py enriches the lead. Finds Mario's has a 3.8-star Google rating, 47 reviews, no website, competitors in the area have online ordering.

**RLM active:** Research output (full competitor analysis, review sentiment breakdown, pricing data from menu photos) stored to Mem0, appended to the same context ID.

**Agent DNA active:** Research respects rate limits, logs the enrichment decision, checks that no PII was scraped beyond business public info.

### Stage 3: Email Composition (Titan)

email_compose.py runs. This is where RLMs make the biggest difference.

**RLM active:** Instead of receiving `{name: "Mario's Pizzeria", industry: "restaurant", gap_score: 7.2}`, the compose stage calls:
```
recursive_compose(
    query="Write a personalized cold email for Mario's Pizzeria",
    context=mem0.get("mario_pizzeria_research"),  // Full 15k-token research output
    max_depth=2,
    recursive_model="haiku"
)
```

The recursive call extracts: "3 reviews mention long wait times for takeout orders", "closest competitor (Tony's) has online ordering and 4.5 stars", "Mario's menu has 23 items but no prices online." The email now references these specific details.

**Anti-Slop active:** Fresh Haiku call scores the email:
- Naturalness: 0.82 (no "I hope this email finds you well")
- Specificity: 0.91 (references actual review quotes and competitor data)
- Conciseness: 0.78 (127 words, every sentence earns its place)
- Authenticity: 0.85 (reads like someone who actually looked at the business)
- Overall: 0.84 -- PASS

**Agent DNA active:** Email includes CAN-SPAM compliant unsubscribe link, physical address. Suppression list checked. Decision logged.

### Stage 4: Email Send (Titan)

email_send.py dispatches via Instantly with warm-up and domain rotation.

**Agent DNA active:** Budget tracked. Send decision logged. Compliance gate passed.

### Stage 5: Follow-Up Decision (Titan)

3 days later, no reply. follow_up.py decides whether to send a follow-up.

**DeerFlow Memory active:** Memory includes "follow-up emails to restaurant leads after 3 days have 12% reply rate vs 7% for 5-day delay." Decision: send now.

**RLM active:** Follow-up email uses the same Mem0 context to reference a different angle ("I noticed Tony's just added curbside pickup last month — your regulars probably noticed too").

**Anti-Slop active:** Follow-up scored. Passes.

### Stage 6: Site Build (ClawdBot)

Mario replies: "Sure, what would it cost?" ClawdBot builds 3 demo site variants.

**RLM active:** Site copy pulls from the same Mem0 research context. Menu items, hours, location all pre-populated from research.

**Anti-Slop active:** Each site variant's copy scored. One variant has "In today's competitive landscape" in the hero section — fails naturalness, gets recomposed.

**Agent DNA active:** Budget guard checked before Recraft image generation calls. Netlify deploy logged.

### Stage 7: Close & Invoice (Titan/Conway)

Mario picks variant 2, pays $499 setup + $79/month.

**PQC active:** Conway records the transaction. Wallet keys used for the USDC-side accounting are protected with ML-KEM-768 hybrid encryption. Transaction signed with ML-DSA-65.

**HyperAgents active:** This successful conversion feeds back into the meta-evaluation system. Criteria learn: "emails with specific review quotes had 3.1x higher reply rate this week." Evaluation weights shift to favor specificity in future skill assessments.

**DeerFlow Memory updated:** Facts stored: "Portland restaurant: $499 setup won. Review-quote emails convert. Follow-up on day 3 worked." These facts load next time Titan processes a Portland restaurant lead.

---

## 8. MVP Definition

### The Question: What is the minimum set that produces measurable improvement?

### MVP: Anti-Slop + Agent DNA + DeerFlow Memory

**Rationale:**

These three integrations are the minimum set that creates a closed improvement loop:

1. **Agent DNA** (2-3 days implementation) — Single file + one modification to shared/llm_client.py. Every LLM call gets engineering principles. Immediate safety improvement, zero risk, no new dependencies.

2. **Anti-Slop** (3-4 days implementation) — Quality gate between email_compose and email_send. One new Haiku call per email. Immediate, measurable quality improvement via quality_scores table. Provides the quality signal needed for feedback.

3. **DeerFlow Memory** (4-5 days implementation) — daemon_memory table + memory.json per daemon + memory injection on startup. Solves the cold-restart problem. Provides persistent storage for quality outcomes.

**Why not RLMs in MVP:** RLMs require Mem0 integration, recursive-llm dependency, and pipeline refactoring. High value but higher implementation risk. Better as Phase 2 after the quality feedback loop exists.

**Why not HyperAgents in MVP:** HyperAgents requires Docker isolation, meta-agent orchestration, and enough outcome data to train on. It needs the quality scores and memory that Anti-Slop and DeerFlow produce. Natural Phase 3.

**Why not PQC in MVP:** PQC is security hardening with no direct revenue impact. Important but not urgent for proving the improvement loop. Phase 4 or parallel track.

### MVP Success Criteria

| Metric | Baseline (estimate) | MVP Target | Measurement |
|--------|---------------------|------------|-------------|
| Email quality score (mean) | N/A (no scoring exists) | >0.70 across all 4 dimensions | quality_scores table |
| Recomposition rate | N/A | <30% (most emails pass first try) | quality_scores recompose count |
| Cold restart recovery time | Manual re-orientation (minutes) | <10 seconds (automatic memory load) | daemon_memory load time |
| DNA coverage | 0% (no universal principles) | 100% of LLM calls include DNA | Audit log |
| Engineering incident rate | Unknown | Establish baseline, track weekly | agent_decisions + hermes alerts |

### Implementation Order

```
Week 1: Agent DNA (lowest risk, immediate value)
  - Create soul/engineering_dna.md
  - Modify shared/llm_client.py to prepend DNA
  - Verify all daemon LLM calls include DNA
  - Add DNA coverage audit to health checks

Week 2: Anti-Slop Quality Gate
  - Create quality_scores table
  - Implement quality gate in titan/pipeline/email_compose.py
  - Add recomposition logic with feedback
  - Wire quality scores to Hermes dashboard

Week 3: DeerFlow Memory
  - Create daemon_memory table (or memory.json per daemon)
  - Implement memory extraction (async, post-pipeline)
  - Implement memory injection on daemon startup
  - Add memory health monitoring to Hermes

Week 4: Integration Testing + Baseline Measurement
  - End-to-end pipeline test with all 3 active
  - Measure quality scores on 50+ emails
  - Measure restart recovery time
  - Establish baselines for Phase 2 planning
```

### Phase 2: RLMs (Weeks 5-7)

Add recursive context retrieval after the quality feedback loop is proven. Requires:
- Mem0 integration for full research context storage
- recursive-llm dependency installation and testing
- Pipeline refactoring to pass context IDs through stages
- Quality score comparison: pre-RLM vs post-RLM emails

### Phase 3: HyperAgents (Weeks 8-10)

Add evolving evaluation criteria after enough outcome data exists. Requires:
- meta_evaluations table with enough rows to train on
- Docker isolation for meta-agent execution
- Criteria evolution logging and drift monitoring
- Human review checkpoint for first 10 criteria mutations

### Phase 4: PQC (Weeks 11-12, or parallel)

Add post-quantum encryption for wallet keys. Can run in parallel with any phase. Requires:
- liboqs-python build verification on macOS ARM64
- Hybrid encryption implementation in conway/wallet.py
- Dual-key migration period (old + new encryption coexist)
- Migration completion verification

---

## Appendix: Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Anti-slop scoring is too aggressive, rejects good emails | Medium | Low | Start with 0.6 threshold, tune based on human review of first 50 scored emails |
| RLM recursive calls cause runaway costs | Low | Medium | Hard limit on max_depth=3, cost tracking per recursive chain, budget_guard enforcement |
| HyperAgents criteria evolve toward gaming metrics | Medium | High | Log every criteria mutation, human review every 50 generations, revert mechanism |
| DeerFlow memory grows unbounded | High | Low | Max 150 facts per daemon, confidence decay, weekly pruning job |
| DNA token overhead increases LLM costs by >10% | Medium | Low | Periodic DNA compression audit, target <400 tokens |
| PQC liboqs fails to build on macOS ARM64 | Low | Medium | Test build in CI before integration, fallback to pqcrypto package |
| Multiple integrations interact in unexpected ways | Medium | Medium | Phase rollout (not big-bang), each integration tested in isolation first |

---

## Appendix: Dependency Graph

```
Agent DNA ──────────────────────────────────────────────────────┐
  (no dependencies, implement first)                            │
                                                                │
Anti-Slop ──────────────────────────────────────────────┐       │
  (depends on: Agent DNA for compliance in scoring)     │       │
                                                        ▼       ▼
DeerFlow Memory ─────────────────────────────────────▶ MVP COMPLETE
  (depends on: Anti-Slop for quality scores to store)
                                                        │
RLMs ──────────────────────────────────────────────────▶│ Phase 2
  (depends on: DeerFlow Memory for outcome tracking)    │
                                                        │
HyperAgents ───────────────────────────────────────────▶│ Phase 3
  (depends on: Anti-Slop scores + DeerFlow Memory       │
   for outcome data to train criteria evolution)        │
                                                        │
PQC ───────────────────────────────────────────────────▶│ Phase 4 (or parallel)
  (no functional dependencies, security hardening)
```
