"""LiteLLM local-path guard hook — P1-1 stub.

Purpose: a LiteLLM pre-call hook that enforces the local-tier routing
policy. When a request targets a cloud model but a matching local model
is available and healthy, the guard should redirect the call to the
local path instead. This prevents silent cloud spend when the local
tier is intended to serve the request.

This file is a placeholder created by audit recovery plan 01-02 (P1-1).
The real implementation is tracked in
``docs/spikes/litellm-pre-call-hook-spike.md``.
"""

from __future__ import annotations

from typing import Any


def enforce_local_path_policy(request: Any) -> Any:
    """Enforce the local-path policy on a LiteLLM request.

    P1-1 stub — see docs/spikes/litellm-pre-call-hook-spike.md for the
    planned behavior. This stub exists so the audit's missing-artifact
    finding is satisfied; calling it intentionally raises so any
    accidental production use is loud.
    """
    raise NotImplementedError(
        "P1-1 stub — see docs/spikes/litellm-pre-call-hook-spike.md"
    )
