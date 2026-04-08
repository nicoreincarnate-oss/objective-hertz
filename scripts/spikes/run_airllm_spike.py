#!/usr/bin/env python3
"""AirLLM proof-of-life spike — Plan 42-5-01 Task 4.

Tests whether AirLLM (disk-streamed model layers) actually delivers usable
heavy-thinking quality at acceptable latency on Mac Studio M4 Max.

Target: 3-8 tok/s for Llama 3.3 70B Q4. If we hit ≥3 tok/s with quality
comparable to Claude Sonnet 4.6 on the test prompts, we ship local-heavy
as the default for Openjarvis mega-plans and Ruflo cross-file refactors.

Usage:
    python -m scripts.spikes.run_airllm_spike \\
        --model meta-llama/Llama-3.3-70B-Instruct \\
        --num-prompts 10 \\
        --quantization 4bit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# 10 representative "heavy thinking" prompts — what AirLLM is for
HEAVY_PROMPTS = [
    "Design the architecture for a multi-tenant SaaS that needs to handle 10k concurrent users, sub-100ms p95 latency, and zero data leakage between tenants. Include database, caching, auth, and observability decisions.",
    "Walk through the proof of why merge sort is O(n log n) in the worst case, then explain how Timsort achieves better real-world performance.",
    "Generate a 5-step plan to migrate a 200k LOC monolith from Python 3.8 + Flask to Python 3.12 + FastAPI without downtime, in production at 50 RPS.",
    "Diagnose why a Postgres query that uses a covering index is slower than the same query without the index. List 6 possible root causes ranked by likelihood.",
    "Compare CRDTs vs OT for collaborative document editing. Specifically address: convergence guarantees, latency, network partition behavior, and storage cost.",
    "Design a rate limiter that handles 100k req/sec, supports per-user + per-IP + per-endpoint limits, persists state across restarts, and degrades gracefully under Redis outage.",
    "Explain the trade-offs between Raft, Paxos, and Viewstamped Replication. When would you pick each?",
    "Plan a feature flag rollout for a payment processor: start at 0.1%, validate, ramp to 100% over 7 days, with rollback gates at every step.",
    "Diagnose: a deep learning model has 99% training accuracy and 65% test accuracy. List the top 5 fixes ordered by expected impact and implementation cost.",
    "Architect an event-sourced order management system for a marketplace with 50k orders/day, 6-month event retention, and the need to replay events into multiple read models.",
]


@dataclass
class HeavyResult:
    prompt_idx: int
    response: str
    tokens_generated: int
    duration_s: float
    tok_per_s: float
    judge_quality_score: float = 0.0  # Sonnet-as-judge 0-1
    error: str = ""


async def run_airllm_call(prompt: str, model_id: str, max_tokens: int) -> HeavyResult:
    """Call AirLLM via the existing HeavyLocalProvider in shared/llm_providers/."""
    try:
        from shared.llm_providers.heavy_local_provider import HeavyLocalProvider
        provider = HeavyLocalProvider()
    except ImportError as exc:
        return HeavyResult(
            prompt_idx=0, response="", tokens_generated=0, duration_s=0.0,
            tok_per_s=0.0, error=f"HeavyLocalProvider not importable: {exc}",
        )

    t0 = time.perf_counter()
    try:
        result = await provider.generate(
            prompt,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=0.5,
        )
        duration = time.perf_counter() - t0
        tokens = result.output_tokens or (len(result.content) // 4)
        tok_per_s = tokens / duration if duration > 0 else 0.0
        return HeavyResult(
            prompt_idx=0,
            response=result.content,
            tokens_generated=tokens,
            duration_s=duration,
            tok_per_s=tok_per_s,
        )
    except Exception as exc:
        return HeavyResult(
            prompt_idx=0, response="", tokens_generated=0,
            duration_s=time.perf_counter() - t0, tok_per_s=0.0,
            error=str(exc),
        )


async def judge_quality(prompt: str, response: str) -> float:
    """Use Sonnet 4.6 to score the response quality 0-1."""
    if not response:
        return 0.0
    try:
        from shared.llm_client import LLMClient
        client = LLMClient()
        judge_prompt = f"""Score this response quality from 0.0 to 1.0.

Prompt: {prompt[:500]}

Response: {response[:2000]}

Score 0.0 = useless, 0.5 = partially correct, 1.0 = excellent.
Output ONLY the float number, nothing else."""
        score_text = await client.generate(
            judge_prompt,
            model="smart",
            max_tokens=10,
            temperature=0.0,
            daemon_name="airllm_spike",
            pipeline_stage="judge",
        )
        try:
            return float(score_text.strip())
        except ValueError:
            return 0.5
    except Exception:
        return 0.5


async def run_spike(args: argparse.Namespace) -> dict:
    print(f"Running AirLLM spike with {args.num_prompts} heavy-thinking prompts...")
    print(f"Model: {args.model}")
    print(f"This will take a while — AirLLM streams from disk.\n")

    prompts = HEAVY_PROMPTS[: args.num_prompts]
    results: list[HeavyResult] = []

    for i, prompt in enumerate(prompts):
        print(f"  Prompt {i+1}/{len(prompts)}: {prompt[:60]}...")
        result = await run_airllm_call(prompt, args.model, args.max_tokens)
        result.prompt_idx = i

        if args.judge_quality and result.response:
            print(f"    Generated {result.tokens_generated} tokens in {result.duration_s:.0f}s = {result.tok_per_s:.1f} tok/s")
            print(f"    Judging quality...")
            result.judge_quality_score = await judge_quality(prompt, result.response)
            print(f"    Quality: {result.judge_quality_score:.2f}")
        elif result.error:
            print(f"    ERROR: {result.error}")
        else:
            print(f"    {result.tok_per_s:.1f} tok/s")

        results.append(result)

    valid = [r for r in results if r.tokens_generated > 0 and not r.error]
    if not valid:
        return {
            "verdict": "RED",
            "reasoning": "All prompts failed",
            "results": [r.__dict__ for r in results],
        }

    avg_tok_per_s = statistics.mean([r.tok_per_s for r in valid])
    p50_tok_per_s = statistics.median([r.tok_per_s for r in valid])
    avg_quality = statistics.mean([r.judge_quality_score for r in valid if r.judge_quality_score > 0]) if args.judge_quality else 0.0

    if avg_tok_per_s >= 3.0 and (avg_quality >= 0.7 if args.judge_quality else True):
        verdict = "GREEN"
        reasoning = (
            f"{avg_tok_per_s:.1f} tok/s avg, quality {avg_quality:.2f} — "
            "ship local-heavy as default for Openjarvis mega-plans + Ruflo refactors"
        )
    elif avg_tok_per_s >= 1.0:
        verdict = "YELLOW"
        reasoning = (
            f"{avg_tok_per_s:.1f} tok/s avg — usable but slow. Ship as opt-in tier "
            "for fully-private heavy reasoning, default to cloud Opus"
        )
    else:
        verdict = "RED"
        reasoning = (
            f"{avg_tok_per_s:.1f} tok/s — too slow to be useful. Drop AirLLM, "
            "always escalate heavy thinking to cloud"
        )

    return {
        "verdict": verdict,
        "reasoning": reasoning,
        "model": args.model,
        "num_prompts": args.num_prompts,
        "successes": len(valid),
        "avg_tok_per_s": avg_tok_per_s,
        "p50_tok_per_s": p50_tok_per_s,
        "avg_quality": avg_quality,
        "avg_duration_s": statistics.mean([r.duration_s for r in valid]),
        "sample_responses": [
            {
                "prompt_preview": HEAVY_PROMPTS[r.prompt_idx][:150],
                "tok_per_s": r.tok_per_s,
                "quality": r.judge_quality_score,
                "response_preview": r.response[:300],
            }
            for r in valid[:3]
        ],
    }


def write_report(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    md = f"""# AirLLM Heavy Tier Spike Report

Date: {time.strftime("%Y-%m-%d")}
Model: {report['model']}
Prompts: {report['num_prompts']}
Successes: {report['successes']}/{report['num_prompts']}

## Verdict: **{report['verdict']}**

{report['reasoning']}

## Performance
- Average tok/s: {report['avg_tok_per_s']:.2f}
- p50 tok/s: {report['p50_tok_per_s']:.2f}
- Average response duration: {report['avg_duration_s']:.0f}s
- Average judge quality (Sonnet-as-judge 0-1): {report['avg_quality']:.2f}

## Sample Responses
{json.dumps(report['sample_responses'], indent=2)}

## Decision Criteria
- GREEN: ≥3 tok/s, ≥0.7 quality → default heavy tier for Openjarvis + Ruflo refactors
- YELLOW: ≥1 tok/s OR <0.7 quality → opt-in tier, cloud Opus default
- RED: <1 tok/s OR fails entirely → drop AirLLM, always cloud
"""
    output_path.write_text(md)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="meta-llama/Llama-3.3-70B-Instruct")
    parser.add_argument("--num-prompts", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--quantization", choices=["4bit", "8bit"], default="4bit")
    parser.add_argument("--judge-quality", action="store_true", default=True)
    parser.add_argument("--no-judge", dest="judge_quality", action="store_false")
    parser.add_argument("--output", type=str, default="docs/spikes/airllm-heavy-spike.md")
    args = parser.parse_args()

    report = asyncio.run(run_spike(args))
    write_report(report, REPO_ROOT / args.output)
    print(f"\nVerdict: {report['verdict']}")
    print(f"Report: {args.output}")
    return 0 if report["verdict"] != "RED" else 1


if __name__ == "__main__":
    sys.exit(main())
