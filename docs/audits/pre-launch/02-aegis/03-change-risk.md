# AEGIS Phase 3 — Change Risk & Reality Gap

Cross-domain synthesis. Staff Engineer + Reality Gap Analyst review.

## Change Risk Findings

### F-CR-001 — The audited branch is provably divergent from main (P0)
**Source synthesis:** F-00-001 + F-11-003 + git log
- The worktree shipped on top of pre-Phase-40 llm_client.py (257 lines).
- Main branch has the post-Phase-40 llm_client.py (1711 lines) which the new tier modules were designed to call into.
- The 50 new files in this branch implicitly assume a different llm_client.py than the one actually in the branch.
- **This is a "ghost integration"** — the modules look connected on paper because both reference `TierName`, but execution-time calls will hit the OLD llm_client which knows nothing about tiers.

**Risk level:** Critical — this branch CANNOT be merged or shipped as-is without first reconciling against main.

### F-CR-002 — Cutover playbook is "all-at-once" with no shadow validation (P0)
**Source synthesis:** F-01-001 + commit `53dfecc` message + cutover-playbook.md
- Per the commit message, cutover changed from "day-by-day (12 days)" to "all-at-once" because Perseus has no production traffic to protect.
- BUT all-at-once requires SHADOW data ("synthetic") to validate before FLIP.
- The shadow_diffs migration (047) is unapplied, so even if shadow ran, results couldn't be logged.
- The new modules aren't wired (F-01-001), so shadow can't run at all.
- **The cutover plan presupposes integration that has not been done.**

### F-CR-003 — Concentration risk: 9k LOC in a single commit by single human-AI pair (P1)
**Source synthesis:** F-11-001 + F-12-001
- Bus factor 1 for the entire Phase 42.5 v2.
- No incremental review on individual modules.
- ADR equivalents only in `.paul/HANDOFF.md`, which is unstructured.
- If something subtle breaks at runtime, debugging requires re-deriving design intent from git diff.

### F-CR-004 — Migration application is implicit, not enforced (P1)
**Source:** F-02-001 + F-07-001
- 046 and 047 must run before any daemon that uses spend_alerts or shadow logging restarts.
- No CI gate, no boot-time auto-migration.

## Reality Gap Findings (code vs documented behavior)

### F-RG-001 — `verifier.sb` claims protection it does not deliver (P0)
- The profile says "deny ~/.ssh" but sandbox-exec doesn't expand `~`.
- The profile says "tested by tests/test_verifier_sandbox_red_team.py" but that file doesn't exist.
- The profile is hardcoded to operator's desktop path; on production it protects nothing.
- **Documentation/code agree on the surface, runtime behavior matches neither.**

### F-RG-002 — `shared.aider.ruflo_loop` claims integration with Aider but Aider isn't a dependency (P1)
- `requirements.txt` does not list `aider-chat`.
- `ruflo_loop.py` references "Aider production data" in comments but the actual integration is a custom architect+editor loop, not Aider's CLI.
- A new contributor reading the code expects Aider; they get a Perseus-internal pattern named "aider".

### F-RG-003 — Tier classifier rule for "default_local" claims to handle Titan stages 1-6 (P1)
- `shared/tier_classifier.py:204-208` matches `lead_classification`, `lead_extraction` to LOCAL tier.
- But Titan's actual call sites in `titan/pipeline/lead_research.py` etc. use `model="auto"` against the OLD llm_client, which routes to Haiku, not local.
- The classifier's claim to "handle stage 1-6" is aspirational; nothing dispatches through the classifier today.

### F-RG-004 — Spend alerts wired to Hermes but Hermes doesn't import the module (P1)
- `shared/spend_alerts.py:163` `from shared.comms import send_telegram_alert`.
- Hermes daemon does not import `shared.spend_alerts`.
- Telegram alerts will not fire; the BLOCK action will not engage.

### F-RG-005 — Voice loop "natural_response" plays back BEFORE the action runs (P2)
- `shared/voice/intent_router.py:34` — natural_response is a "Got it, checking pipeline..." filler.
- The intended UX is: speak filler → run action → speak result.
- No code in the worktree wires the speak-then-act-then-speak loop. The classifier outputs the filler; nothing plays it.

### F-RG-006 — Image gen client expects Draw Things HTTP API but no installation check (P3)
- `shared/imagegen/draw_things_client.py` assumes `127.0.0.1` HTTP endpoint.
- No retry, no clear error if Draw Things isn't running.

## Phase 3 Summary

8 cross-domain findings. 3 P0, 4 P1, 1 P2, 0 P3. All confirm the central thesis: **the new modules look complete in isolation, but the integration layer doesn't exist yet.**
