# AEGIS Phase 0 — Context & Threat Modeling

**Audit run:** 2026-04-07 (autonomous pre-launch pass)
**Target:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion`
**Branch:** `claude/charming-elion` (worktree)
**Mode:** Claude-driven (no external scanners installed; degraded signal phase)

## Repository Profile

- **Languages:** Python 3.11 (primary), SQL, YAML, Bash, macOS sandbox-exec profile
- **Frameworks:** asyncio, httpx, FastAPI (Hermes web), Postgres (psycopg), Redis, LiteLLM proxy, Aider, Ollama/MLX, Anthropic SDK
- **Daemons (8):** perseus, titan, hermes, clawdbot, conway, deerflow_research, ruflo, openjarvis
- **Shared infra:** `shared/` 21 entries (12 modules + 9 new packages from Phase 42.5 v2)
- **Recent commits in scope:** `30770c0`, `53dfecc`, `d9fdc6a` — Phase 42.5 v2 implementation (~50 new files, 9000+ LOC)
- **Tests:** 50+ test files in `tests/`; 7 new Phase 40-44 test files added (982 LOC)
- **Migrations:** 9 SQL files; **046 + 047 NOT yet applied** (tier_spend_log + llm_shadow_diffs schemas)

## Audit Scope (in)

- All Phase 42.5 v2 new files: `shared/tiers.py`, `tier_classifier.py`, `semantic_cache.py`, `lead_worker.py`, `spend_alerts.py`, `aider/`, `voice/`, `imagegen/`, `verifier/`, `escalation_log/`
- New infra: `litellm/sandboxes/verifier.sb`, `config/litellm_config.yaml`, `config/langfuse_evals.yaml`
- Spike scripts: `scripts/spikes/run_airllm_spike.py`, `run_gbnf_spike.py`, `run_litellm_hook_spike.py`
- Migrations: `scripts/migrations/046-tier-spend-tracking.sql`, `047-shadow-diffs.sql`
- Migration scripts: `scripts/migrate_to_litellm.py`, `rollback_litellm.py`
- Existing daemons (integration audit only — confirm wiring)
- Worktree drift vs `main`

## Out of Scope

- Running daemons (no Studio yet — see RUN.md)
- Production secrets / live API call execution
- macOS sandbox-exec actually loaded (only static profile review)
- Performance benchmarks (no Mac Studio M4 Max in this audit environment)

## Threat Model (top categories)

1. **Stale-base drift** — worktree based on outdated code; new modules integrate with stale APIs
2. **Wired-but-unused** — new tier/classifier/cache modules built but no daemon imports them
3. **Sandbox profile not validated** — verifier.sb is untested macOS sandbox-exec rules; one syntax error = no isolation
4. **Prompt injection in escalation log / verifier** — untrusted LLM output flows into pytest sandbox + DB log
5. **Migration gap** — code references `tier_spend_log` and `llm_shadow_diffs` tables that don't exist in DB
6. **Spike scripts in main tree** — never run, no error handling, may shadow real behavior if anyone runs them
7. **Cost runaway** — 11 tier definitions with 28 model entries; misroute = unbounded OpenRouter spend
8. **Operator-secret leakage** — escalation log captures full LLM transcripts; redactor regex coverage gaps = PII leak
9. **No real API key checking** — LiteLLM master key in env var, no rotation policy, no audit log
10. **Voice loop attack surface** — Parakeet → intent router → daemon dispatch; injection via spoken command

## Risk Profile

- **Business criticality:** Pre-launch / pre-revenue. No customer impact YET, but ship date is days away.
- **Data sensitivity:** Operator wallet keys (Conway), API keys, lead PII, escalation logs (full LLM transcripts)
- **Exposure surface:** Currently zero (all local, no public endpoints). Post-launch: Telegram, war-room dashboard, possibly Hermes inbound
- **Blast radius if Phase 42.5 v2 fails:** Total daemon outage on cutover (no rollback path tested)

## Phase 0 Output

Context established. Proceeding to Phase 1 with degraded signal sources (no Trivy/Semgrep/Gitleaks/Checkov/Syft/Grype installed — only Claude-driven static review and `git`).
