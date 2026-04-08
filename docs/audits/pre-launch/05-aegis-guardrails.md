# AEGIS Guardrails — Project Rules (Post-Audit Pass)

Generated: 2026-04-07
Source: AEGIS pre-launch audit (docs/audits/pre-launch/02-aegis/)
Target worktree: /Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/
Format: CARL rule blocks — suitable for `.claude/CLAUDE.md` (project) or `~/.claude/CLAUDE.md` (global)
Intervention level: Suggesting (advisory — AI assistant behavior is not hard-enforced)

These rules codify the recurring failure patterns found by the AEGIS audit during Perseus Phase 42.5 autonomous buildout. Each rule names a concrete trigger pattern and a concrete enforcement mechanism. Install scope is labeled per rule.

---

## RULE 1 — Stale-branch writes to shared/ must fail closed

**Install scope:** Project (`.claude/CLAUDE.md`)
**Pattern:** Claude writes new files under `shared/` (or any cross-agent module path) from a worktree whose base branch is behind `origin/main`.
**Audit evidence:** Phase 42.5 committed 40+ shared/ files from `claude/charming-elion` while `main` had diverged — creating ghost modules that nothing imports.

```carl
rule: no-stale-shared-writes
when:
  - tool: Write | Edit
  - path_matches: "shared/**" | "daemons/*/shared/**" | "agents/*/shared/**"
precondition:
  - run: "git fetch origin main --quiet && git rev-list --count HEAD..origin/main"
  - expect: "0"
on_violation:
  - halt
  - message: "Base branch diverged. Rebase onto origin/main before writing to shared/."
enforcement:
  - hook: PreToolUse on Write/Edit (settings.json hook running scripts/preflight/check-branch-fresh.sh)
  - ci: pre-commit hook matching the same glob
```

---

## RULE 2 — Sandbox profiles must use absolute paths, never tilde

**Install scope:** Project
**Pattern:** Any file matching `**/sandbox*.{json,yaml,toml,sb}` or `**/seatbelt*.sb` containing `~/` or `$HOME` in a path literal.
**Audit evidence:** Sandbox profile referenced `~/.ssh` which macOS Seatbelt does not expand — the rule silently became a no-op.

```carl
rule: sandbox-absolute-paths-only
when:
  - tool: Write | Edit
  - path_matches: "**/sandbox*.{json,yaml,toml,sb,profile}" | "**/seatbelt*.sb" | "**/run_in_sandbox.sh"
forbid_regex:
  - "~/"
  - "\\$HOME"
  - "\\$\\{HOME\\}"
require:
  - "All filesystem paths MUST begin with / and resolve to an absolute path"
on_violation:
  - halt
  - message: "Sandbox profiles do not expand ~ or $HOME. Use /Users/majovega/... literally."
enforcement:
  - hook: PreToolUse regex grep on sandbox glob
  - lint: scripts/lint/sandbox-profile-lint.py (run in CI)
```

---

## RULE 3 — Crypto modules must fail closed, never fall back to plaintext

**Install scope:** Global (`~/.claude/CLAUDE.md`)
**Pattern:** Any module under `shared/crypto/`, `shared/secrets/`, `shared/encryption/`, or matching `*_cipher.py`, `*_keystore.py` that contains an `except` / fallback branch returning plaintext when key material, env vars, or crypto backends are missing.
**Audit evidence:** `shared/crypto.py` caught missing `PERSEUS_MASTER_KEY` and returned the plaintext value "so callers could keep working" — silently defeating the encryption layer.

```carl
rule: crypto-fail-closed
when:
  - tool: Write | Edit
  - path_matches: "**/crypto*.py" | "**/secrets*.py" | "**/keystore*.py" | "**/encryption*.py" | "**/*cipher*.py"
forbid_patterns:
  - "return plaintext"
  - "return value  # unencrypted fallback"
  - "except.*:\\s*return\\s+\\w+\\s*#.*fallback"
  - "if not .*KEY.*:\\s*return"
require:
  - "Missing key material MUST raise CryptoNotConfigured (or equivalent) and refuse to proceed"
  - "No except branch may return an unencrypted form of the input"
on_violation:
  - halt
  - message: "Crypto modules must fail closed. Raise, do not fall back to plaintext."
enforcement:
  - hook: PreToolUse regex scan
  - semgrep: rules/crypto-fail-closed.yaml in CI
  - test: tests/security/test_crypto_fail_closed.py must exist and be imported in the test suite
```

---

## RULE 4 — New env vars require .env.example entry in the same commit

**Install scope:** Project
**Pattern:** Claude introduces `os.getenv("X")` / `process.env.X` / `ENV["X"]` for an `X` not already in `.env.example`.
**Audit evidence:** Phase 42.5 referenced 11 new env vars (LITELLM_*, AIRLLM_*, KIMI_*, PARAKEET_*) without updating `.env.example` — preflight checks passed locally because the operator's shell already had them exported.

```carl
rule: env-var-declared-in-example
when:
  - tool: Write | Edit
  - content_matches_regex: "(os\\.getenv|os\\.environ\\[|process\\.env\\.)\\s*['\"]?([A-Z][A-Z0-9_]+)"
require:
  - "Every captured env var MUST also appear (as KEY=) in .env.example"
  - "If absent, append KEY=<placeholder> to .env.example in the same edit batch"
on_violation:
  - halt
  - message: "Env var {VAR} is not in .env.example. Add it before writing the code that reads it."
enforcement:
  - hook: PostToolUse diff scan comparing new env references against .env.example
  - ci: scripts/preflight/check-env-example.py
  - preflight: daemons must boot through a validator that errors on undocumented env vars
```

---

## RULE 5 — Test files must verify their imports resolve before being written

**Install scope:** Project
**Pattern:** Claude writes `tests/**/test_*.py` containing `from shared.X import Y` or `from daemons.X import Y` where the target module or symbol does not yet exist on disk.
**Audit evidence:** Phase 42.5 wrote 14 test files importing from modules that were never created — the test suite failed to collect 14 files silently when pytest's `--continue-on-collection-errors` was on.

```carl
rule: test-imports-must-resolve
when:
  - tool: Write | Edit
  - path_matches: "tests/**/test_*.py" | "tests/**/*_test.py"
precondition:
  - "For every `from X import Y` in the new content, resolve X to a file on disk"
  - "For every imported symbol Y, grep the target file and confirm the symbol is defined"
on_violation:
  - halt
  - message: "Test imports {module}.{symbol} but the symbol does not exist. Create the source first, or remove the test."
enforcement:
  - hook: PreToolUse Python AST walk + filesystem check
  - ci: `python -m compileall tests/` and `pytest --collect-only -q` must exit 0 (no errors tolerated)
```

---

## RULE 6 — Runbooks must only reference scripts that exist

**Install scope:** Project
**Pattern:** Claude writes `docs/**/*.md`, `RUNBOOK.md`, `docs/runbooks/**` containing fenced commands or inline `./path/to/script.sh` references where the script file does not exist on disk.
**Audit evidence:** Phase 42.5 runbook referenced `./scripts/run_in_sandbox.sh` and `./scripts/migrate_to_litellm.py --apply` — neither existed at the paths cited, and the runbook was the only documentation pointing operators to them.

```carl
rule: runbook-scripts-must-exist
when:
  - tool: Write | Edit
  - path_matches: "**/RUNBOOK*.md" | "docs/runbooks/**" | "docs/audits/**/RUN*.md" | "docs/operations/**"
scan_for:
  - inline backtick commands mentioning `./` or `scripts/` paths
  - fenced bash blocks invoking local scripts
require:
  - "Every referenced script path MUST exist on disk at the cited location"
  - "Every referenced script MUST have the executable bit set (or be invoked via `python`/`bash` explicitly)"
on_violation:
  - halt
  - message: "Runbook references {script} which does not exist. Create it first, or change the runbook to describe the manual steps."
enforcement:
  - hook: PostToolUse markdown parser → filesystem existence check
  - ci: scripts/lint/runbook-link-check.py
```

---

## RULE 7 — Mass autonomous commits require wire-up proof

**Install scope:** Project
**Pattern:** A single Claude session stages more than 20 new files AND the session has not executed the project's declared wire-up / migration script (e.g. `scripts/migrate_to_litellm.py --apply`) with exit code 0.
**Audit evidence:** Phase 42.5 committed 50 files in one autonomous pass without running `migrate_to_litellm.py --apply` — the files existed but no daemon imported them. Classic ghost code.

```carl
rule: no-orphan-mass-commits
when:
  - tool: Bash
  - command_matches: "git commit" | "git add -A" | "git add ."
  - staged_file_count: "> 20"
precondition:
  - "A wire-up script declared in .claude/wire-up-scripts.yaml MUST have been run with exit 0 during this session"
  - "OR: every new file MUST be importable from at least one daemon entry point (reverse-import graph check)"
on_violation:
  - halt
  - message: "Large autonomous commits require proof of wire-up. Run `python scripts/migrate_to_litellm.py --apply` and then retry."
enforcement:
  - hook: PreToolUse on `git commit` — reads .claude/wire-up-scripts.yaml, checks session log for successful invocation
  - ci: scripts/preflight/check-orphan-modules.py builds import graph from daemon entry points and fails if new shared/ files are unreachable
  - policy: .claude/settings.json defines `staged_file_ceiling: 20` beyond which PreToolUse hook runs the orphan check
```

---

## RULE 8 — Worktree freshness check before any Phase N+1 work

**Install scope:** Project (reinforces Rule 1)
**Pattern:** Claude continues a multi-phase mega-plan from a worktree without first verifying main is current.
**Audit evidence:** Multiple phases shipped from stale worktrees with divergent state vs main, causing silent merge conflicts downstream.

```carl
rule: phase-boundary-freshness-check
when:
  - user_message_matches: "phase \\d+" | "continue phase" | "next phase"
precondition:
  - run: "cd $(git rev-parse --show-toplevel) && git fetch origin main --quiet && git merge-base --is-ancestor origin/main HEAD"
  - expect_exit: 0
on_violation:
  - halt
  - message: "This worktree is behind origin/main. Rebase before starting the next phase."
enforcement:
  - hook: UserPromptSubmit hook matching phase-related prompts
  - skill: /base:pulse runs this check at session start
```

---

## Install Summary

| Rule | Scope | Mechanism |
|---|---|---|
| 1. No stale shared/ writes | Project | PreToolUse hook on Write/Edit glob |
| 2. Sandbox absolute paths | Project | PreToolUse regex + CI lint |
| 3. Crypto fail-closed | Global | PreToolUse regex + Semgrep + required test file |
| 4. Env vars in .env.example | Project | PostToolUse diff + CI preflight |
| 5. Test imports resolve | Project | PreToolUse AST walk + pytest --collect-only |
| 6. Runbook scripts exist | Project | PostToolUse link-check + CI lint |
| 7. No orphan mass commits | Project | PreToolUse on git commit + import-graph check |
| 8. Phase boundary freshness | Project | UserPromptSubmit hook |

## Next Actions

1. Copy rules 1, 2, 4, 5, 6, 7, 8 into `.claude/CLAUDE.md` under a `## Guardrails (AEGIS Pre-Launch)` heading.
2. Copy rule 3 into `~/.claude/CLAUDE.md` — crypto fail-closed is a universal operator invariant.
3. Implement the hooks referenced in enforcement blocks under `.claude/hooks/` and register them in `.claude/settings.json`.
4. Add the CI scripts (`scripts/preflight/*.py`, `scripts/lint/*.py`) and wire them into the existing CI job that already runs pre-commit hooks.
5. Re-run `/aegis:audit` after install to confirm the recurring pattern count drops.

## Notes on Intervention Level

These guardrails are SUGGESTING level — they are advisory to the AI assistant. True enforcement requires the hooks and CI scripts to be installed and running. A rule in CLAUDE.md without a matching hook is a nudge, not a guarantee. The audit found that Phase 42.5 had prose rules in CLAUDE.md that Claude ignored under autonomous pressure — so every rule here names its hook/CI mechanism explicitly.
