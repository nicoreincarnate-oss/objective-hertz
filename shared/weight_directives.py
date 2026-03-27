# DEFERRED — not part of shipped scope. See module docstring for details.
"""
SEAL Weight Directives — NOT IMPLEMENTED.

Papers: SEAL (NeurIPS 2025, LLM generates own weight-update directives),
Transformer² (SVD-based dynamic weight adjustment at inference).

STATUS: Stub only. generate_weight_directive() asks an LLM to *describe*
a weight update, but apply_weight_directive() cannot actually modify model
weights. There is no SVD decomposition, no MLX integration, no delta
application. All three functions are uncalled across the codebase.

Gated behind SEAL_DIRECTIVES=1 (default 0). Even when enabled, no weights
are modified — the functions return NotImplementedError or log-only results.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("perseus.weight_directives")


def _seal_directives_enabled() -> bool:
    try:
        from shared.config import config
        return config.training.seal_directives
    except Exception:
        return False


async def generate_weight_directive(
    proposal: dict,
    model_name: str = "qwen2.5:14b",
) -> dict | None:
    """Ask an LLM to describe a hypothetical weight adjustment for a proposal.

    NOTE: This generates a *description* of what a weight edit would look
    like. It does NOT produce an actual SVD delta vector. The output is
    informational only and cannot be applied by apply_weight_directive().

    Returns a dict with layer, direction, magnitude, rationale — or None.
    """
    if not _seal_directives_enabled():
        return None

    try:
        from shared.llm_client import llm
        result = await llm.generate(
            f"Given this proposed system change, determine which model layer and "
            f"direction would implement this behavioral shift:\n\n"
            f"PROPOSAL: {json.dumps(proposal, default=str)[:500]}\n\n"
            f"Return JSON: {{\n"
            f'  "layer": "layers.N.self_attn.q_proj",\n'
            f'  "direction": "increase specificity / reduce hedging / etc",\n'
            f'  "magnitude": 0.01-0.1,\n'
            f'  "rationale": "why this layer controls this behavior"\n'
            f"}}",
            model="smart",
            temperature=0.2,
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        directive = json.loads(result[start:end])

        magnitude = directive.get("magnitude", 0.01)
        directive["revert_magnitude"] = -magnitude
        directive["applied"] = False

        return directive

    except Exception as e:
        logger.debug(f"SEAL directive generation failed: {e}")
        return None


async def apply_weight_directive(directive: dict, model_path: str = "") -> bool:
    """NOT IMPLEMENTED — cannot apply weight deltas.

    This would need:
    1. MLX model loading from model_path
    2. SVD decomposition of the target layer
    3. Delta vector computation from the directive's direction/magnitude
    4. Weight matrix modification and save
    5. Revert vector storage for rollback

    None of this exists. Returns False unconditionally.
    """
    if not _seal_directives_enabled() or not directive:
        return False

    logger.warning(
        "SEAL apply_weight_directive called but NOT IMPLEMENTED. "
        "Directive for layer '%s' (magnitude=%.3f) was NOT applied. "
        "No model weights were modified.",
        directive.get("layer", "?"),
        abs(directive.get("magnitude", 0)),
    )
    return False


async def measure_directive_impact(
    directive: dict,
    eval_prompts: list[str] | None = None,
) -> float:
    """Measure impact of an applied directive. Returns 0.0 since apply is unimplemented."""
    if not directive or not directive.get("applied"):
        return 0.0

    # Since apply_weight_directive never sets applied=True, this is unreachable.
    # Kept for future implementation.
    logger.debug("measure_directive_impact called but no directive was ever applied")
    return 0.0
