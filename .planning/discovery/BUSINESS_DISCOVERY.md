# Business Discovery Report: 7 Research Integrations

**Date:** 2026-03-29
**Scope:** Revenue and business impact analysis for Objective Hertz pipeline
**Pipeline:** discover > research > compose > send > follow_up > close > build > deploy > invoice
**Product:** Professional websites at $200-325 per sale
**Budget:** $800/month (Claude API + infrastructure)

---

## 1. Revenue Impact Ranking

Ranked by direct impact on the metric that matters: dollars collected per month.

### Rank 1: Anti-Slop Quality Gate
**Revenue multiplier: HIGH**

The pipeline's bottleneck is email-to-reply conversion. Currently `email_compose.py` has a regex-based validator (lines 240-286) that catches spam triggers and false claims, but nothing checks whether the email reads like a human wrote it. Every email that sounds like AI slop is a wasted lead. The existing `validate_email_content()` function catches 12 patterns; anti-slop adds 60+ rules specifically tuned for AI writing tells ("It's important to note that", "leverage", "Furthermore").

The mechanism is straightforward: insert a quality gate between `email_compose` (stage 3) and `email_send` (stage 4). Emails that fail get recomposed with a different prompt temperature or a fresh agent context. This directly increases reply rate on every email sent, which directly increases the number of leads reaching `close_deal` (stage 6), which directly increases revenue.

**Revenue path:** Better emails > higher reply rate > more interested leads > more sales
**Estimated lift:** 15-30% improvement in reply rate based on the difference between generic and personalized cold email performance in B2B outreach benchmarks.

### Rank 2: RLMs (Recursive Language Models)
**Revenue multiplier: HIGH**

The single biggest quality problem in the pipeline is context loss between stages. `lead_research.py` scrapes websites, extracts structured facts (services, locations, signals), builds relationship graphs, and selects reference sites. All of this gets compressed into `research_summary` (a text column) and `research_facts` (a JSON column) in the clients table. By the time `email_compose.py` reads it back, it gets the flat summary and a truncated version of the learnings.

RLMs would store the full research payload in Mem0/Qdrant (the infrastructure already exists at `titan/memory.py` lines 34-78) and let the email composer recursively query into it. Instead of working from "Plumber in Dallas, licensed, 24/7 service" the composer could pull specific review quotes, pricing gaps with competitors, and exact service descriptions. The paper shows 2x improvement on long-context tasks using ~2-3k tokens per query vs 95k+ for stuffing everything into the prompt.

**Revenue path:** Richer personalization > dramatically better emails > higher reply rate > more sales
**Estimated lift:** 20-40% improvement in email personalization quality. Combined with Anti-Slop, this is the highest-impact pair.

### Rank 3: Agent DNA
**Revenue multiplier: MEDIUM**

Currently there is no shared engineering DNA layer across the 5 daemons. The `soul/` directory has personality and copywriting guidelines but not universal principles like "check budget_guard before every paid API call" or "run compliance gates on all outward-facing actions". This matters for revenue because:

1. Compliance failures (missing CAN-SPAM unsubscribe, missing physical address) cause email deliverability to tank, which kills reply rates
2. Budget overruns cause the system to downgrade to Ollama, which produces worse emails, which reduces revenue
3. Missing quality gates let bad outputs through any daemon, not just email

The implementation is lightweight: create `soul/engineering_dna.md`, load it as a system prompt prefix in `shared/llm_client.py`. Every LLM call across every daemon inherits baseline quality and compliance behavior.

**Revenue path:** Consistent quality across all stages > fewer pipeline failures > more leads reaching invoice stage
**Estimated lift:** 5-15% reduction in pipeline drop-off rate (leads that enter but never reach invoice).

### Rank 4: HyperAgents (Self-Modifying Evaluation)
**Revenue multiplier: MEDIUM-LOW (but compounds over time)**

OH already has self-improvement infrastructure: `titan/expansion.py` for shadow rollouts, `shared/magma.py` for learning, `titan/training.py` for LoRA fine-tuning. HyperAgents would make the expansion engine's evaluation criteria evolve based on outcomes. Instead of fixed rules for what makes a good discovery skill or email template, the meta-agent would modify its own scoring criteria based on which leads actually converted to paying customers.

This is a compounding effect, not an immediate revenue boost. Month 1 impact is near zero. By month 6, the system would have iterated its own evaluation criteria enough to meaningfully outperform static rules.

**Revenue path:** Better self-evaluation > smarter skill selection > higher quality leads > more sales (delayed)
**Estimated lift:** 0-5% in month 1, potentially 15-25% by month 6 through accumulated meta-improvements.

### Rank 5: DeerFlow Memory (Persistent Daemon Memory)
**Revenue multiplier: LOW-MEDIUM**

When daemons restart (crashes, deploys, Mac reboots), they lose in-memory state. Titan already persists learnings to Postgres (`titan_learnings` table) and Mem0, so the core pipeline survives restarts. DeerFlow's memory pattern would add automatic context extraction from conversations and persistent state across restarts without manual memory tagging.

This primarily reduces operational friction rather than directly increasing revenue. The main revenue benefit is that daemon restarts would not cause the pipeline to lose context about in-progress leads.

**Revenue path:** Fewer lost-state bugs after restarts > smoother pipeline operation > marginally more leads completing the full pipeline
**Estimated lift:** 2-5% reduction in leads lost to daemon restart issues.

### Rank 6: PQC (Post-Quantum Cryptography)
**Revenue multiplier: ZERO (defensive only)**

Post-quantum encryption for wallet keys (`conway/wallet.py`) and API secrets protects against future quantum computing attacks on the ECDSA keys used for Base L2 USDC wallets. This is exclusively a security hardening measure. It does not increase revenue, reduce costs, or improve any business metric.

It becomes relevant only if OH accumulates significant USDC balances in agent wallets and needs to protect against harvest-now-decrypt-later attacks. At current scale ($200-325 per sale), the wallet balances are small and the threat model is years away.

**Revenue path:** None. Pure security insurance.
**Estimated lift:** 0% revenue impact. Risk mitigation only.

### Rank 7: Quantum Computing (QAOA Portfolio Optimization)
**Revenue multiplier: ZERO (out of scope)**

Already scoped out of OH. This is for a separate trading project. No integration point with the 10-stage pipeline.

**Revenue path:** None. Not applicable to OH.
**Estimated lift:** 0%.

---

## 2. Cost Impact Analysis

### Integrations That Increase Costs

| Integration | Cost Driver | Estimated Monthly Impact |
|---|---|---|
| Anti-Slop | Extra LLM call per email (quality gate review) | +$15-40/mo (Haiku calls for scoring) |
| RLMs | Additional Mem0 storage + recursive query calls | +$10-25/mo (more vector queries, slightly more LLM tokens) |
| HyperAgents | Docker containers for isolated evaluation + meta-agent LLM calls | +$30-80/mo (significant compute for self-modification cycles) |
| Agent DNA | Longer system prompts on every LLM call | +$5-15/mo (additional tokens per call, but shared across all) |

### Integrations That Decrease Costs

| Integration | Cost Saving | Estimated Monthly Impact |
|---|---|---|
| RLMs | Uses 2-3k tokens per query vs 95k+ for context stuffing | -$20-50/mo (major token savings on research-heavy calls) |
| Anti-Slop | Catches bad emails before they waste send quota (Instantly.ai charges per send) | -$5-15/mo (fewer wasted sends) |
| Agent DNA | Budget enforcement DNA prevents accidental Opus calls where Haiku suffices | -$10-30/mo (model routing discipline) |
| DeerFlow Memory | Prevents redundant re-computation after restarts | -$5-10/mo (avoids re-researching leads) |

### Net Cost Summary

| Integration | Net Monthly Cost | Within $800 Budget? |
|---|---|---|
| Anti-Slop | +$0 to +$25 | Yes, easily |
| RLMs | -$10 to +$5 (net savings likely) | Yes, may save money |
| Agent DNA | -$5 to +$5 (roughly neutral) | Yes |
| DeerFlow Memory | -$5 to -$10 (net savings) | Yes, saves money |
| HyperAgents | +$30 to +$80 | Yes, but largest new cost |
| PQC | +$0 (one-time implementation) | Yes |
| Quantum | $0 (not applicable) | N/A |

**Total additional monthly cost:** $10-105/month, well within the $800 budget.

---

## 3. Risk Assessment

### HIGH RISK: Could break the pipeline if done wrong

**RLMs**
- Touches the data flow between the two most critical stages (research > compose)
- If Mem0 queries fail or return stale data, emails get worse, not better
- Risk: recursive query loops consuming budget without producing results
- Mitigation: implement with fallback to current flat-record approach; A/B test before full rollout

**HyperAgents**
- Self-modifying code is inherently unpredictable
- A bad meta-modification could degrade evaluation criteria, causing the system to select worse skills
- The non-commercial CC BY-NC-SA license is a legal risk for a commercial revenue system
- Risk: runaway self-modification, license violation
- Mitigation: strict safety bounds (max iterations, rollback on performance drop), verify licensing

### MEDIUM RISK: Could degrade quality if implemented poorly

**Anti-Slop**
- False positives could block good emails (over-aggressive quality gate)
- If the gate is too strict, throughput drops and fewer leads get contacted
- Risk: quality gate becomes a bottleneck
- Mitigation: start with soft scoring (log but don't block), tune thresholds with real data

**Agent DNA**
- Adding system prompt prefix to every LLM call increases input tokens
- If DNA instructions conflict with stage-specific prompts, LLM output quality may degrade
- Risk: prompt interference, budget increase
- Mitigation: keep DNA concise (<500 tokens), test with existing prompts before deploying

### LOW RISK: Unlikely to break anything

**DeerFlow Memory**
- Additive change, doesn't modify existing data flow
- Worst case: memory file gets corrupted, daemon falls back to current behavior
- Risk: minimal

**PQC**
- Isolated to wallet encryption, doesn't touch pipeline stages
- Worst case: wallet access fails, Conway can't process payments (but manual fallback exists)
- Risk: minimal if tested before deploying to production wallets

**Quantum Computing**
- Not being integrated. Zero risk.

---

## 4. Dependency Analysis

```
Phase 1 (Foundation — no dependencies)
├── Agent DNA         [standalone, enables everything else]
├── Anti-Slop         [standalone, immediate value]
└── DeerFlow Memory   [standalone, operational improvement]

Phase 2 (requires Phase 1 stable)
├── RLMs              [depends on: Mem0 infrastructure stable, Agent DNA for consistent prompting]
│                     [benefits from: Anti-Slop to validate RLM-enhanced emails]
└── PQC               [depends on: nothing, but lower priority, do after revenue features]

Phase 3 (requires Phase 2 stable + data)
└── HyperAgents       [depends on: RLMs (for richer evaluation data), Agent DNA (for consistent behavior)]
                      [requires: 2-3 months of pipeline performance data to train meta-agent]
```

### Critical Path
Agent DNA and Anti-Slop are foundation pieces that should ship first because:
1. Agent DNA ensures every subsequent integration inherits quality and compliance behavior
2. Anti-Slop provides the measurement framework (quality scores) that RLMs and HyperAgents need to evaluate their own improvements

### Order Matters
RLMs before HyperAgents because HyperAgents needs outcome data to learn from. Without RLMs producing better emails that generate measurable reply-rate differences, HyperAgents has nothing to optimize against.

---

## 5. ROI Estimate per Integration

| # | Integration | Implementation Effort | Time to Revenue Impact | Revenue Lift | Cost | ROI Rating |
|---|---|---|---|---|---|---|
| 1 | Anti-Slop | 2-3 days | Immediate (next email batch) | 15-30% reply rate | ~$25/mo | EXCELLENT |
| 2 | Agent DNA | 1-2 days | Immediate (all LLM calls) | 5-15% pipeline completion | ~$0/mo | EXCELLENT |
| 3 | RLMs | 5-7 days | 1-2 weeks (needs A/B test) | 20-40% email quality | ~$0/mo (net) | VERY GOOD |
| 4 | DeerFlow Memory | 3-4 days | 1 week | 2-5% lead retention | -$5/mo (saves) | GOOD |
| 5 | PQC | 2-3 days | Never (defensive) | 0% | $0 | LOW (insurance) |
| 6 | HyperAgents | 10-15 days | 3-6 months | 15-25% (delayed) | ~$50/mo | SPECULATIVE |
| 7 | Quantum | N/A | N/A | 0% | $0 | NOT APPLICABLE |

### Quick Win Stack (ship in 2 weeks, immediate revenue impact)
1. Agent DNA (1-2 days)
2. Anti-Slop (2-3 days)
3. RLMs (5-7 days)

Combined estimated lift: 30-60% improvement in email-to-reply conversion, which is the pipeline's primary bottleneck.

---

## 6. Business Case Summary

### The Core Problem

Objective Hertz's revenue is gated by one metric: what percentage of cold emails generate a reply from the business owner. The pipeline can discover leads, research them, compose emails, and build websites. But if the email reads like AI-generated slop or fails to reference specific details about the business, the reply rate stays low and the pipeline starves.

### Why These Integrations Matter

The top 3 integrations (Anti-Slop, RLMs, Agent DNA) all attack the same bottleneck from different angles:

- **Anti-Slop** catches bad emails before they waste a send. Every email that sounds robotic is a lead burned.
- **RLMs** make the good emails dramatically better by preserving full research context through to composition. The difference between "we noticed your plumbing business" and "we saw your 4.8-star Google reviews for same-day drain repair in North Dallas" is the difference between delete and reply.
- **Agent DNA** prevents the entire pipeline from producing inconsistent quality. A missed compliance check tanks deliverability for every email, not just one.

Together, these three integrations transform the pipeline from "AI generates generic cold emails at scale" to "AI generates hyper-personalized, quality-checked outreach that reads like a human researcher wrote it."

### What to Skip

- **Quantum Computing** is already scoped out. Correct decision.
- **PQC** is security insurance with zero revenue impact. Do it when wallet balances justify the protection, not before.
- **HyperAgents** is the most intellectually interesting integration but the worst ROI in the short term. Its non-commercial license is also a legal concern for a revenue-generating system. Defer until the pipeline has 3+ months of performance data and the licensing question is resolved.

### Recommended Execution Order

| Week | Integration | Expected Outcome |
|---|---|---|
| Week 1 | Agent DNA + Anti-Slop | Every LLM call inherits quality DNA; email quality gate live |
| Week 2-3 | RLMs | Full research context flows into email composition |
| Week 4 | DeerFlow Memory | Daemons survive restarts without losing state |
| Month 3+ | PQC | Wallet security hardened (if balances justify it) |
| Month 6+ | HyperAgents | Self-improving evaluation criteria (if license permits) |

### Bottom Line

Three integrations (Anti-Slop, RLMs, Agent DNA) can be shipped in under 3 weeks, cost less than $25/month net, and are projected to improve the pipeline's email-to-reply conversion by 30-60%. This is the highest-leverage work available for Objective Hertz's revenue goal. Everything else is either operational improvement (DeerFlow), security insurance (PQC), or speculative long-term investment (HyperAgents).
