"""LoRA training manager for Perseus self-improvement.

Manages the fine-tuning lifecycle: data accumulation monitoring,
training triggers, model validation, Ollama import, A/B testing.
Integrates titan/training.py (execution) with shared/llm_client.py (routing).

Called by perseus/sleep_cycle.py during nightly optimization.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.config import config
from shared.db import execute, fetch_all, fetch_val, get_config, set_config

logger = logging.getLogger("perseus.lora_manager")

# Minimum labeled examples before training is allowed
MIN_EXAMPLES = 100
# Minimum days between training runs
MIN_DAYS_BETWEEN_RUNS = 7


@dataclass
class TrainingStatus:
    """Current state of the LoRA training pipeline."""

    examples_collected: int = 0
    examples_needed: int = MIN_EXAMPLES
    ready_to_train: bool = False
    last_training_date: str = ""
    current_model: str = ""
    fine_tuned_model: str = ""
    training_in_progress: bool = False
    mlx_available: bool = False
    vastai_available: bool = False


def _check_mlx_available() -> bool:
    """Check if mlx-lm is installed for local Apple Silicon training."""
    try:
        result = subprocess.run(
            ["python3", "-c", "import mlx_lm"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_vastai_available() -> bool:
    """Check if Vast.ai API key is configured for cloud GPU training."""
    import os

    return bool(os.environ.get("VAST_AI_API_KEY", ""))


async def get_training_status() -> TrainingStatus:
    """Check the current state of LoRA training readiness."""
    # Count labeled examples in the 30-day training window
    count = (
        await fetch_val(
            """SELECT COUNT(*) FROM training_data
               WHERE outcome IN ('positive', 'negative')
               AND created_at > NOW() - INTERVAL '30 days'"""
        )
        or 0
    )

    # Last training date from titan_learnings
    last_train_row = await fetch_val(
        """SELECT MAX(created_at) FROM titan_learnings
           WHERE category = 'lora_training'"""
    )
    last_date = ""
    if last_train_row:
        last_date = str(last_train_row)

    # Current and fine-tuned model names
    current_model = config.ollama.model
    ft_model = await get_config("fine_tuned_model", "")

    # Check if training is in progress (task queue)
    in_progress = bool(
        await fetch_val(
            """SELECT COUNT(*) FROM tasks
               WHERE task_type = 'lora_training'
               AND status = 'running'"""
        )
    )

    mlx = _check_mlx_available()
    vastai = _check_vastai_available()

    ready = (
        count >= MIN_EXAMPLES
        and not in_progress
        and (mlx or vastai)
    )

    # Check cooldown
    if ready and last_train_row:
        try:
            if datetime.now() - last_train_row < timedelta(days=MIN_DAYS_BETWEEN_RUNS):
                ready = False
        except (TypeError, ValueError):
            pass

    return TrainingStatus(
        examples_collected=count,
        examples_needed=MIN_EXAMPLES,
        ready_to_train=ready,
        last_training_date=last_date,
        current_model=current_model,
        fine_tuned_model=ft_model or "",
        training_in_progress=in_progress,
        mlx_available=mlx,
        vastai_available=vastai,
    )


async def check_and_trigger_training() -> bool:
    """Check if training should run and trigger it if ready.

    Called by sleep_cycle or scheduler. Delegates readiness checks to
    titan/training.should_train() and execution to titan/training.run_lora_training().

    Returns True if training was triggered.
    """
    from titan.training import run_lora_training, should_train

    if not await should_train():
        return False

    logger.info("LoRA training conditions met, triggering training run")
    try:
        await run_lora_training()
        return True
    except Exception as e:
        logger.error("LoRA training run failed: %s", e)
        return False


# ── Validation ───────────────────────────────────────────────────

_VALIDATION_PROMPTS = [
    {
        "name": "email_composition",
        "system": "You are Titan, Perseus's revenue engine. Write cold emails that get replies.",
        "prompt": (
            "Write a cold outreach email to a dentist in Austin, TX who needs a new website. "
            "Their current site is outdated and not mobile-friendly."
        ),
        "min_length": 100,
    },
    {
        "name": "lead_scoring",
        "system": "Score this lead from 0-100 based on fit and intent signals.",
        "prompt": (
            "Lead: John's Plumbing, Austin TX. Revenue ~$500K/yr. Current website is a "
            "Facebook page only. Recently posted hiring ad. Score and explain."
        ),
        "min_length": 20,
    },
    {
        "name": "follow_up_timing",
        "system": "You decide optimal follow-up timing for cold email sequences.",
        "prompt": (
            "First email sent Monday 9am, opened Tuesday 2pm, no reply. "
            "Industry: dental. When should the follow-up go out and why?"
        ),
        "min_length": 30,
    },
]


async def validate_fine_tuned_model(model_name: str) -> dict:
    """Validate a fine-tuned model before deployment.

    Runs test prompts against both the candidate and the base model,
    comparing output quality on length, coherence, and format.

    Returns: {"valid": bool, "scores": dict, "comparison": str}
    """
    import httpx

    base_model = config.ollama.model
    scores: dict[str, dict] = {}
    passed = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        for test in _VALIDATION_PROMPTS:
            # Test the fine-tuned model
            try:
                ft_resp = await client.post(
                    f"{config.ollama.host}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": test["prompt"],
                        "system": test["system"],
                        "stream": False,
                        "options": {"num_predict": 512, "temperature": 0.7},
                    },
                )
                if ft_resp.status_code != 200:
                    scores[test["name"]] = {"error": f"HTTP {ft_resp.status_code}"}
                    continue
                ft_output = ft_resp.json().get("response", "")
            except Exception as e:
                scores[test["name"]] = {"error": str(e)}
                continue

            # Test the base model for comparison
            try:
                base_resp = await client.post(
                    f"{config.ollama.host}/api/generate",
                    json={
                        "model": base_model,
                        "prompt": test["prompt"],
                        "system": test["system"],
                        "stream": False,
                        "options": {"num_predict": 512, "temperature": 0.7},
                    },
                )
                base_output = base_resp.json().get("response", "") if base_resp.status_code == 200 else ""
            except Exception:
                base_output = ""

            # Score: length check, non-empty, no degenerate repetition
            ft_len = len(ft_output)
            base_len = len(base_output)
            min_len = test["min_length"]

            # Detect degenerate repetition (same 20-char chunk repeated 5+ times)
            degenerate = False
            if ft_len > 100:
                chunk = ft_output[50:70]
                if chunk and ft_output.count(chunk) >= 5:
                    degenerate = True

            test_passed = ft_len >= min_len and not degenerate
            if test_passed:
                passed += 1

            scores[test["name"]] = {
                "ft_length": ft_len,
                "base_length": base_len,
                "min_required": min_len,
                "degenerate": degenerate,
                "passed": test_passed,
            }

    total = len(_VALIDATION_PROMPTS)
    valid = passed >= (total * 2 // 3)  # Must pass at least 2/3 of tests

    comparison = f"{passed}/{total} tests passed"
    if not valid:
        comparison += " (FAILED: model not ready for deployment)"
    else:
        comparison += " (PASSED: model ready for deployment)"

    return {"valid": valid, "scores": scores, "comparison": comparison}


# ── Activation / Deactivation ────────────────────────────────────

async def activate_model(model_name: str) -> bool:
    """Activate a fine-tuned model for pipeline use.

    Sets system_config 'fine_tuned_model' so llm_client._resolve_ollama_model()
    routes pipeline stages to it. Validates first unless already validated.
    """
    # Verify the model exists in Ollama
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if model_name not in (result.stdout or ""):
            logger.error("Model %s not found in Ollama", model_name)
            return False
    except Exception as e:
        logger.error("Cannot verify model in Ollama: %s", e)
        return False

    await set_config("fine_tuned_model", model_name)
    logger.info("Activated fine-tuned model: %s", model_name)
    from shared.db import emit_event

    await emit_event("lora_model_activated", {"model": model_name})
    return True


async def deactivate_model() -> bool:
    """Revert to base model by removing fine_tuned_model config.

    Used when fine-tuned model underperforms or A/B test shows regression.
    """
    current = await get_config("fine_tuned_model")
    if not current:
        logger.info("No fine-tuned model active, nothing to deactivate")
        return True

    await set_config("fine_tuned_model", "")
    logger.info("Deactivated fine-tuned model: %s (reverted to base)", current)
    from shared.db import emit_event

    await emit_event("lora_model_deactivated", {"model": current})
    return True


# ── A/B Testing ──────────────────────────────────────────────────

async def get_ab_test_results() -> dict:
    """Compare fine-tuned vs base model performance.

    Queries email_sequences + outreach_metrics for emails composed with
    each model, comparing open rates, reply rates, and bounce rates.

    Returns: {
        "has_data": bool,
        "fine_tuned": {"emails": int, "open_rate": float, "reply_rate": float},
        "base": {"emails": int, "open_rate": float, "reply_rate": float},
        "recommendation": str,
    }
    """
    ft_model = await get_config("fine_tuned_model", "")

    # Query emails tagged with model info in metadata
    ft_stats = await fetch_all(
        """SELECT
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE opened_at IS NOT NULL) as opened,
               COUNT(*) FILTER (WHERE replied_at IS NOT NULL) as replied,
               COUNT(*) FILTER (WHERE bounced = TRUE) as bounced
           FROM email_sequences
           WHERE metadata->>'model' IS NOT NULL
           AND metadata->>'model' != %s""",
        (config.ollama.model,),
    )

    base_stats = await fetch_all(
        """SELECT
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE opened_at IS NOT NULL) as opened,
               COUNT(*) FILTER (WHERE replied_at IS NOT NULL) as replied,
               COUNT(*) FILTER (WHERE bounced = TRUE) as bounced
           FROM email_sequences
           WHERE metadata->>'model' IS NULL
           OR metadata->>'model' = %s""",
        (config.ollama.model,),
    )

    def _compute_rates(stats_rows):
        if not stats_rows:
            return {"emails": 0, "open_rate": 0.0, "reply_rate": 0.0, "bounce_rate": 0.0}
        row = stats_rows[0]
        total = row.get("total", 0) or 0
        if total == 0:
            return {"emails": 0, "open_rate": 0.0, "reply_rate": 0.0, "bounce_rate": 0.0}
        return {
            "emails": total,
            "open_rate": round((row.get("opened", 0) or 0) / total, 3),
            "reply_rate": round((row.get("replied", 0) or 0) / total, 3),
            "bounce_rate": round((row.get("bounced", 0) or 0) / total, 3),
        }

    ft_rates = _compute_rates(ft_stats)
    base_rates = _compute_rates(base_stats)

    has_data = ft_rates["emails"] >= 10 and base_rates["emails"] >= 10

    # Recommendation
    recommendation = "insufficient data for comparison"
    if has_data:
        ft_score = ft_rates["reply_rate"] * 2 + ft_rates["open_rate"] - ft_rates["bounce_rate"]
        base_score = base_rates["reply_rate"] * 2 + base_rates["open_rate"] - base_rates["bounce_rate"]
        if ft_score > base_score * 1.05:
            recommendation = "fine-tuned model outperforms base — keep active"
        elif base_score > ft_score * 1.05:
            recommendation = "base model outperforms fine-tuned — consider deactivating"
        else:
            recommendation = "performance is similar — continue testing"

    return {
        "has_data": has_data,
        "fine_tuned_model": ft_model,
        "fine_tuned": ft_rates,
        "base": base_rates,
        "recommendation": recommendation,
    }
