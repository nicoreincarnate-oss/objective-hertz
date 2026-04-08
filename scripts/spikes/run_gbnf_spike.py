#!/usr/bin/env python3
"""GBNF × mlx_lm.server proof-of-life spike — Plan 42-5-01 Task 3.

Tests whether outlines GBNF actually produces schema-conformant tool calls
through mlx_lm.server when running Qwen3-30B-A3B MLX-4bit. If this works,
verifier Layer 1 ships as-designed. If it fails, we fall back to JSON-mode +
post-hoc Pydantic validation.

Usage:
    python -m scripts.spikes.run_gbnf_spike \\
        --model-path /opt/perseus/models/qwen3-30b-a3b-mlx-4bit \\
        --port 11434 \\
        --num-prompts 100 \\
        --schema ruflo

Outputs:
    docs/spikes/gbnf-mlx-vlm-spike.md       (verdict + evidence)
    docs/spikes/gbnf-spike-results.jsonl    (raw per-prompt results)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================================
# Test schemas — pick one with --schema
# ============================================================================

SCHEMAS: dict[str, dict] = {
    "ruflo": {
        "name": "propose_patch",
        "description": "Propose a unified diff for a failing test",
        "schema": {
            "type": "object",
            "required": ["files", "diff", "rationale", "estimated_impact"],
            "properties": {
                "files": {"type": "array", "items": {"type": "string"}},
                "diff": {"type": "string"},
                "rationale": {"type": "string"},
                "estimated_impact": {"type": "number"},
            },
        },
    },
    "titan": {
        "name": "extract_lead",
        "description": "Extract a lead record from raw text",
        "schema": {
            "type": "object",
            "required": ["name", "email", "company", "score"],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "score": {"type": "integer"},
                "signals": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "deerflow": {
        "name": "score_source",
        "description": "Score a research source for adoption potential",
        "schema": {
            "type": "object",
            "required": ["score", "category", "rationale"],
            "properties": {
                "score": {"type": "number"},
                "category": {"type": "string", "enum": ["adopt", "test", "watch", "ignore"]},
                "rationale": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

# Sample prompts that should trigger each tool call
SAMPLE_PROMPTS: dict[str, list[str]] = {
    "ruflo": [
        "Propose a fix for: test_user_auth fails on macOS with KeyError 'session'. File: shared/auth.py:42",
        "Fix: test_payment_calculation expected $100, got $99.99. File: titan/billing.py",
        "Patch: test_email_validation rejects valid emails with + sign. File: shared/validators.py",
    ],
    "titan": [
        "Extract lead from: 'Dr. Smith DDS, smith@smithdental.com, 5 dentist office in Austin TX'",
        "Extract: 'Joe's Plumbing - joes@joesplumbing.net - residential plumber - Phoenix area'",
        "Find lead in: 'Tina Lee, founder Bloom Florist Studio, tina@bloomflorist.com, Brooklyn NY'",
    ],
    "deerflow": [
        "Score: 'h3lib - new Python HTTP/3 library, 412 stars, last commit 3 days ago'",
        "Score: 'AnotherTodoApp - generic todo list, 5 stars, no commits in 2 years'",
        "Score: 'mlx-vlm - Apple Silicon vision language models, 1.2k stars, weekly releases'",
    ],
}


@dataclass
class SpikeResult:
    prompt_idx: int
    grammar_constrained: bool
    success: bool
    response: str
    parse_error: str = ""
    schema_violations: list[str] = field(default_factory=list)
    latency_ms: int = 0


@dataclass
class SpikeReport:
    schema_name: str
    model: str
    hardware: str
    num_prompts: int
    grammar_pass_rate: float
    baseline_pass_rate: float
    grammar_p50_ms: int
    grammar_p95_ms: int
    baseline_p50_ms: int
    baseline_p95_ms: int
    latency_overhead_p50_ms: int
    latency_overhead_pct: float
    verdict: str
    verdict_reasoning: str
    failures: list[dict] = field(default_factory=list)


# ============================================================================
# Spike runner
# ============================================================================

async def run_one_call(
    prompt: str,
    *,
    api_base: str,
    model: str,
    grammar_text: str | None,
    schema: dict,
) -> SpikeResult:
    import httpx
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a tool-calling assistant. Output only JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }
    if grammar_text:
        payload["extra_body"] = {"grammar": grammar_text}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        content = data["choices"][0]["message"]["content"]

        # Validate
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return SpikeResult(
                prompt_idx=0,
                grammar_constrained=grammar_text is not None,
                success=False,
                response=content,
                parse_error=str(exc),
                latency_ms=latency_ms,
            )

        # Schema check
        violations = _validate(parsed, schema)
        return SpikeResult(
            prompt_idx=0,
            grammar_constrained=grammar_text is not None,
            success=not violations,
            response=content,
            schema_violations=violations,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        return SpikeResult(
            prompt_idx=0,
            grammar_constrained=grammar_text is not None,
            success=False,
            response="",
            parse_error=f"http error: {exc}",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


def _validate(obj: dict, schema: dict) -> list[str]:
    violations: list[str] = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for key in required:
        if key not in obj:
            violations.append(f"missing required: {key}")
    for key, value in obj.items():
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected_type = prop_schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            violations.append(f"{key}: expected string, got {type(value).__name__}")
        elif expected_type in ("number", "integer") and not isinstance(value, (int, float)):
            violations.append(f"{key}: expected number, got {type(value).__name__}")
        elif expected_type == "array" and not isinstance(value, list):
            violations.append(f"{key}: expected array, got {type(value).__name__}")
        elif expected_type == "object" and not isinstance(value, dict):
            violations.append(f"{key}: expected object, got {type(value).__name__}")
    return violations


async def run_spike(args: argparse.Namespace) -> SpikeReport:
    schema_def = SCHEMAS[args.schema]
    sample_prompts = SAMPLE_PROMPTS[args.schema]
    api_base = f"http://localhost:{args.port}/v1"

    # Compile grammar from schema
    try:
        from shared.verifier.grammar_compiler import GrammarCompiler
        compiler = GrammarCompiler(output_dir=Path("/tmp/perseus_spike_grammars"))
        grammar = compiler.compile(
            schema_def["schema"],
            daemon=args.schema,
            tool_name=schema_def["name"],
        )
        grammar_text = grammar.gbnf_text
    except Exception as exc:
        print(f"Grammar compilation failed: {exc}", file=sys.stderr)
        grammar_text = None

    print(f"Running {args.num_prompts} prompts × 2 paths (grammar + baseline)...")

    grammar_results: list[SpikeResult] = []
    baseline_results: list[SpikeResult] = []

    for i in range(args.num_prompts):
        prompt = sample_prompts[i % len(sample_prompts)]

        # Grammar-constrained
        gr = await run_one_call(
            prompt, api_base=api_base, model=args.model_name,
            grammar_text=grammar_text, schema=schema_def["schema"],
        )
        gr.prompt_idx = i
        grammar_results.append(gr)

        # Baseline (no grammar)
        br = await run_one_call(
            prompt, api_base=api_base, model=args.model_name,
            grammar_text=None, schema=schema_def["schema"],
        )
        br.prompt_idx = i
        baseline_results.append(br)

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{args.num_prompts} done")

    grammar_pass = sum(1 for r in grammar_results if r.success)
    baseline_pass = sum(1 for r in baseline_results if r.success)

    grammar_latencies = [r.latency_ms for r in grammar_results]
    baseline_latencies = [r.latency_ms for r in baseline_results]

    grammar_p50 = int(statistics.median(grammar_latencies))
    grammar_p95 = int(statistics.quantiles(grammar_latencies, n=20)[18]) if len(grammar_latencies) >= 20 else max(grammar_latencies)
    baseline_p50 = int(statistics.median(baseline_latencies))
    baseline_p95 = int(statistics.quantiles(baseline_latencies, n=20)[18]) if len(baseline_latencies) >= 20 else max(baseline_latencies)

    overhead_ms = grammar_p50 - baseline_p50
    overhead_pct = (overhead_ms / max(baseline_p50, 1)) * 100

    pass_rate = grammar_pass / args.num_prompts
    if pass_rate >= 0.99 and overhead_ms < 50:
        verdict = "GREEN"
        reasoning = f"{pass_rate:.0%} pass rate, {overhead_ms}ms overhead — ship as-is"
    elif pass_rate >= 0.90 and overhead_ms < 150:
        verdict = "YELLOW"
        reasoning = (
            f"{pass_rate:.0%} pass rate, {overhead_ms}ms overhead — proceed with monitoring "
            "and JSON-mode fallback as Layer 1.5"
        )
    else:
        verdict = "RED"
        reasoning = (
            f"{pass_rate:.0%} pass rate, {overhead_ms}ms overhead — REPLACE Layer 1 with "
            "JSON-mode + post-hoc Pydantic validation, update Phase 42.5 v2 plan"
        )

    failures = [
        {
            "idx": r.prompt_idx,
            "parse_error": r.parse_error,
            "violations": r.schema_violations,
            "response_preview": r.response[:200],
        }
        for r in grammar_results if not r.success
    ][:5]

    return SpikeReport(
        schema_name=args.schema,
        model=args.model_name,
        hardware="extrapolated_to_M4_Max_410GBs",
        num_prompts=args.num_prompts,
        grammar_pass_rate=pass_rate,
        baseline_pass_rate=baseline_pass / args.num_prompts,
        grammar_p50_ms=grammar_p50,
        grammar_p95_ms=grammar_p95,
        baseline_p50_ms=baseline_p50,
        baseline_p95_ms=baseline_p95,
        latency_overhead_p50_ms=overhead_ms,
        latency_overhead_pct=overhead_pct,
        verdict=verdict,
        verdict_reasoning=reasoning,
        failures=failures,
    )


def write_report(report: SpikeReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"""# GBNF × mlx_lm.server Spike Report

Schema: {report.schema_name}
Model: {report.model}
Hardware: {report.hardware}
Date: {time.strftime("%Y-%m-%d")}
Prompts tested: {report.num_prompts}

## Verdict: **{report.verdict}**

{report.verdict_reasoning}

## Pass Rates

|              | Grammar-constrained | Baseline (no grammar) |
|--------------|---------------------|------------------------|
| Pass rate    | {report.grammar_pass_rate:.0%}             | {report.baseline_pass_rate:.0%}                  |
| p50 latency  | {report.grammar_p50_ms}ms              | {report.baseline_p50_ms}ms                  |
| p95 latency  | {report.grammar_p95_ms}ms              | {report.baseline_p95_ms}ms                  |

## Latency Overhead
- p50: +{report.latency_overhead_p50_ms}ms ({report.latency_overhead_pct:.0f}%)

## Top Failures (first 5)
{json.dumps(report.failures, indent=2)}

## Decision Criteria
- GREEN: ≥99% pass rate, <50ms overhead → ship Layer 1 as designed
- YELLOW: 90-99% pass rate or 50-150ms overhead → ship + add JSON-mode fallback
- RED: <90% pass rate or >150ms overhead → replace Layer 1 with JSON-mode + Pydantic
""")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--model-name", type=str, default="qwen3-30b-a3b")
    parser.add_argument("--num-prompts", type=int, default=100)
    parser.add_argument("--schema", choices=list(SCHEMAS.keys()), default="ruflo")
    parser.add_argument("--output", type=str, default="docs/spikes/gbnf-mlx-vlm-spike.md")
    args = parser.parse_args()

    report = asyncio.run(run_spike(args))
    write_report(report, REPO_ROOT / args.output)
    print(f"\nSpike complete. Verdict: {report.verdict}")
    print(f"Report: {args.output}")
    return 0 if report.verdict != "RED" else 1


if __name__ == "__main__":
    sys.exit(main())
