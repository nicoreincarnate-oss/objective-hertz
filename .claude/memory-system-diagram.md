# Perseus Memory & Learning System — Complete Architecture

## The 30-Second Summary

Every interaction (email sent, reply received, deal closed, rule triggered) flows through **6 memory stores** and **3 learning loops** that make the system smarter every day without human intervention.

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                    THE MEMORY STACK (6 layers)                      │
  │                                                                     │
  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐              │
  │  │   Neo4j      │  │   Qdrant     │  │     Zep      │              │
  │  │  (MAGMA)     │  │  (via Mem0)  │  │  (Graphiti)  │              │
  │  │             │  │              │  │              │              │
  │  │ 4 graphs:   │  │ Vector       │  │ Temporal     │              │
  │  │ • TEMPORAL   │  │ embeddings   │  │ facts with   │              │
  │  │ • CAUSAL     │  │ for semantic │  │ auto-expiry  │              │
  │  │ • SEMANTIC   │  │ similarity   │  │              │              │
  │  │ • ENTITY     │  │ search       │  │              │              │
  │  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘              │
  │         │                │                  │                       │
  │         └────────────────┼──────────────────┘                       │
  │                          │                                          │
  │              ┌───────────▼────────────┐                             │
  │              │  MAGMA Intent Router   │                             │
  │              │                        │                             │
  │              │ "Why?" → CAUSAL graph  │                             │
  │              │ "When?" → TEMPORAL     │                             │
  │              │ "Who?" → ENTITY        │                             │
  │              │ "What?" → SEMANTIC     │                             │
  │              └───────────┬────────────┘                             │
  │                          │                                          │
  │  ┌───────────────────────▼────────────────────────────────────┐    │
  │  │              Postgres (structured memory)                   │    │
  │  │                                                             │    │
  │  │  titan_learnings   titan_rules   training_data              │    │
  │  │  agent_decisions   sleep_cycle_log   pending_outcomes       │    │
  │  │  email_sequences   clients   outreach_metrics               │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  │                                                                     │
  │  ┌─────────────────────────────────────────────────────────────┐    │
  │  │              Filesystem (soul docs + config)                 │    │
  │  │                                                             │    │
  │  │  soul/soul_copy.md    soul/soul_agent.md    .env            │    │
  │  │  (editable by sleep cycle backprop)                         │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  └─────────────────────────────────────────────────────────────────────┘
```

---

## How Memory Flows: Top to Bottom

### Level 1: Real-World Interaction
```
  PROSPECT (human)
       │
       ▼
  ┌────────────┐     ┌───────────┐     ┌────────────┐
  │ Instantly   │────▶│  Titan    │────▶│  ClawdBot  │
  │ (email API) │     │ Pipeline  │     │ (builder)  │
  └────────────┘     └─────┬─────┘     └────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                  ▼
    email_sent        reply_received     site_built
    email_opened      deal_closed        skill_executed
    email_bounced     lead_lost          demo_verified
```

### Level 2: Immediate Recording (every interaction)
```
  INTERACTION
       │
       ├──▶ training_data        (raw example: input → output → outcome)
       │      └──▶ pending_outcomes  (schedule 7/14/30-day re-checks)
       │
       ├──▶ email_sequences      (subject, body, prompt_version_hash)
       │
       ├──▶ agent_decisions      (who decided what and why)
       │
       ├──▶ Mem0/Qdrant          (vector embedding for semantic search)
       │
       ├──▶ Neo4j via MAGMA      (temporal chain + entity links)
       │      └──▶ [async] causal edge inference (slow path)
       │
       └──▶ Zep                  (temporal fact with 7-30 day expiry)
```

### Level 3: Daily Learning (every night)
```
  ┌─────────────────────────────────────────────────────┐
  │              DAILY REFLECTION                         │
  │                                                       │
  │  INPUT:                                               │
  │  • outreach_metrics (opens, replies, bounces)         │
  │  • training_data outcomes (last 24h)                  │
  │                                                       │
  │  PROCESS:                                             │
  │  • Claude analyzes: "What worked? What didn't?"       │
  │  • Generates 3-5 insights with confidence scores      │
  │  • Contradiction detection vs existing learnings      │
  │                                                       │
  │  OUTPUT:                                              │
  │  ├──▶ titan_learnings    (structured insights)        │
  │  ├──▶ Mem0               (vector for retrieval)       │
  │  ├──▶ MAGMA Neo4j        (temporal + entity graph)    │
  │  └──▶ titan_rules        (if 15%+ metric improvement) │
  └─────────────────────────────────────────────────────┘
                          │
                          ▼
  ┌─────────────────────────────────────────────────────┐
  │              RULE EXTRACTION                          │
  │                                                       │
  │  IF open_rate improved 15%+ over yesterday:           │
  │    Claude extracts: "Subject lines under 6 words      │
  │    get 2.1x open rate" (n=234)                        │
  │                                                       │
  │  Stored in titan_rules with:                          │
  │  • metric_name: open_rate                             │
  │  • metric_before: 18.2%                               │
  │  • metric_after: 21.5%                                │
  │  • sample_size: 234                                   │
  │  • confidence: 0.72                                   │
  │                                                       │
  │  INJECTED into every future email prompt as:          │
  │  "PROVEN RULES (follow these):                        │
  │    1. Subject lines under 6 words (2.1x, n=234)"     │
  └─────────────────────────────────────────────────────┘
```

### Level 4: Weekly Strategy (every 7 days)
```
  ┌─────────────────────────────────────────────────────┐
  │              WEEKLY STRATEGY REVIEW                    │
  │                                                       │
  │  INPUT:                                               │
  │  • 7-day aggregated metrics                           │
  │  • All daily insights from the week                   │
  │  • Training data stats (positive/negative counts)     │
  │                                                       │
  │  DECISIONS:                                           │
  │  ├── Change target industries?                        │
  │  ├── Adjust pricing ($299 → $249 or $349)?           │
  │  ├── Shift email tone/style?                          │
  │  ├── Change geographic focus?                         │
  │  ├── Adjust email volume?                             │
  │  └── Ready for LoRA training?                         │
  │                                                       │
  │  SIDE EFFECTS:                                        │
  │  ├──▶ evaluate_rules()     (deactivate regressions)  │
  │  ├──▶ confidence_decay()   (-0.1 per 30d unvalidated)│
  │  ├──▶ analyze_ab_results() (promote A/B winners)     │
  │  └──▶ run_lora_training()  (if 500+ examples)        │
  └─────────────────────────────────────────────────────┘
```

### Level 5: Nightly Self-Modification (sleep cycle)
```
  ┌─────────────────────────────────────────────────────────────┐
  │                    SLEEP CYCLE (2 AM)                         │
  │                                                               │
  │  PHASE 0: MEMORY GC                                          │
  │  • Prune learnings: confidence < 0.4 older than 90 days      │
  │  • Deduplicate: similarity > 0.85 → keep highest confidence  │
  │  • Flag contradictory rules for Alpha/Beta debate             │
  │                                                               │
  │  PHASE A: SNAPSHOT                                            │
  │  ┌─────────────────────────────────────────────────────┐     │
  │  │ metrics + errors + decisions + rules + source_quality│     │
  │  │ + self_models + soul_docs + git_history              │     │
  │  │ + MAGMA causal chains (if enabled)                   │     │
  │  └─────────────────────────────────────────────────────┘     │
  │                          │                                    │
  │  PHASE B: DEBATE         ▼                                    │
  │  ┌──────────┐    ┌──────────────┐    ┌──────────┐           │
  │  │  ALPHA   │───▶│  3-5 change  │───▶│  BETA    │           │
  │  │ (Opus)   │    │  proposals   │    │ (Opus)   │           │
  │  │ Proposer │    │              │    │ Attacker │           │
  │  └──────────┘    └──────────────┘    └────┬─────┘           │
  │                                           │                   │
  │                    ┌──────────────────────┘                   │
  │                    ▼                                           │
  │            APPROVE / MODIFY / REJECT                          │
  │                    │                                           │
  │  PHASE C: APPLY    ▼                                          │
  │  ┌─────────────────────────────────────────────────────┐     │
  │  │ soul_doc_edit  → modify soul/soul_copy.md           │     │
  │  │ config_change  → adjust pricing, targeting           │     │
  │  │ rule_change    → activate/deactivate titan_rules     │     │
  │  │                                                       │     │
  │  │ GUARDRAILS:                                           │     │
  │  │ • Compliance lines IMMUTABLE (soul_copy.md 34-42)    │     │
  │  │ • Pricing: ±$50 max, range $149-$499                 │     │
  │  │ • Max 5 proposals per cycle                           │     │
  │  │ • All changes recorded in agent_decisions             │     │
  │  │ • Git commit with cycle summary                       │     │
  │  └─────────────────────────────────────────────────────┘     │
  └─────────────────────────────────────────────────────────────┘
```

### Level 6: Model Fine-Tuning (when ready)
```
  ┌─────────────────────────────────────────────────────────────┐
  │                    LoRA FINE-TUNING                           │
  │                                                               │
  │  TRIGGER: weekly_strategy says "ready_for_training"           │
  │  GATE: ≥500 labeled examples + not trained in 7 days + budget │
  │                                                               │
  │  ┌────────────┐    ┌───────────────┐    ┌──────────────┐    │
  │  │  Export     │───▶│ Cloud GPU     │───▶│  Download    │    │
  │  │  JSONL      │    │ (Vast.ai)    │    │  adapter     │    │
  │  │  (30-day    │    │              │    │  weights     │    │
  │  │   window)   │    │ OPLoRA on    │    │              │    │
  │  │             │    │ Qwen2.5-14B  │    │              │    │
  │  │ + recency   │    │ 3 epochs     │    │              │    │
  │  │   boost     │    │ 2-4 hours    │    │              │    │
  │  └────────────┘    └───────────────┘    └──────┬───────┘    │
  │                                                 │             │
  │                                                 ▼             │
  │                                          ┌──────────────┐    │
  │                                          │ Ollama       │    │
  │                                          │ (merge as    │    │
  │                                          │  custom      │    │
  │                                          │  model)      │    │
  │                                          └──────────────┘    │
  │                                                               │
  │  RESULT: Next emails use improved model that learned from     │
  │  actual outcomes, not generic training data                    │
  └─────────────────────────────────────────────────────────────┘
```

---

## The Complete Learning Chain (email → better email)

```
  DAY 1: Email composed
  ├─ soul_copy guidelines + active rules + learned tips injected
  ├─ prompt_version_hash stored (traceability)
  └─ training_data row created (outcome: empty)

  DAY 1-3: Prospect opens/ignores
  ├─ Instantly.ai reports opens
  └─ prospect_state updated (opens_without_reply counter)

  DAY 3: Prospect replies "interested"
  ├─ Reply classified → intent: interested → outcome: positive
  ├─ training_data updated: outcome = "positive"
  ├─ Causal attribution: "The line about their Google reviews triggered this"
  │   └─ Stored as sentence_attribution learning
  └─ pending_outcomes created: re-check at 7/14/30 days

  DAY 7: Pending outcome check
  └─ Lead status still "interested" → no change needed

  DAY 14: Pending outcome check
  └─ Lead status now "proposal_sent" → still "positive" ✓

  DAY 30: Pending outcome check
  └─ Lead status now "paid" → still "positive" ✓ (accurate label)

  NIGHTLY: Daily reflection
  ├─ Sees: reply rate up 18% today
  ├─ Extracts insight: "Referencing Google reviews gets 2x replies"
  ├─ Contradiction check: no conflict → stored
  └─ Rule extracted: "Include specific Google review reference" (1.8x, n=47)

  NIGHTLY: Sleep cycle
  ├─ Alpha sees: "Google review mentions correlate with 2x replies"
  ├─ Alpha proposes: Edit soul_copy.md to add "Always reference a specific review"
  ├─ Beta checks: "Is 47 a large enough sample? Yes, p<0.05. APPROVE."
  └─ Backprop applies edit to soul/soul_copy.md

  WEEKLY: Strategy review
  ├─ Rule still holding? Yes (reply rate sustained) → keep active
  ├─ Confidence decay check: validated within 30 days → no decay
  ├─ A/B test: variant B (with review reference) → 2.1x replies → promoted
  └─ ≥500 labeled examples → trigger LoRA training

  TRAINING: Fine-tune model
  ├─ Export 30-day window (recency-boosted)
  ├─ OPLoRA on Qwen2.5-14B (no catastrophic forgetting)
  └─ Deploy to Ollama

  DAY 31+: Next emails
  ├─ Soul copy now says "reference Google reviews"
  ├─ Active rule enforces it
  ├─ Fine-tuned model naturally includes it
  └─ LOOP CONTINUES
```

---

## Cross-Agent Memory Sharing

```
  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ OpenJarvis│     │  Titan   │     │  Hermes  │     │ ClawdBot │
  │ (boss)   │     │ (revenue)│     │ (comms)  │     │ (builder)│
  └────┬─────┘     └────┬─────┘     └────┬─────┘     └────┬─────┘
       │                │                 │                 │
       │  ┌─────────────▼─────────────────▼─────────────────▼──┐
       │  │         SHARED MEMORY LAYER                         │
       │  │                                                     │
       │  │  titan_learnings (category-based)                   │
       │  │  ├─ email_performance  (Titan writes, all read)     │
       │  │  ├─ skill_performance  (ClawdBot writes, Titan reads)│
       │  │  ├─ competitive_intel  (Scout writes, Titan reads)  │
       │  │  ├─ sentence_attribution (Titan writes, all read)   │
       │  │  └─ weekly_strategy    (Titan writes, all read)     │
       │  │                                                     │
       │  │  agent_decisions (all write, all read)              │
       │  │  system_config   (all write, all read)              │
       │  │  events          (broadcast bus)                     │
       └──▶  task_queue      (inter-agent work dispatch)        │
          │                                                     │
          └─────────────────────────────────────────────────────┘
```

---

## MAGMA: The Causal Intelligence Layer

```
  WITHOUT MAGMA (flat memory):
  ┌───────────────────────────────────────────────────┐
  │ Alpha sees:                                        │
  │   "Reply rate dropped 12% this week"               │
  │   "47 emails sent, 2 replies"                      │
  │   "Rule #47 active: Use descriptive subjects"      │
  │                                                    │
  │ Alpha proposes: "Experiment with subject lines"    │
  │ (vague, no causal chain, might re-try failed idea) │
  └───────────────────────────────────────────────────┘

  WITH MAGMA (causal graph):
  ┌───────────────────────────────────────────────────┐
  │ Alpha sees:                                        │
  │   CAUSAL CHAIN:                                    │
  │   [Mar-20] Reply rate dropped 12%                  │
  │       ← caused by                                  │
  │   [Mar-19] Subject avg length: 6→9 words           │
  │       ← caused by                                  │
  │   [Mar-18] Rule #47 activated: "descriptive subjs" │
  │       ← caused by                                  │
  │   [Mar-17] Scout: competitor uses longer subjects   │
  │                                                    │
  │ Alpha proposes: "Revert Rule #47"                  │
  │ (specific, causal, targets the root cause)          │
  └───────────────────────────────────────────────────┘
```
