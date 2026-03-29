---
phase: 00A-p0-bug-fixes-skill-hardening
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - shared/skill_loader.py
  - shared/skill_signer.py
  - shared/skill_verify_key.pem
  - scripts/generate-skill-signing-key.sh
  - tests/conway/test_skill_signing.py
autonomous: true
requirements: [FIX-04]

must_haves:
  truths:
    - "load_skill() rejects unsigned skill files and returns empty string"
    - "load_skill() accepts properly signed skill files and returns content"
    - "Tampered skill content (modified after signing) is rejected"
    - "SKILL_LOADER_ALLOW_UNSIGNED=true bypasses verification with warning log"
    - "Ed25519 keypair can be generated on macOS ARM64"
  artifacts:
    - path: "shared/skill_signer.py"
      provides: "Ed25519 signing utility"
      exports: ["sign_skill"]
      contains: "Ed25519"
    - path: "shared/skill_loader.py"
      provides: "Verification-enabled skill loader"
      contains: "verify_skill"
    - path: "shared/skill_verify_key.pem"
      provides: "Ed25519 public key for signature verification"
      contains: "PUBLIC KEY"
    - path: "scripts/generate-skill-signing-key.sh"
      provides: "Key generation script"
      contains: "Ed25519"
    - path: "tests/conway/test_skill_signing.py"
      provides: "FIX-04 regression tests"
      contains: "test_load_skill_rejects_unsigned"
  key_links:
    - from: "shared/skill_loader.py"
      to: "shared/skill_verify_key.pem"
      via: "reads public key to verify signatures"
      pattern: "skill_verify_key.pem"
    - from: "shared/skill_signer.py"
      to: "shared/skill_loader.py"
      via: "creates .sig files that loader verifies"
      pattern: "\\.sig"
    - from: "shared/skill_loader.py"
      to: "SKILL_LOADER_ALLOW_UNSIGNED"
      via: "env var check for dev bypass"
      pattern: "SKILL_LOADER_ALLOW_UNSIGNED"
---

<objective>
Implement Ed25519 skill signing and verification so the skill loader rejects unsigned/tampered files. This hardens the trust boundary before Phase 1 (Agent DNA) injects skill content into LLM system prompts.

Purpose: Prevent arbitrary code/prompt injection through the skill loader surface.
Output: Signing utility, verification in loader, public key, key generation script, and comprehensive tests.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

@shared/skill_loader.py
@shared/config.py

<interfaces>
<!-- Current skill_loader.py interface that must be preserved -->

From shared/skill_loader.py:
```python
def find_skill(name: str) -> Path | None: ...
def load_skill(path: Path) -> str: ...       # Currently: path.read_text() — MUST add verification
async def execute_skill(skill_name: str, task_prompt: str, context: dict | None = None, model: str = "fast") -> str: ...
async def execute_skill_or_fallback(skill_name: str, task_prompt: str, fallback_fn, context: dict | None = None, model: str = "fast"): ...
def list_installed_skills() -> list[dict]: ...
```

From shared/config.py:
```python
config.root_dir  # Path to project root
```

The `cryptography` library is already available (used by openjarvis/security/signing.py, eth_account).
Ed25519 API:
```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
# Generate: key = Ed25519PrivateKey.generate()
# Sign: sig = key.sign(data_bytes)
# Verify: pub_key.verify(sig, data_bytes)  # raises InvalidSignature
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Implement skill signing utility, key generation, and loader verification</name>
  <files>shared/skill_signer.py, shared/skill_loader.py, scripts/generate-skill-signing-key.sh, shared/skill_verify_key.pem</files>
  <read_first>
    - shared/skill_loader.py (full file — current load_skill at line 76-78, execute_skill at 81-112, execute_skill_or_fallback at 115-132)
    - shared/config.py (config.root_dir usage for locating verify key)
    - openjarvis/security/signing.py (existing signing patterns in codebase using cryptography library)
  </read_first>
  <action>
**1. Create `scripts/generate-skill-signing-key.sh`:**
```bash
#!/usr/bin/env bash
# Generate Ed25519 keypair for skill signing
# Private key: ~/.objective-hertz/skill-signing-key.pem (operator keeps, never commit)
# Public key: shared/skill_verify_key.pem (checked into repo)
set -euo pipefail
PRIVATE_KEY_DIR="$HOME/.objective-hertz"
PRIVATE_KEY_PATH="$PRIVATE_KEY_DIR/skill-signing-key.pem"
PUBLIC_KEY_PATH="$(dirname "$0")/../shared/skill_verify_key.pem"
mkdir -p "$PRIVATE_KEY_DIR"
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
priv_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
pub_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
open('$PRIVATE_KEY_PATH', 'wb').write(priv_pem)
open('$PUBLIC_KEY_PATH', 'wb').write(pub_pem)
print(f'Private key: $PRIVATE_KEY_PATH')
print(f'Public key: $PUBLIC_KEY_PATH')
"
chmod 600 "$PRIVATE_KEY_PATH"
echo "Done. Private key secured at $PRIVATE_KEY_PATH"
```
Make executable: `chmod +x scripts/generate-skill-signing-key.sh`.

**2. Create `shared/skill_signer.py`:**

Module with:
- `sign_skill(skill_path: Path, private_key_path: Path) -> Path` — reads skill content bytes, loads Ed25519 private key from PEM, computes signature via `private_key.sign(content_bytes)`, writes raw signature bytes to `{skill_path}.sig`, returns sig path.
- `sign_all_skills(private_key_path: Path) -> list[Path]` — iterates `SKILL_DIRS` from skill_loader, signs every `SKILL.md` found.
- CLI block: `if __name__ == "__main__":` with argparse supporting `sign <skill_path> --key <private_key_path>` and `sign-all --key <private_key_path>`.

Imports: `from pathlib import Path`, `from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey`, `from cryptography.hazmat.primitives import serialization`, `import argparse`, `import logging`.

**3. Modify `shared/skill_loader.py`:**

Add a `verify_skill(skill_path: Path) -> bool` function above `load_skill`:
- Locate public key at `Path(__file__).parent / "skill_verify_key.pem"`.
- If public key file doesn't exist: log warning "No verify key found at shared/skill_verify_key.pem", return False.
- Locate sig file at `Path(str(skill_path) + ".sig")`.
- If sig file doesn't exist: log error f"No signature file for {skill_path}", return False.
- Load public key: `serialization.load_pem_public_key(pub_pem_bytes)`.
- Read skill content bytes: `skill_path.read_bytes()`.
- Read signature bytes: `sig_path.read_bytes()`.
- Call `public_key.verify(signature, content)` — if `InvalidSignature` raised, log error "Signature verification failed for {skill_path}", return False.
- Return True on success.

Modify `load_skill(path: Path) -> str`:
- Check `os.environ.get("SKILL_LOADER_ALLOW_UNSIGNED", "").lower() == "true"` — if so, log warning "Loading unsigned skill: {path} (SKILL_LOADER_ALLOW_UNSIGNED=true)", return `path.read_text()`.
- Call `verify_skill(path)` — if False, log error "Refusing to load unverified skill: {path}", return `""`.
- If True, return `path.read_text()`.

Add imports at top: `import os`, `from cryptography.hazmat.primitives import serialization`, `from cryptography.exceptions import InvalidSignature`.

**4. Generate keypair and sign existing skills:**
Run the key generation script. Then run `python3 -m shared.skill_signer sign-all --key ~/.objective-hertz/skill-signing-key.pem` to sign all existing skills. Commit `shared/skill_verify_key.pem` and all `.sig` files.

IMPORTANT: Keep `find_skill`, `find_skills_by_category`, `execute_skill`, `execute_skill_or_fallback`, and `list_installed_skills` unchanged in behavior. Only `load_skill` gets the verification gate.
  </action>
  <verify>
    <automated>cd /Users/majovega/Desktop/Projects/objective-hertz && python3 -c "from shared.skill_signer import sign_skill; print('signer OK')" && python3 -c "from shared.skill_loader import verify_skill, load_skill; print('loader OK')" && test -f shared/skill_verify_key.pem && echo "verify key exists" && ruff check shared/skill_signer.py shared/skill_loader.py</automated>
  </verify>
  <acceptance_criteria>
    - grep "def verify_skill" shared/skill_loader.py returns 1 match
    - grep "def load_skill" shared/skill_loader.py returns 1 match
    - grep "SKILL_LOADER_ALLOW_UNSIGNED" shared/skill_loader.py returns at least 1 match
    - grep "InvalidSignature" shared/skill_loader.py returns at least 1 match
    - grep "def sign_skill" shared/skill_signer.py returns 1 match
    - grep "Ed25519PrivateKey" shared/skill_signer.py returns at least 1 match
    - test -f shared/skill_verify_key.pem (public key exists)
    - test -f scripts/generate-skill-signing-key.sh (generation script exists)
    - test -x scripts/generate-skill-signing-key.sh (script is executable)
    - ruff check shared/skill_signer.py shared/skill_loader.py exits 0
  </acceptance_criteria>
  <done>Skill signing utility created, loader verifies signatures before loading, public key committed, key generation script works, all existing skills signed, ruff clean.</done>
</task>

<task type="auto">
  <name>Task 2: Skill signing and verification tests (FIX-04)</name>
  <files>tests/conway/test_skill_signing.py</files>
  <read_first>
    - shared/skill_loader.py (after Task 1 modifications — verify_skill and updated load_skill)
    - shared/skill_signer.py (sign_skill function)
    - shared/skill_verify_key.pem (public key location)
  </read_first>
  <action>
Create `tests/conway/test_skill_signing.py` with these tests. Use `tmp_path` fixture for all file operations. Generate a fresh Ed25519 keypair in a fixture for test isolation (do not depend on the real production key).

**Fixture `skill_keys(tmp_path)`:**
```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
priv_path = tmp_path / "test-private.pem"
pub_path = tmp_path / "test-public.pem"
priv_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
pub_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
return {"private": priv_path, "public": pub_path, "key": key}
```

**Tests:**

1. `test_sign_skill_creates_sig_file(tmp_path, skill_keys)` — Write a SKILL.md to tmp_path. Call `sign_skill(skill_path, skill_keys["private"])`. Assert `{skill_path}.sig` exists. Assert sig file is non-empty (64 bytes for Ed25519).

2. `test_verify_skill_accepts_valid_signature(tmp_path, skill_keys)` — Write SKILL.md, sign it, patch `skill_loader` to use test public key (patch `Path(__file__).parent / "skill_verify_key.pem"` resolution to `skill_keys["public"]`). Call `verify_skill(skill_path)`. Assert returns True.

3. `test_verify_skill_rejects_tampered_content(tmp_path, skill_keys)` — Write SKILL.md, sign it, then modify the skill content (append "TAMPERED"). Patch public key path. Call `verify_skill(skill_path)`. Assert returns False.

4. `test_verify_skill_rejects_missing_sig(tmp_path, skill_keys)` — Write SKILL.md but don't sign. Patch public key path. Assert `verify_skill(skill_path)` returns False.

5. `test_load_skill_rejects_unsigned(tmp_path, skill_keys, monkeypatch)` — Write SKILL.md, don't sign. Patch public key path. Ensure `SKILL_LOADER_ALLOW_UNSIGNED` is NOT set. Call `load_skill(skill_path)`. Assert returns `""` (empty string).

6. `test_load_skill_accepts_signed(tmp_path, skill_keys, monkeypatch)` — Write SKILL.md with content `"# Test Skill\nDo the thing"`, sign it. Patch public key path. Call `load_skill(skill_path)`. Assert returns `"# Test Skill\nDo the thing"`.

7. `test_load_skill_unsigned_bypass(tmp_path, monkeypatch)` — Write SKILL.md with content `"# Unsigned"`. Set `SKILL_LOADER_ALLOW_UNSIGNED=true` via `monkeypatch.setenv`. Call `load_skill(skill_path)`. Assert returns `"# Unsigned"`.

8. `test_load_skill_unsigned_bypass_case_insensitive(tmp_path, monkeypatch)` — Same as above but set env var to `"True"`. Assert still bypasses.

For patching the public key path: use `monkeypatch.setattr` on the module-level or patch `Path` resolution. The cleanest approach is to patch `shared.skill_loader.verify_skill` to use a helper that takes an optional `verify_key_path` parameter, OR patch `Path(__file__)` resolution. Alternatively, patch `serialization.load_pem_public_key` to use the test key.

Simplest approach: Have `verify_skill` read the key path from a module-level variable `VERIFY_KEY_PATH = Path(__file__).parent / "skill_verify_key.pem"` and monkeypatch that variable.
  </action>
  <verify>
    <automated>cd /Users/majovega/Desktop/Projects/objective-hertz && PYTHONPATH=. python3 -m pytest tests/conway/test_skill_signing.py -v 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - grep -c "def test_" tests/conway/test_skill_signing.py returns at least 7
    - grep "test_load_skill_rejects_unsigned" tests/conway/test_skill_signing.py returns 1 match
    - grep "test_load_skill_accepts_signed" tests/conway/test_skill_signing.py returns 1 match
    - grep "test_verify_skill_rejects_tampered" tests/conway/test_skill_signing.py returns 1 match
    - grep "SKILL_LOADER_ALLOW_UNSIGNED" tests/conway/test_skill_signing.py returns at least 2 matches
    - grep "Ed25519PrivateKey" tests/conway/test_skill_signing.py returns at least 1 match
    - PYTHONPATH=. python3 -m pytest tests/conway/test_skill_signing.py -v exits 0
  </acceptance_criteria>
  <done>8 tests pass proving: signing creates valid .sig files, verification accepts valid signatures, rejects tampered content, rejects missing signatures, load_skill blocks unsigned skills, load_skill allows signed skills, ALLOW_UNSIGNED bypass works.</done>
</task>

</tasks>

<verification>
```bash
cd /Users/majovega/Desktop/Projects/objective-hertz
PYTHONPATH=. python3 -m pytest tests/conway/test_skill_signing.py -v
ruff check shared/skill_signer.py shared/skill_loader.py tests/conway/test_skill_signing.py
```
All skill signing tests pass. Ruff reports no lint errors on new and modified files.
</verification>

<success_criteria>
- FIX-04: load_skill() rejects unsigned files (returns empty string, logs error)
- FIX-04: load_skill() accepts properly signed files (returns full content)
- FIX-04: Tampered skills (modified after signing) are rejected
- FIX-04: SKILL_LOADER_ALLOW_UNSIGNED=true bypasses with warning
- Ed25519 keypair generation works (public key committed)
- All existing skills have .sig files
- ruff check clean on all new/modified files
</success_criteria>

<output>
After completion, create `.planning/phases/00A-p0-bug-fixes-skill-hardening/00A-02-SUMMARY.md`
</output>
