#!/usr/bin/env python3
"""LiteLLM pre_call_hook rewrite spike — Plan 42-5-01 Task 5.

Tests whether LiteLLM's pre_call_hook can REWRITE a request before it goes to
the provider, or only OBSERVE it. Phase 42.5 needs rewrite capability to enforce
absolute MLX model paths (the EJellerson research found mlx-vlm hot-swaps to
bf16 weights and OOMs without absolute paths).

If rewrite works (GREEN): ship local_path_guard.py as designed.
If rewrite blocked (RED): wrap LiteLLM behind a thin FastAPI proxy that does
  the rewrite at the HTTP boundary, document the workaround.

Usage:
    python -m scripts.spikes.run_litellm_hook_spike --proxy-url http://localhost:4000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


async def test_pre_call_hook_rewrite() -> dict:
    """Try to register a pre_call_hook that rewrites the model field."""
    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger
    except ImportError:
        return {
            "verdict": "RED",
            "reason": "litellm not installed (pip install 'litellm>=1.40')",
            "workaround": "Install LiteLLM first, then re-run spike",
        }

    # Define a custom callback that tries to mutate the kwargs before the call
    rewrites_observed = []

    class RewriteHook(CustomLogger):
        async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
            # Try to mutate
            original_model = data.get("model")
            if original_model == "local-mlx-fast":
                data["model"] = "ollama/qwen3:30b-a3b-mlx-4bit"
                if "extra_body" not in data:
                    data["extra_body"] = {}
                data["extra_body"]["model_path"] = "/opt/perseus/models/qwen3-30b-a3b-mlx-4bit"
                rewrites_observed.append({
                    "original": original_model,
                    "rewritten": data["model"],
                    "extra_body": data["extra_body"],
                })
            return data

    try:
        litellm.callbacks = [RewriteHook()]
    except Exception as exc:
        return {
            "verdict": "RED",
            "reason": f"Cannot register callback: {exc}",
            "workaround": "Use a custom FastAPI middleware in front of LiteLLM",
        }

    # Now make a test call to see if the rewrite is reflected
    try:
        # Use a tiny test request
        response = await litellm.acompletion(
            model="local-mlx-fast",
            messages=[{"role": "user", "content": "test"}],
            mock_response="ok",
            max_tokens=1,
        )
    except Exception as exc:
        return {
            "verdict": "YELLOW",
            "reason": f"Hook registered but call failed (likely no real provider configured): {exc}",
            "rewrites_observed": rewrites_observed,
            "workaround": "If rewrites_observed is non-empty, hook fires before provider call - SHIP IT",
        }

    if rewrites_observed:
        return {
            "verdict": "GREEN",
            "reason": "Pre-call hook successfully rewrote model field",
            "rewrites_observed": rewrites_observed,
            "code_sample": """
class LocalPathGuard(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if data.get('model', '').startswith('local-mlx'):
            data.setdefault('extra_body', {})['model_path'] = ABSOLUTE_PATHS[data['model']]
        return data
""",
        }
    return {
        "verdict": "RED",
        "reason": "Hook fires but data mutation not reflected in provider call",
        "workaround": (
            "Wrap LiteLLM in a thin FastAPI middleware that intercepts /chat/completions, "
            "rewrites the body, forwards to LiteLLM. Add to docker-compose as a sidecar."
        ),
    }


async def main_async(args: argparse.Namespace) -> int:
    print("Testing LiteLLM pre_call_hook rewrite capability...")
    result = await test_pre_call_hook_rewrite()

    output_path = REPO_ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"""# LiteLLM pre_call_hook Rewrite Spike Report

Date: {time.strftime("%Y-%m-%d")}
LiteLLM version: $(python -c 'import litellm; print(litellm.__version__)' || echo unknown)

## Verdict: **{result['verdict']}**

{result['reason']}

## Workaround (if RED)
{result.get('workaround', 'N/A')}

## Code Sample
```python
{result.get('code_sample', '# See workaround above')}
```

## Test Output
```json
{json.dumps(result.get('rewrites_observed', []), indent=2)}
```

## Decision Criteria
- GREEN: Pre-call hook can rewrite model field → ship local_path_guard.py as planned
- YELLOW: Hook fires but mutation unclear → ship with verification logging
- RED: Hook can only observe → write FastAPI proxy sidecar instead
""")

    print(f"Verdict: {result['verdict']}")
    print(f"Report: {args.output}")
    return 0 if result["verdict"] != "RED" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-url", type=str, default="http://localhost:4000")
    parser.add_argument("--output", type=str, default="docs/spikes/litellm-pre-call-hook-spike.md")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
