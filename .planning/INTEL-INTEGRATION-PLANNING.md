# Objective Hertz — Intel Integration

> Integrate 7 research references into OH's 5-daemon autonomous revenue system to upgrade context retrieval, self-improvement, content quality, runtime resilience, engineering discipline, and cryptographic security.

**Created:** 2026-03-29
**Type:** Application (enhancement to existing system)
**Stack:** Python 3.11+ | OpenJarvis | FastAPI | Postgres | Mem0 | Qdrant | Docker | Claude API | Ollama
**Skill Loadout:** PAUL (managed build), AEGIS (security audit), GSD (phased execution)
**Quality Gates:** ruff lint, pytest suite, anti-slop scoring, PQC key validation, middleware chain tests

---

## Problem Statement

Objective Hertz has the daemon architecture for autonomous revenue generation but runs on first-generation intelligence. Seven specific deficiencies limit its effectiveness:

1. **Context loss** — Titan's 10-stage pipeline passes data through Postgres as flat rows. By the time `email_compose.py` runs, rich research context from `lead_discovery` and `lead_research` has been compressed to database columns. Result: generic emails that could be sent to anyone.

2. **Static self-improvement** — Self-improvement infrastructure exists (`expansion.py`, `magma.py`, `backprop.py`, `cell_division.py`, `sleep_cycle.py`) but uses fixed evaluation criteria. A skill that produces fewer but higher-value leads gets rejected because `conversion_rate > 0.05` is hardcoded.

3. **No quality gate** — Emails and site copy ship without naturalness checks. AI-sounding emails tank open rates and damage the brand. No anti-slop check exists between composition and delivery.

4. **Fragile runtime** — All 5 daemons share the same filesystem with no sandbox isolation, no persistent memory across restarts, and no middleware chain for cross-cutting concerns like compliance and budget enforcement.

5. **No engineering DNA** — Each daemon operates independently. When a new skill or daemon is added, it doesn't automatically inherit compliance gates, budget checks, quality requirements, or security practices. The `soul/` directory has personality guidelines but no engineering principles.

6. **Cryptographic vulnerability** — Conway manages Base L2 USDC wallets with private keys. OH stores live API keys in `.env`. Both are vulnerable to harvest-now-decrypt-later (HNDL) quantum attacks. No post-quantum protection exists.

7. **No quantum readiness** — The trading project (separate from OH) will need quantum portfolio optimization at scale (20-50 assets). Amazon Braket provides the path, but this is future work — scoped OUT of OH, noted here for cross-project awareness.

**Audience:** This is infrastructure for an autonomous system. No human end-users — the daemons are the users.
**Why build vs buy:** These are architectural enhancements to a custom system. No off-the-shelf solution addresses OH's specific daemon topology.

---

## Tech Stack

Existing stack is stable. New dependencies are additive — nothing gets replaced.

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Python 3.11+ | Existing. All daemons are Python. |
| Orchestrator | OpenJarvis | Existing. DAG runner, A2A, security, tools. |
| Database | Postgres (23 tables) | Existing. New tables: `quality_scores`, `daemon_memory`, `meta_evaluations`, `encrypted_keys`. |
| Vector Store | Mem0 (write) + Qdrant (read) | Existing. RLM integration: Mem0 stores full research context, Qdrant serves fast vector retrieval for recursive queries. |
| Containers | Docker Compose | Existing. New: HyperAgents meta-agent isolation, DeerFlow sandbox provider. |
| LLM | Claude API (Opus/Sonnet) + Ollama (Haiku) | Existing. RLM recursive sub-calls use Haiku for cost efficiency. |

### New Dependencies

| Package | Version | Purpose | Risk |
|---------|---------|---------|------|
| `recursive-llm` | latest | RLM REPL for recursive context retrieval | MIT, well-tested, LiteLLM-based |
| `liboqs-python` | latest | Post-quantum KEM + signatures | OQS project, NIST algorithms, requires cmake |
| `cryptography` | existing | Fernet symmetric encryption (used with PQ shared secrets) | Already in stack |
| `RestrictedPython` | latest | Safe REPL execution for RLM code | Zope project, production-grade sandbox |

### Research Needed
- `recursive-llm` async performance under concurrent Titan pipeline cycles
- `liboqs-python` build requirements on macOS ARM64 (M4)
- Docker resource limits for HyperAgents meta-agent containers

---

## Data Model

### New Tables

| Table | Key Fields | Relationships | Purpose |
|-------|-----------|---------------|---------|
| `quality_scores` | id, content_type, content_id, naturalness, specificity, conciseness, authenticity, overall, model_used, created_at | FK to pipeline_tasks, email_campaigns | Anti-slop scoring for all generated content |
| `daemon_memory` | id, daemon_name, fact_content, category, confidence, source, created_at, updated_at | None (standalone per-daemon) | DeerFlow persistent memory pattern |
| `meta_evaluations` | id, evaluation_id, criteria_before, criteria_after, outcome_metric, generation, created_at | FK to titan_learnings | HyperAgents criteria evolution tracking |
| `encrypted_keys` | id, wallet_id, ciphertext_hex, encrypted_key, algorithm, pq_public_key_hex, migration_status, created_at | FK to agent_wallets | PQ-encrypted wallet keys (dual-key during migration) |

### Modified Tables

| Table | Change | Why |
|-------|--------|-----|
| `leads` | Add `mem0_context_id` column | Reference to full research context in Mem0 for RLM retrieval |
| `pipeline_tasks` | Add `middleware_log` JSONB column | Track which middleware ran per task and their outputs |
| `agent_wallets` | Add `encryption_version` column | Track PQ migration status (v1=legacy, v2=pq-hybrid) |

### Notes
- `quality_scores` is standalone (not JSONB on pipeline_tasks) to enable trend analysis across time and A/B testing of composition strategies
- `daemon_memory` uses write-through pattern: JSON cache at `data/{daemon}_memory.json` for fast startup, Postgres as source of truth
- `encrypted_keys` supports dual-key period: both legacy and PQ-encrypted versions valid for 30 days during migration

---

## API Surface

No new external-facing APIs. All changes are internal daemon enhancements via A2A protocol.

### New A2A Capabilities

| Daemon | Capability | Purpose |
|--------|-----------|---------|
| Titan | `compose_with_rlm` | Recursive context retrieval during email composition |
| Titan | `evaluate_with_meta` | HyperAgents-enhanced skill evaluation with evolving criteria |
| All | `check_quality` | Anti-slop quality gate for any generated content |
| All | `get_dna` | Load current engineering DNA for system prompt injection |
| Conway | `encrypt_key_pq` | PQ-safe key encryption |
| Conway | `decrypt_key_pq` | PQ-safe key decryption |
| Perseus | `get_daemon_memory` | Retrieve persistent facts for a daemon |
| Perseus | `set_daemon_memory` | Store/update persistent facts |

### Internal vs External
- **Public endpoints:** None changed
- **Internal A2A:** 8 new capabilities above
- **Hermes Web Dashboard:** New views for quality scores, meta-evaluations, daemon memory, PQC status

---

## Deployment Strategy

### Incremental Rollout

Each phase deploys independently. No big-bang migration.

| Phase | Migration Required | Rollback Strategy | Shadow Period |
|-------|-------------------|-------------------|--------------|
| 1 (DNA) | None | Remove DNA from LLM calls | None needed |
| 2 (Anti-Slop) | `quality_scores` table | Drop table, remove gate | 1 week shadow scoring without blocking |
| 3 (Memory) | `daemon_memory` table | Drop table, remove JSON cache | 1 week dual-write |
| 4 (Middleware) | `pipeline_tasks.middleware_log` column | Remove middleware chain, restore direct calls | 2 week parallel pipelines |
| 5 (RLM) | `leads.mem0_context_id` column | Revert to flat DB query in email_compose | 2 week A/B test (RLM vs original) |
| 6 (PQC) | `encrypted_keys` table, `agent_wallets.encryption_version` | 30-day dual-key period, rollback to legacy keys | 30-day dual-key |
| 7 (HyperAgents) | `meta_evaluations` table | Revert to fixed evaluation criteria | N cycles shadow alongside existing eval |

### Local Development
- All existing Docker Compose services unchanged
- New: `docker-compose.hyperagents.yml` overlay for meta-agent isolation containers
- Daemon memory JSON cache stored in `data/` directory (gitignored)

### Production
- Database migrations via `scripts/migrations/`
- Per-phase feature flags in daemon configs
- Hermes alerts for migration status and rollback triggers

---

## Security Considerations

### PQC (Primary Security Enhancement)
- **Conway wallets:** ML-KEM-768 hybrid encryption (AES-256 + ML-KEM). If either breaks, the other protects.
- **Wallet transactions:** ML-DSA-65 signatures on all transfer operations.
- **.env secrets:** `SecureConfig` class with PQ encryption at rest for `STRIPE_SECRET_KEY`, `INSTANTLY_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `CONWAY_WALLET_PRIVATE_KEY`.
- **Migration:** Backup + 30-day dual-key period. Both legacy and PQ keys valid during transition. Old keys deleted only after validation.

### RLM Sandbox
- `RestrictedPython` executes model-generated code in the REPL. No filesystem access, no network access, no imports beyond whitelisted modules.
- REPL output capped at 8,192 characters per turn.
- Recursion depth bounded by `max_depth` config.

### HyperAgents Isolation
- Meta-agent code execution in Docker containers (never on host).
- Containers have no network access, read-only filesystem except `/tmp`.
- Evaluation results extracted via stdout, not filesystem.

### Anti-Slop Secret Detection
- 25+ regex patterns scan ALL outgoing content for leaked secrets (AWS keys, Stripe, Anthropic, GitHub, Slack, JWTs, private keys).
- Runs on emails (Titan), site copy (ClawdBot), DMs, and any new outward-facing channel.

### Agent DNA Compliance
- DNA enforces CAN-SPAM (unsubscribe, physical address), LFPDPPP (Baja Swarm), suppression list checks, budget guards.
- Every daemon inherits compliance automatically via system prompt injection.

---

## UI/UX Needs

### War Room Dashboard Additions

| View | Purpose | Complexity |
|------|---------|------------|
| Quality Scores | Trend lines for naturalness/specificity/conciseness/authenticity over time. Alert on drops. | Medium — chart components + DB query |
| Meta-Evaluation Viewer | HyperAgents evolution: how criteria changed, why, before/after comparison | Medium — timeline + diff view |
| Daemon Memory Inspector | Browse persistent facts per daemon. Search, filter, confidence levels. | Low — table view with filters |
| PQC Status | Key encryption migration progress. Algorithm versions. Dual-key countdown. | Low — status cards |
| DNA Compliance | Per-daemon audit: DNA loaded, LLM calls include DNA, gates active | Low — checklist view |

### Design System
- Existing War Room uses React + FastAPI. New views follow existing patterns.
- Desktop-only (admin dashboard).

---

## Integration Points

| Integration | Type | Impact | Auth |
|------------|------|--------|------|
| Mem0 | SDK | RLM write path — stores full research context per lead | API key |
| Qdrant | SDK | RLM read path — fast vector retrieval during email composition | API key |
| Instantly.ai | API | Anti-slop gate before emails hit Instantly. Quality score logged per campaign. | API key |
| Netlify | API | Anti-slop gate on ClawdBot site copy before deployment | API key |
| Docker | Container API | HyperAgents meta-agent isolation + DeerFlow sandbox provider | Local socket |
| Telegram | Bot API | Hermes alerts for quality drops, PQC migration, daemon memory anomalies | Bot token |
| Stripe/Wise | API | No change to payment flow. Conway PQC only affects key storage. | API keys (PQ-encrypted at rest) |

---

## Phase Breakdown

### Phase 1: Agent DNA
- **Build:** `soul/engineering_dna.md` with universal principles (security, compliance, budget, quality, resilience, decisions, learning). `shared/dna.py` loader. Inject DNA into every LLM call via `shared/llm_client.py`.
- **Testable:** Every daemon's LLM calls include DNA in system prompt. DNA audit checklist passes for all 5 daemons.
- **Outcome:** Every agent in the system inherits engineering principles automatically. New skills/daemons get compliance, budget, and quality rules for free.

### Phase 2: Anti-Slop Quality Gate
- **Build:** Quality gate between `email_compose` and `email_send` in Titan pipeline. Same gate for ClawdBot site copy before Netlify deploy. `quality_scores` table. Anti-slop system prompt. Fresh-agent review pattern (composer agent != reviewer agent). Secret detection regex on all outgoing content.
- **Testable:** Score distribution across N emails. Before/after comparison on naturalness, specificity. Secret detection catches injected test patterns.
- **Outcome:** No AI-sounding emails or site copy ships. Quality scores logged and trendable. Revenue impact measurable via open rate changes.

### Phase 3: DeerFlow Persistent Memory
- **Build:** `daemon_memory` table + JSON cache write-through pattern. Startup injection for all daemons. Automatic fact extraction from daemon operations. Top-15 facts injected into daemon system prompts. Deduplication via normalized matching.
- **Testable:** Daemon restart preserves planning context. Perseus remembers "I was about to run lead discovery on restaurants in Portland" instead of replanning from scratch.
- **Outcome:** Daemons have persistent memory across restarts. Reduces cold-start planning overhead. Enables smarter decision-making with historical context.

### Phase 4: DeerFlow Middleware Chain
- **Build:** Cross-cutting middleware chain for Titan pipeline: guardrail middleware (anti-slop, compliance), budget middleware (pre-call spend check), summarization middleware (token reduction), memory middleware (async fact extraction), subagent limit middleware (concurrency). Mandatory for all stages.
- **Testable:** Middleware chain executes on every pipeline stage. Budget middleware blocks over-budget calls. Compliance middleware catches CAN-SPAM violations. `middleware_log` JSONB populated per task.
- **Outcome:** Cross-cutting concerns enforced uniformly. No stage can bypass anti-slop, compliance, or budget checks. Cleaner than per-stage code.

### Phase 5: RLM Recursive Context Retrieval
- **Build:** `recursive-llm` integration in `email_compose.py`. Mem0 write path: store full research output with `mem0_context_id` on leads. Qdrant read path: recursive queries into full research during composition. `max_depth=2`, `recursive_model=haiku` for sub-calls.
- **Testable:** A/B test: RLM-composed emails vs flat-context emails. Measure specificity score difference. Verify recursive queries hit Qdrant, not main model context.
- **Outcome:** Emails reference actual details (review quotes, pricing gaps, competitor names) instead of generic templates. Specificity scores increase measurably.

### Phase 6: Post-Quantum Cryptography
- **Build:** `QuantumSafeWallet` class in `conway/wallet.py` using ML-KEM-768 + AES-256 hybrid. `SecureConfig` class in `shared/config.py` for .env secret encryption at rest. ML-DSA-65 transaction signing. `encrypted_keys` table. 30-day dual-key migration with backup.
- **Testable:** Wallet keys encrypt/decrypt correctly with PQ. .env secrets encrypted at rest. Dual-key period: both legacy and PQ keys work. Transaction signatures verify.
- **Outcome:** Conway wallet keys and API secrets protected against harvest-now-decrypt-later quantum attacks. NIST-standardized algorithms. Hybrid approach ensures defense-in-depth.

### Phase 7: HyperAgents Metacognitive Self-Modification
- **Build:** Meta-evaluation loop in `titan/expansion.py`. Docker-isolated meta-agent that reviews past adopt/reject decisions, discovers that evaluation criteria miss important signals (e.g., `avg_deal_value` not just `conversion_rate`), and self-modifies evaluation logic. `meta_evaluations` table. Population-based exploration (archive of prior agent variants).
- **Testable:** Meta-agent runs N shadow cycles alongside existing evaluation. Criteria evolution logged. New criteria produce better adopt/reject decisions (measured by revenue outcome of adopted skills).
- **Outcome:** Titan's expansion engine continuously improves HOW it evaluates new capabilities, not just what capabilities it evaluates. Self-accelerating improvement loop.

---

## Skill Loadout & Quality Gates

### Skills Used During Build

| Skill | When It Fires | Purpose |
|-------|--------------|---------|
| GSD | All phases | Phased execution with atomic commits |
| PAUL | Quality-critical phases (PQC, HyperAgents) | Acceptance criteria, BDD verification |
| AEGIS | Post-Phase 7 | Full security audit of integrated system |
| quality-gate | Each phase plan | Anti-slop scoring of plans themselves |

### Quality Gates

| Gate | Threshold | When |
|------|-----------|------|
| ruff lint | 0 errors | Every phase |
| pytest suite | All pass | Every phase |
| Anti-slop score | > 0.7 naturalness | Phases 2, 5 (email-related) |
| PQC key validation | Encrypt/decrypt roundtrip | Phase 6 |
| Middleware chain coverage | All stages covered | Phase 4 |
| Shadow rollout | No degradation vs baseline | Phases 5, 7 |

---

## Design Decisions

1. **Standalone quality_scores table over JSONB column**: Enables trend analysis, A/B comparison of compose strategies, and cross-task quality reporting. Worth the extra join.
2. **Mem0 writes, Qdrant reads for RLM**: Clean separation. Mem0 handles embedding and storage during research. Qdrant serves fast vector retrieval during composition. No double-write.
3. **JSON cache + Postgres for daemon memory**: Fast daemon startup (read JSON), persistent truth (Postgres). Write-through pattern. JSON is gitignored.
4. **Mandatory middleware for all stages**: No stage can bypass compliance, budget, or quality checks. Prevents the "I forgot to add the gate" failure mode.
5. **30-day dual-key for PQC migration**: Both legacy and PQ-encrypted keys valid during transition. Zero-downtime migration. Old keys deleted only after manual validation.
6. **Hybrid PQ encryption (AES-256 + ML-KEM-768)**: Defense-in-depth. If either algorithm is broken, the other still protects.
7. **Quantum computing scoped OUT of OH**: QAOA portfolio optimization is for the trading project only. OH gets PQC (defensive) but not quantum computing (offensive).
8. **HyperAgents CC BY-NC-SA 4.0 license**: Non-commercial research only. OH's implementation will be inspired by the pattern (metacognitive self-modification) but custom-built, not using their codebase directly.

---

## Open Questions

1. `recursive-llm` async performance under concurrent Titan pipeline cycles — needs benchmarking
2. `liboqs-python` build on macOS ARM64 (M4) — may need Homebrew cmake + custom build flags
3. Docker resource limits for HyperAgents containers — how much RAM/CPU per meta-agent run?
4. DeerFlow middleware ordering — does budget check run before or after anti-slop? (Budget check first — prevents spending on content that gets rejected)
5. RLM REPL safety — is RestrictedPython sufficient, or do we need Docker isolation for the REPL too?
6. HyperAgents generation count — how many meta-evaluation cycles before the criteria stabilize?

---

## Next Actions

- [ ] Run mega-plan Stage 0 (memory-scorer + base:orientation)
- [ ] Run mega-plan Stage 0.5 (CARL domain rules)
- [ ] Run mega-plan Stage 1 (map-codebase)
- [ ] Continue through Stages 2-8 of mega-plan pipeline

---

## References

- `intel/recursive-language-models/REFERENCE.md` — MIT RLMs (arXiv:2512.24601)
- `intel/hyperagents/REFERENCE.md` — Meta HyperAgents (arXiv:2603.19461)
- `intel/anti-slop/REFERENCE.md` — Anti-slop tools (60+ rules, 9 categories)
- `intel/deerflow/REFERENCE.md` — ByteDance DeerFlow 2.0 (37k stars)
- `intel/agent-dna/REFERENCE.md` — Two-layer DNA + Skills architecture
- `intel/post-quantum-crypto/REFERENCE.md` — liboqs-python, ML-KEM-768, ML-DSA-65
- `intel/quantum-computing/REFERENCE.md` — Amazon Braket, QAOA (trading project only)

---

*Last updated: 2026-03-29*
