# Requirements: Intel Integration

**Defined:** 2026-03-29
**Core Value:** Every email reads like a human who actually looked at the business, and the system continuously improves how it judges quality.

## v1 Requirements

### Prerequisites (Phase 0a)

- [ ] **FIX-01**: Conway wallet INSERT column order fixed
- [ ] **FIX-02**: Conway keystore uses env-var password
- [ ] **FIX-03**: Conway survival tier JSONB writes fixed
- [ ] **FIX-04**: Signed skill manifests for loader

### Infrastructure (Phase 0b)

- [ ] **ASYNC-01**: WorkflowEngine.run() async with sync wrapper
- [ ] **ASYNC-02**: All call sites migrated
- [ ] **CONTRACT-01**: Interface contracts (shared/contracts.py)
- [ ] **OBS-01**: Observability baseline metrics

### Agent DNA (Phase 1)

- [ ] **DNA-01**: Universal engineering DNA document
- [ ] **DNA-02**: Per-daemon DNA profiles
- [ ] **DNA-03**: Opt-in DNA loader with 500-token cap
- [ ] **DNA-04**: Circuit breaker on elevated error rates
- [ ] **DNA-05**: All 5 daemons DNA-injected

### Anti-Slop (Phase 2)

- [ ] **SLOP-01**: 5-dimension quality scorer
- [ ] **SLOP-02**: Email pipeline quality gate
- [ ] **SLOP-03**: Best-of-N rewrite loop
- [ ] **SLOP-04**: Good-enough threshold
- [ ] **SLOP-05**: ClawdBot site copy gate
- [ ] **SLOP-06**: Secret detection regex
- [ ] **SLOP-07**: quality_scores table
- [ ] **SLOP-08**: Per-context thresholds

### DeerFlow Memory (Phase 3)

- [ ] **MEM-01**: daemon_memory table with expiry + row cap
- [ ] **MEM-02**: In-process working memory
- [ ] **MEM-03**: Episodic memory with daily cleanup
- [ ] **MEM-04**: Semantic memory with MAGMA compression
- [ ] **MEM-05**: JSON cache write-through
- [ ] **MEM-06**: Daemon startup memory injection
- [ ] **MEM-07**: Per-daemon memory isolation
- [ ] **MEM-08**: Memory stats monitoring view

### DeerFlow Middleware (Phase 4)

- [ ] **MW-01**: Async middleware protocol
- [ ] **MW-02**: Per-pipeline middleware config
- [ ] **MW-03**: MemoryMiddleware
- [ ] **MW-04**: DNAGuardMiddleware
- [ ] **MW-05**: AntiSlopMiddleware
- [ ] **MW-06**: TelemetryMiddleware
- [ ] **MW-07**: stage_metrics table
- [ ] **MW-08**: Middleware ordering config

### RLM Context (Phase 5)

- [ ] **RLM-01**: Recursive compose loop
- [ ] **RLM-02**: Mem0 write path
- [ ] **RLM-03**: Qdrant read path
- [ ] **RLM-04**: Per-email + monthly spend caps
- [ ] **RLM-05**: Haiku evaluation (reuses anti-slop scorer)
- [ ] **RLM-06**: Shadow mode A/B testing
- [ ] **RLM-07**: Feature flag with instant rollback

### Post-Quantum Crypto (Phase 6)

- [ ] **PQC-01**: ARM64 validation spike
- [ ] **PQC-02**: Hybrid ML-KEM-768 + AES-256
- [ ] **PQC-03**: ML-DSA-65 transaction signatures
- [ ] **PQC-04**: SecureConfig for .env encryption
- [ ] **PQC-05**: Dual-key migration table
- [ ] **PQC-06**: Per-agent key derivation
- [ ] **PQC-07**: Key rotation mechanism
- [ ] **PQC-08**: Feature flag with rollback

### Adaptive Thresholds (Phase 7)

- [ ] **ADAPT-01**: Thompson sampling bandit
- [ ] **ADAPT-02**: DB-stored thresholds
- [ ] **ADAPT-03**: Pipeline outcome training signal
- [ ] **ADAPT-04**: Warm-start with current priors
- [ ] **ADAPT-05**: Concurrent A/B experiments
- [ ] **ADAPT-06**: meta_evaluations table
- [ ] **ADAPT-07**: Feature flag
- [ ] **ADAPT-08**: CC BY-NC-SA compliance verification

### Phase 8: TRIBE v2 Neuro-Scorer

- [ ] **NEURO-01**: MPS validation spike — TRIBE v2 inference benchmarked on M4 32GB
- [ ] **NEURO-02**: TRIBE v2 model loading with lazy load/unload for memory management
- [ ] **NEURO-03**: ROI extraction — fsaverage5 atlas regions mapped to 4 cognitive dimensions
- [ ] **NEURO-04**: 4-dimension scoring (self-relevance, trust, cognitive ease, emotional resonance) with normalization
- [ ] **NEURO-05**: Email pipeline integration — score after compose, gate low-scoring drafts
- [ ] **NEURO-06**: All content types scored — emails, site copy, follow-ups, alerts
- [ ] **NEURO-07**: neuro_scores JSONB stored on email_sequences table
- [ ] **NEURO-08**: Closed-loop learning — daily outcome-correlation analysis
- [ ] **NEURO-09**: Neural rule auto-extraction when statistical significance reached
- [ ] **NEURO-10**: Segment-specific neural profiles computed and used in compose prompts

## Out of Scope

| Feature | Reason |
|---------|--------|
| HyperAgents codebase | CC BY-NC-SA 4.0 — non-commercial only |
| Quantum computing (QAOA) | Trading project only |
| Agent spawning (cell_division) | Separate effort, hollow stub |
| On-chain registry (ERC-8004) | Separate effort, address is zeros |
| War Room dashboard additions | Phase 8 / separate milestone |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FIX-01 through FIX-04 | Phase 0a | Pending |
| ASYNC-01, ASYNC-02, CONTRACT-01, OBS-01 | Phase 0b | Pending |
| DNA-01 through DNA-05 | Phase 1 | Pending |
| SLOP-01 through SLOP-08 | Phase 2 | Pending |
| MEM-01 through MEM-08 | Phase 3 | Pending |
| MW-01 through MW-08 | Phase 4 | Pending |
| RLM-01 through RLM-07 | Phase 5 | Pending |
| PQC-01 through PQC-08 | Phase 6 | Pending |
| ADAPT-01 through ADAPT-08 | Phase 7 | Pending |
| NEURO-01 through NEURO-10 | Phase 8 | Pending |

**Coverage:**
- v1 requirements: 56 total
- Mapped to phases: 56
- Unmapped: 0

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-29 after mega-plan initialization (full scope)*
