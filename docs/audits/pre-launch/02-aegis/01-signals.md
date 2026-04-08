# AEGIS Phase 1 — Automated Signal Gathering

## Tool inventory (`.aegis/MANIFEST.md`)

| Tool | Status | Notes |
|------|--------|-------|
| SonarQube | NOT INSTALLED | Phase signals unavailable for code health metrics |
| Semgrep | NOT INSTALLED | No pattern-based security/correctness scan |
| Trivy | NOT INSTALLED | No filesystem CVE scan |
| Gitleaks | NOT INSTALLED | No secrets-history scan |
| Checkov | NOT INSTALLED | No IaC policy scan (no IaC files anyway) |
| Syft | NOT INSTALLED | No SBOM generated |
| Grype | NOT INSTALLED | No CVE matching |
| git | OK | `/usr/bin/git` available |

**This audit is running in DEGRADED MODE** — signal phase relies on Claude-driven manual scans (Read/Grep/Bash) and git history only. Coverage gap acknowledged in every Phase 2 finding's confidence vector.

## Manual signals collected

### git (commits in scope)

- `30770c0` — Phase 42.5 v2 artifacts (40 files, +9017 LOC, single commit)
- `53dfecc` — Native services + 1TB drive + AirLLM defer (+6579 LOC)
- `d9fdc6a` — Pre-launch audit RUN.md spec (+250 LOC)

All 3 commits are by `nicoreincarnate-oss` over 2026-04-07 evening, all co-authored by Claude. **This is single-author, single-day, ~15k LOC of net new infrastructure** — concentration risk = maximum.

### Static scan (Claude-driven)

- **Syntax check** of all new shared/* modules — all parse cleanly under Python 3.11
- **Hardcoded secrets** — none found in `config/`, `litellm/`, `shared/` (all keys via `os.environ/...`)
- **Deprecated API use** — `shared/spend_alerts.py:143` uses `datetime.utcnow()` (deprecated in 3.12, scheduled removal in 3.13). Minor.
- **TODO/FIXME/NotImplementedError** — zero in new shared modules. Either very polished or all stubs are silent.
- **Daemon imports of new modules** — search across `perseus titan hermes clawdbot conway deerflow_research ruflo openjarvis`:
  - `from shared.tiers` → ZERO daemon imports (only tests)
  - `from shared.tier_classifier` → ZERO daemon imports (only tests)
  - `from shared.semantic_cache` → ZERO daemon imports (only tests)
  - `from shared.lead_worker` → ZERO daemon imports (only tests)
  - `from shared.spend_alerts` → ZERO daemon imports
  - `from shared.aider` → ZERO daemon imports
  - `from shared.voice` → ZERO daemon imports
  - `from shared.imagegen` → ZERO daemon imports
  - `from shared.verifier` → ZERO daemon imports
  - `from shared.escalation_log` → ZERO daemon imports
- **Old llm_client still used everywhere**:
  - perseus/sleep_cycle.py
  - titan/pipeline/{follow_up,close_deal,email_compose,lead_research,lead_discovery}.py
  - titan/{memory,expansion}.py
  - hermes/web/insights.py
  - clawdbot/{site_builder,brain,daemon}.py

### Worktree drift signal

- `shared/llm_client.py` in worktree: **257 lines** (the old Claude-direct + Ollama implementation)
- `shared/llm_client.py` in main repo: **1711 lines** (per audit instructions — contains LiteLLM routing the worktree pretends to add)
- The worktree was branched off a state of llm_client.py that PREDATES the actual integration work. Phase 42.5 v2 commits did not touch llm_client.py, so the worktree still ships the old version.

### File volume by surface

| Surface | New files | LOC |
|---------|-----------|-----|
| `shared/tiers.py` | 1 | 494 |
| `shared/tier_classifier.py` | 1 | 451 |
| `shared/semantic_cache.py` | 1 | 298 |
| `shared/lead_worker.py` | 1 | 216 |
| `shared/spend_alerts.py` | 1 | 189 |
| `shared/verifier/*` | 5 | 676 |
| `shared/aider/*` | 3 | 797 |
| `shared/voice/*` | 4 | 415 |
| `shared/imagegen/*` | 2 | 193 |
| `shared/escalation_log/*` | 2 | 271 |
| `litellm/sandboxes/verifier.sb` | 1 | 134 |
| `config/litellm_config.yaml` | 1 | 319 |
| `config/langfuse_evals.yaml` | 1 | 126 |
| `scripts/migrate_to_litellm.py` | 1 | 349 |
| `scripts/rollback_litellm.py` | 1 | 172 |
| `scripts/spikes/*` | 3 | 798 |
| `scripts/migrations/046,047` | 2 | 221 |
| `tests/test_phase4*` + `test_verifier_layers.py` | 7 | 982 |

## Signal coverage gaps (escalate to Phase 2 confidence)

- **No CVE scan** — cannot verify dependency vulnerabilities
- **No secrets history scan** — only current-tree checked
- **No IaC scan** — N/A (no Terraform/CFN/K8s)
- **No code complexity metrics** — cannot quantify hot spots
- **No git churn analysis** — bus factor estimate is qualitative only

Phase 2 agents must set `evidence_diversity = low` in confidence vectors due to single-source (Claude review) signal base.
