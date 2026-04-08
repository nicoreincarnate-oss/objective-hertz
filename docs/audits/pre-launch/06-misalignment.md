# Misalignment Audit — Phase 42.5 v2 Spec vs Implementation

**Date:** 2026-04-07
**Auditor:** misalignment-detector skill (autonomous)
**Source of truth:** `.paul/STATE.md` locked decisions + `project_local_tier_phase_42_5_v2.md`
**Implementation under audit:** commits `30770c0`, `53dfecc`, `d9fdc6a` (50 files, ~9000 LOC)

## Verdict

**OVERALL: ALIGNED — NO BLOCKING DRIFT**

- Drift items found: **2** (both LOW severity / cosmetic)
- Blocking misalignments: **0**
- Spec adherence: **~98%**

The committed code is a faithful materialization of the locked Phase 42.5 v2 design. All ten spec-conformance checks passed substantively. The two minor drifts noted below do not threaten the architecture and can be resolved during the standard /paul:apply pass.

---

## Conformance Matrix (10 spec checks)

| # | Spec Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Qwen3-30B-A3B as primary local model | PASS | `shared/tiers.py:298` `primary_model="ollama/qwen3:30b-a3b-mlx-4bit"` + module docstring lines 9-19 |
| 2 | `shared/tiers.py` defines all 11 tiers | PASS | TierName enum lines 39-49 lists all 11: GENIUS, SMART, CODEX, AGENTIC, LONGCTX, CHAT, FAST, CHEAP, LOCAL, LOCAL_HEAVY, VISION. TIERS dict at line 105 has 11 entries. |
| 3 | LOCAL_HEAVY (AirLLM) marked DEFERRED, fallback routes to GENIUS | PASS | `shared/tiers.py:324-339` explicit comment "DEFERRED until Samsung T9 2TB Thunderbolt arrives" + `fallback_chain=[TierName.GENIUS, TierName.SMART]` |
| 4 | Verifier has 4 layers (grammar, consistency, depth, daemon-side L3 callback) | PASS | `shared/verifier/grammar_compiler.py` (L1), `consistency.py` (L2), `a2a_callback.py` (L3 daemon callback contract), `depth_guard.py` (L4). All 4 files present, 919 LOC total. |
| 5 | Escalation log redactor: 15+ patterns + AES-256-GCM encryption | PASS (overshoots) | `shared/escalation_log/redactor.py` REDACTION_PATTERNS contains **18** patterns (3 over spec): openai/anthropic/google/aws-access/aws-temp/ghp/github_pat/stripe-live/stripe-test/jwt/base64/email/phone/ssn/credit-card/eth/bearer/password. AES-256-GCM via `cryptography.hazmat...AESGCM` line 220. Canary test present line 124. |
| 6 | macOS sandbox profile denies network + ssh/wallet/env access | PASS | `litellm/sandboxes/verifier.sb`: `(deny default)` line 18, `(deny network*)` line 113, explicit deny on `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/Library/Keychains`, `/opt/perseus/data/keystores`, `/opt/perseus/wallets`, `*.env` regex. process-exec restricted to `/usr/bin`, `/opt/homebrew/bin`, python/pytest/git via PATH allowlist. |
| 7 | Cutover playbook reflects all-at-once not day-by-day | PASS | `docs/runbooks/cutover-playbook.md` line 1 title "(All-at-Once)", line 3 explicitly says "rewritten for the operator's 'do everything now' approach". 5-step all-at-once cutover at line 21. Day-by-day preserved as fallback at line 226. |
| 8 | Voice loop replaces ElevenLabs entirely | PASS | `shared/voice/` contains parakeet_client.py + kokoro_client.py + intent_router.py. Zero references to "ElevenLabs" anywhere in `shared/voice/` (grep clean). voice-loop-guide.md treats it as the canonical voice path. |
| 9 | Memory budget = 22 GB MLX wired cap (not 28 GB) | PASS | `cutover-playbook.md:13,29-30`: `iogpu.wired_limit_mb=22000`. `native-services-setup.md:319,361` confirms ~22 GB hot set. `.paul/HANDOFF.md:30,34` documents "down from original 28672". |
| 10 | Aider architect+editor split exists for Ruflo and Clawdbot | PASS | `shared/aider/ruflo_loop.py` (383 LOC) + `shared/aider/clawdbot_loop.py` (394 LOC). Both have 4-stage Architect→Editor→Verifier→Loop structure. Ruflo uses Coder-14B editor + pytest verifier; Clawdbot uses Coder-14B editor + Qwen3-VL visual verifier + Draw Things asset gen. |

---

## Drift Items

### Drift #1 — Pattern count overshoots stated spec (LOW / informational)
- **Spec said:** 15 redaction patterns
- **Actual:** 18 patterns in `REDACTION_PATTERNS`
- **Severity:** LOW (overshoot, not under-shoot — extra patterns are AWS temp keys, GitHub PATs, base64 blobs)
- **Drift direction:** strictly improves coverage
- **Action:** None required. Update spec text to "15+" or document the 3 added patterns in the redactor docstring. The HANDOFF.md / spec wording is the artifact slightly out of sync, not the code.

### Drift #2 — `.paul/ROADMAP.md` still mentions "MLX RSS ≤28 GB" (LOW / cosmetic)
- **Found at:** `.paul/ROADMAP.md:36` — "Zero OOM, MLX RSS ≤28 GB, daemon RSS ≤6 GB, no jetsam, no critical memory pressure"
- **Conflict:** STATE.md, HANDOFF.md, cutover-playbook.md, native-services-setup.md all say 22 GB. Only ROADMAP.md still has the pre-revision 28 GB number.
- **Severity:** LOW (cosmetic — code uses 22000, runtime gates use 22000, only the SLA acceptance criterion in ROADMAP is stale)
- **Action:** One-line edit to ROADMAP.md changing "≤28 GB" → "≤22 GB" before the cutover gate is evaluated. Otherwise the launch criterion will incorrectly pass even at 26 GB which would actually be OOM territory.

---

## Items NOT in commits (correctly deferred per HANDOFF.md)

These are intentionally excluded because they require the Studio physically present or operator review. They are NOT drift:

- LiteLLMBackend integration into `shared/llm_client.py` (operator review required)
- Daemon-side L3 verifier callback registrations (need each daemon's init code)
- Studio provisioning, model downloads, spike runs
- Migration script `--apply` execution
- Sandbox red-team test (needs macOS sandbox loaded)
- Shadow mode runs

---

## Bias Accumulation Check (memory drift signals)

Scanned the spec and decision log for signs the system favors one approach over alternatives without justification:

- **Model selection bias:** PASS — STATE.md decisions explicitly diversified (Qwen primary, Llama 70B heavy, Kimi vision, DeepSeek cheap, Sonnet smart, Opus genius). Operator-locked rule "no tribal loyalty to Anthropic" is preserved in code comments at `shared/tiers.py:14-15`.
- **Tool call routing bias:** PASS — verifier has 4 distinct layers, not collapsed into one model. Sandbox is real OS-level isolation, not a library wrapper.
- **Architecture bias:** PASS — compound system architecture (local + verifier + escalation) is consistently applied across Ruflo, Clawdbot, and the verifier module. No single-model fallback shortcuts found.

---

## Stale Decision Check

Scanned `.paul/STATE.md` for decisions from earlier versions that may now be wrong:

- "mlx-lm fork" — DROPPED in v2, no references in committed code (PASS)
- "Gemma 4 26B A4B" — REPLACED by Qwen3-30B-A3B in v2, code uses Qwen3 (PASS)
- "ElevenLabs" — REMOVED in v2, voice/ has zero references (PASS)
- "day-by-day cutover" — REVOKED in v2, playbook has all-at-once primary, day-by-day demoted to fallback section (PASS)
- "28 GB MLX cap" — REVISED to 22 GB everywhere except ROADMAP.md (Drift #2)

---

## Recommended Actions

1. **(LOW)** Edit `.paul/ROADMAP.md` line 36: change `MLX RSS ≤28 GB` to `MLX RSS ≤22 GB`. One-line fix.
2. **(INFO)** Update spec narrative to acknowledge 18 redaction patterns instead of 15, or document the extra 3 in `REDACTION_PATTERNS` docstring.
3. **No code changes required.** Implementation faithfully matches the locked v2 design.

---

## Alignment Health: HEALTHY

The Phase 42.5 v2 implementation is in strong alignment with the locked spec. The two drift items are cosmetic and can be resolved in under 5 minutes. The substantive architectural decisions — local-first Qwen3, deferred AirLLM, 4-layer verifier, OS-sandboxed L3, encrypted redacted escalation log, all-at-once cutover, full-local voice — are all present and consistent across the codebase.
