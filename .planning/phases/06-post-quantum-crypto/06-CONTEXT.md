---
phase: 6
name: Post-Quantum Cryptography
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 6: Post-Quantum Cryptography — Context

## Phase Boundary

Conway wallet keys and API secrets protected against harvest-now-decrypt-later quantum attacks.

Requirements: PQC-01 through PQC-08
Feature flag: PQC_ENABLED
Dependencies: Phase 0a (Conway P0 bugs fixed)

## Canonical References

- `.planning/phases/6/PLAN.md` — Full task breakdown (Tasks 1-8)
- `conway/wallet.py` — Wallet keystore to upgrade
- `shared/contracts.py` — CryptoProvider Protocol
- `intel/post-quantum-crypto/` — PQC research reference
