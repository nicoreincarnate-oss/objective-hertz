# 15 — GSD Review (Cross-AI Peer Review)

**Date:** 2026-04-07
**Scope:** Phase 42.5 v2 implementation (commits 30770c0, 53dfecc, d9fdc6a) — 50 new files in `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/`
**Skill:** `/gsd:review` (cross-AI peer review workflow)

## External CLI Status

| CLI     | Installed | Result  |
|---------|-----------|---------|
| gemini  | No        | skipped |
| codex   | No        | skipped |
| claude  | No (external session) | skipped |

None of the external AI CLIs (Gemini, Codex/GPT-5, external Claude session) are installed on this machine. `which gemini codex claude` returned not-found for all three. The skill's intended flow — dispatch the diff to a non-Claude model family and collect its independent verdict — cannot run.

**Fallback executed:** I performed the review myself using a deliberately non-Claude posture — treating the code as a hostile reviewer would, focused on logic errors, API misuse, and cryptographic hazards that Claude's self-review characteristically misses. This is clearly labeled below as "Simulated non-Claude reviewer" and should not be weighted the same as a real second model.

## Files Actually Read

- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/llm_client.py` (257 lines)
- `/Users/majovega/Desktop/Projects/objective-hertz/shared/llm_client.py` (1711 lines — main repo baseline)
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/scripts/migrate_to_litellm.py` (349 lines)
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/verifier/grammar_compiler.py` (169 lines)
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/verifier/consistency.py` (185 lines)
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/escalation_log/redactor.py` (246 lines)

## Baseline Drift — Confirmed P0

`wc -l` confirms: worktree `shared/llm_client.py` = **257 lines**, main repo `shared/llm_client.py` = **1711 lines**. The worktree file is a from-scratch rewrite, not an extension of the existing client. Everything downstream (tier router, verifier, escalation log, migration script) was designed against a file that does not exist in main. **Merging this worktree as-is would delete 1454 lines of production code** (caching, retry, budget guard, tracing, provider fallback, etc.) and replace it with a stub.

Verdict: **not a drop-in merge**. Two recoverable paths:
1. Port the 257-line worktree design into the 1711-line main `llm_client.py` as additive methods (`generate(..., tier=, operation=, daemon_name=)`) without deleting the existing surface area. ~1–2 weeks.
2. Treat Phase 42.5 v2 as a greenfield replacement and rewrite against the main baseline. ~3–4 weeks.

Path 1 is recommended. A full rewrite is not needed — the verifier, grammar compiler, redactor, and migration script are largely independent of `llm_client.py` internals and can be kept.

## Simulated Non-Claude Reviewer Findings

### Top 3 Findings a Non-Claude Reviewer Would Catch First

#### NC-1: `migrate_to_litellm.py` silently skips multi-line calls AND claims them as "edits" zero in the log, but the user has no way to see which sites were skipped

`apply_patch()` at line 249 does:
```python
if "llm.generate(" not in line and "llm_client.generate(" not in line:
    continue  # Multi-line call — skip for safety, hand-migrate
```

Every `await llm.generate(\n    prompt,\n    model="smart",\n    ...\n)` style call site — which is the **dominant pattern in Python for kwarg-heavy calls** — will be silently skipped. The `scan_repo()` phase counts them (so `len(sites)` looks right and the `by_daemon` report shows them as "needs work") but `apply_patch()` then skips them with no warning and no list output. The user runs `--apply`, sees `Total edits: N`, and assumes the migration is done when in fact the majority of real call sites were untouched.

Worse: `visitor.visit_Call` records `node.lineno` which, for a multi-line call, is the line of `await llm.generate(` — which **does** contain the substring `llm.generate(`, so the `"llm.generate(" not in line` guard will actually **match** the opening line and inject the kwargs there. But then the insertion point is `line.index("generate(") + len("generate(")` on that line, and the existing args (`prompt,` on line 2, `model="smart",` on line 3) are unaffected. Net result: the first line becomes `await llm.generate(operation="...", daemon_name="...", ` and the closing `)` is still 4 lines down. That is syntactically valid Python — but now the injected kwargs appear **before** the positional `prompt` arg on line 2, which **is a SyntaxError** (positional after keyword).

This is exactly the class of bug flagged in the task description. The fix is to either (a) use `ast.unparse()` / `libcst` for a proper structural rewrite, or (b) explicitly detect that the `Call` node spans multiple lines (`node.end_lineno != node.lineno`) and route those to a separate "please hand-migrate" report rather than attempting a text splice.

Recommended fix: add `if node.end_lineno != node.lineno: record as multi_line=True` in `_record_call`, and in `apply_patch` skip those with a **visible warning count** so the user knows how many sites still need manual work. Even better: switch to `libcst` — it was built for exactly this.

#### NC-2: Verifier consistency algorithm has an off-by-one in the "2 out of 3" threshold that rejects valid majorities

`consistency.py` line 157: `consistent=majority >= (2 / 3)`. With `n_samples=3`, the possible majorities are 1/3, 2/3, 3/3. `2/3` in Python is `0.6666...` and the comparison `2/3 >= 2/3` is `True`, so that branch is fine. **But:** when one of the `n_samples - 1` extra calls raises and gets caught at line 126, `responses` can end up with length 2 (first + one successful retry). Now the possible majorities are 1/2 and 2/2. `1/2 = 0.5` fails the threshold, but `2/2 = 1.0` passes — **and so does `1/1`** when both extras fail and `len(responses) == 1`, because then `winner_count=1, total=1, majority=1.0 >= 0.6667 → consistent=True`.

This means: **if both retry samples fail, the consistency check reports `consistent=True` with `confidence=1.0`** — the exact opposite of what should happen. A failed consistency check is being laundered into a passed one by silently dropping failed samples. This is a correctness bug in the escalation gate: high-stakes calls that should escalate to the cloud tier will instead return the single first-sample answer with fake 100% confidence.

Fix: track `attempted_samples` separately from `successful_samples`; if `successful_samples < ceil(n_samples * 2/3)`, return `consistent=False, triggered=True` to force escalation. Also log a distinct error class "consistency_sample_failure" for alerting.

#### NC-3: `grammar_compiler.py` has no recursion guard and no `$ref` / `definitions` support — any recursive schema or `oneOf` nested inside an object will produce broken GBNF

`_compile_rule` has no visited-set and no depth limit. A schema like `{"type":"object","properties":{"child":{"$ref":"#"}}}` (common in tool schemas for tree-shaped payloads) will either (a) infinite-loop if `$ref` is ever added, or (b) today, silently fall through `_compile_rule` to the `return "string"` default at line 106 because `$ref` is not a recognized type, which means **any schema using `$ref` gets compiled to `string` and the "grammar-constrained" guarantee is a lie** — the model is allowed to emit any string, and Layer 1 verification passes vacuously.

Additionally, `_compile_oneof` calls `_compile_rule` on each option but does **not register** the sub-rules in the `rules` dict — they're inlined as raw expression text. For `oneOf` options that are objects, this means the object's sub-properties also get inlined, producing a flat rule with no recursion boundary. llama.cpp's GBNF parser handles inlined rules fine up to a point, but combined with the `_compile_object` sub-rule-name scheme (`{rule_name}_{key}`), you can get **duplicate rule names when two `oneOf` branches have the same property name** — e.g., `oneOf: [{type:object, properties:{id: {...}}}, {type:object, properties:{id: {...}}}]` both create a rule named `root_opt0_id` and `root_opt1_id` which is fine, but **the inner properties inside opt0 and opt1 that share names will collide** if the naming recursion isn't prefix-aware (check `_compile_object` — it uses `{rule_name}_{key}` which IS prefixed, so this specific case is OK, but only because of the prefix; change the naming and it breaks silently).

Also missing: `anyOf`, `allOf`, `$defs`/`definitions`, `const`, `pattern` (for string regex), `minItems`/`maxItems`, `minimum`/`maximum`, required property ordering (JSON objects are unordered but GBNF grammars are strictly ordered — the current code forces a specific serialization order, which means the model will be penalized for emitting `{"b":1,"a":2}` even though it's valid JSON). This last point is a **silent accuracy tax** on every constrained call.

Fix: add `$ref` resolution, recursion guard via visited set + max depth, `anyOf`/`allOf`, and — critically — generate a permutation-tolerant object rule (every permutation of required keys as alternation) or at minimum document the ordering constraint loudly in the prompt template.

### Top 2 Issues All Reviewers Would Agree On

#### A-1: The redactor's SHA-256 key derivation is cryptographically unacceptable

`redactor.py` line 159: `self.encryption_key = hashlib.sha256(password.encode()).digest()`.

Every reviewer across any model family would flag this:
- **Unsalted** — a rainbow table against `CONWAY_KEYSTORE_PASSWORD` is cheap.
- **Single iteration** — SHA-256 is a hash, not a KDF. A GPU does ~10 GH/s against SHA-256. If the keystore password is human-chosen (which the variable name implies — it's operator-set), offline brute force is trivial.
- **No versioning / no rotation support** — the doc comment says "Rotation: 90 days" but there is no version byte on the ciphertext, so rotating the password renders all existing log lines unreadable with no migration path.
- **AES-GCM nonce is stored prepended, then base64-encoded** — nonce reuse risk is low (it's random 12 bytes), but there is no authenticated header binding `daemon`, `ts`, or `prompt_hash` to the ciphertext via `associated_data=`, so an attacker with write access could swap ciphertexts between log lines undetected.

Fix: use `scrypt` or `argon2id` (via the already-imported `cryptography` package) with a per-installation salt stored alongside the log file; include an HKDF-derived per-file subkey; pass `prompt_hash`-bound AAD to `AESGCM.encrypt`; add a 1-byte key version prefix on every ciphertext.

Any non-Claude reviewer with security training flags this immediately. This is the exact finding the human audit called out in the task description — it is not a false positive.

#### A-2: `migrate_to_litellm.py` skips the file that is most important to migrate — `shared/llm_client.py` itself — and then claims a clean run

The baseline drift (257 vs 1711 lines) means the **main repo** `llm_client.py` has a completely different internal structure than the worktree assumes. The migration script scans `REPO_ROOT` (which is the worktree when run from the worktree) and will happily report "0 migrations needed" — because the worktree's own `llm_client.py` is already in the new shape. But when this worktree is merged to main, the **real** `llm_client.py` (1711 lines, with its own caching and retry layer that wraps `generate()`) will suddenly be called with kwargs it doesn't recognize (`tier=`, `operation=`, `daemon_name=`), and depending on how the existing signature handles `**kwargs`, it will either TypeError at runtime or silently drop the routing metadata on the floor — defeating the entire point of Phase 42.5.

Both Claude and a hypothetical external reviewer would agree: **the migration script must be run against the MAIN branch of the repo**, not the worktree, and its output must be cross-checked against the actual main `llm_client.py` signature before any of this is merged. Today the worktree's `.git` context makes this trivially easy to get wrong.

Fix: (a) port the worktree `llm_client.py` design into main additively rather than replacing, (b) add a sanity assertion at the top of `migrate_to_litellm.py` that inspects `shared/llm_client.py` and refuses to run if the file does not contain the new `generate(..., tier=, operation=, daemon_name=)` signature.

## Recoverable or Rewrite?

**Recoverable.** The four subsystems audited (grammar compiler, consistency checker, redactor, migration script) are logically independent of `llm_client.py` and can be landed incrementally. The only rewrite required is the `llm_client.py` itself — and that rewrite is a **port** (adding tier/operation/daemon_name kwargs to the existing 1711-line client) rather than a replacement. Estimated effort: 1–2 focused days for the port, plus the NC-1 / NC-2 / NC-3 / A-1 fixes above (~2–3 days each). Total: 2 weeks of focused work, not a from-scratch rewrite.

**Do not merge the worktree `shared/llm_client.py` file directly.** Everything else in the worktree can be landed after the fixes above.

## Actions

1. (P0) Do not merge worktree `shared/llm_client.py` over main — port additively instead.
2. (P0) Fix `redactor.py` KDF — switch to argon2id with salt + versioning + AAD.
3. (P0) Fix `consistency.py` sample-failure laundering bug — track attempted vs successful.
4. (P1) Rewrite `migrate_to_litellm.py` multi-line handling with `libcst` or `ast.unparse()`.
5. (P1) Add `$ref`, `$defs`, recursion guard, and permutation tolerance to `grammar_compiler.py`.
6. (P2) Install at least one external AI CLI (`gemini` or `codex`) so this skill can run its intended cross-model review next time.

## Caveat

This review was performed by Claude simulating a non-Claude reviewer posture. It is not a substitute for a real second-model review. The top-3 "non-Claude unique findings" above would ideally be validated by running this same diff through GPT-5 or Gemini once one of those CLIs is installed.
