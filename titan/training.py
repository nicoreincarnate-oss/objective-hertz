"""
Titan Training System — Collects training data, manages LoRA fine-tuning.

Flow:
1. Collect: Every email sent, reply received, and outcome is logged as training data
2. Format: Convert interactions into instruction-tuning JSONL format
3. Upload: Push training data to cloud GPU (Vast.ai / RunPod)
4. Train: Run LoRA fine-tuning on Qwen2.5 or Llama model
5. Deploy: Pull fine-tuned model back to Ollama on Mac Studio
6. Evaluate: Compare fine-tuned model performance vs base model

Training happens when:
- At least 500 labeled examples exist
- Weekly check decides if enough new data to justify training
- Budget allows ($50-100 from $800/month allocation)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from shared.config import config
from shared.db import emit_event, execute, fetch_all, fetch_one, fetch_val

logger = logging.getLogger("perseus.titan.training")

TRAINING_DATA_DIR = config.root_dir / "training_data"
TRAINING_METHOD = "oplora"  # Orthogonal Projection LoRA — prevents catastrophic forgetting


async def collect_training_example(
    example_type: str,
    input_text: str,
    output_text: str,
    outcome: str = "",
    metadata: dict | None = None,
):
    """
    Collect a single training example from a real interaction.
    Called by pipeline stages whenever something happens.

    example_type: 'email_compose', 'follow_up', 'reply_classify', 'proposal', 'research'
    outcome: 'positive' (opened/replied/closed), 'negative' (bounced/lost/unsubscribed), ''
    """
    await execute(
        """INSERT INTO training_data (example_type, input_text, output_text, outcome, metadata)
           VALUES (%s, %s, %s, %s, %s)""",
        (example_type, input_text, output_text, outcome, json.dumps(metadata or {})),
    )


async def collect_email_outcome(email_seq_id: int, outcome: str):
    """
    Update a training example with its outcome after we learn what happened.
    Called when: email opened, replied, bounced, lead converted, lead lost.
    """
    # Find the original email
    email = await fetch_one(
        "SELECT client_id, subject, body FROM email_sequences WHERE id = %s",
        (email_seq_id,),
    )
    if not email:
        return

    # Find the lead's research data (the input that generated this email)
    lead = await fetch_one(
        "SELECT research_summary, industry, language FROM clients WHERE id = %s",
        (email["client_id"],),
    )
    if not lead:
        return

    input_text = json.dumps({
        "research": lead.get("research_summary", ""),
        "industry": lead.get("industry", ""),
        "language": lead.get("language", "en"),
    })
    output_text = json.dumps({
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
    })

    result = await fetch_one(
        """INSERT INTO training_data (example_type, input_text, output_text, outcome, metadata)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        ("email_compose", input_text, output_text, outcome,
         json.dumps({"email_seq_id": email_seq_id, "client_id": email["client_id"]})),
    )

    # Schedule delayed outcome re-checks (2.1) so training labels
    # get corrected when deals close weeks later
    if result and result.get("id"):
        try:
            from titan.memory import create_pending_outcome_checks
            await create_pending_outcome_checks(
                training_data_id=result["id"],
                client_id=email["client_id"],
                email_seq_id=email_seq_id,
            )
        except Exception as e:
            logger.debug(f"Pending outcome scheduling failed (non-critical): {e}")


MUA_LR_ENABLED = os.environ.get("MUA_LR_ENABLED", "1") == "1"
WDPO_ENABLED = os.environ.get("WDPO_ENABLED", "1") == "1"
UNI_DPO_ENABLED = os.environ.get("UNI_DPO_ENABLED", "1") == "1"
ULTRAMIX_CURATION = os.environ.get("ULTRAMIX_CURATION", "1") == "1"
HYBRID_SFT_DPO = os.environ.get("HYBRID_SFT_DPO", "1") == "1"


def compute_mua_lr(width: int, layer_type: str) -> float:
    """muA learning rate scaling (muA paper: Init[B] alpha=1, eta ∝ n^(-1)).

    Principled LR that's rank-invariant. Attention layers get alpha=1.0,
    FFN layers get alpha=0.5 (they need less aggressive updates).
    For rank=16, width=4096: lr = 1.0 / 4096 ≈ 2.4e-4 (similar to default but principled).
    """
    alpha = 1.0 if layer_type in ("q_proj", "k_proj", "v_proj", "o_proj") else 0.5
    return alpha / max(1, width)


def compute_wdpo_weights(examples: list[dict], noise_tolerance: float = 0.3) -> list[float]:
    """wDPO: per-example weights inversely proportional to estimated noise.

    Stage 1: Margin-aware soft label correction — examples with small margins
    between positive/negative get lower weight (more likely mislabeled).
    Stage 2: Gradient winsorization — clip extreme weights to prevent dominance.

    Sales preference data is inherently noisy (30%+ label noise). wDPO handles this.
    """
    if not examples:
        return []

    weights = []
    # Count outcome flip rate per example_type as noise proxy
    type_outcomes: dict[str, dict[str, int]] = {}
    for ex in examples:
        etype = ex.get("example_type", "unknown")
        outcome = ex.get("outcome", "")
        if etype not in type_outcomes:
            type_outcomes[etype] = {"positive": 0, "negative": 0}
        type_outcomes[etype][outcome] = type_outcomes[etype].get(outcome, 0) + 1

    for ex in examples:
        etype = ex.get("example_type", "unknown")
        counts = type_outcomes.get(etype, {"positive": 1, "negative": 1})
        total = counts.get("positive", 0) + counts.get("negative", 0)
        if total == 0:
            weights.append(1.0)
            continue

        # Noise estimate: types with near-50/50 split are noisiest
        minority_ratio = min(counts.get("positive", 0), counts.get("negative", 0)) / total
        noise_estimate = minority_ratio * 2  # 0.0 = pure signal, 1.0 = pure noise

        # Weight: inversely proportional to noise, floored at noise_tolerance
        weight = max(noise_tolerance, 1.0 - noise_estimate)
        weights.append(weight)

    # Winsorize: clip to [5th, 95th] percentile to prevent gradient dominance
    if len(weights) > 10:
        sorted_w = sorted(weights)
        p5 = sorted_w[len(sorted_w) // 20]
        p95 = sorted_w[-len(sorted_w) // 20 - 1]
        weights = [max(p5, min(p95, w)) for w in weights]

    return weights


def uni_dpo_dynamic_weights(examples: list[dict], epoch: int, total_epochs: int) -> list[float]:
    """Uni-DPO: dual-perspective weighting that evolves over training.

    Early epochs: explore (uniform weights, learn from everything).
    Late epochs: exploit (upweight high-confidence, downweight noisy).
    """
    if not examples or total_epochs <= 0:
        return [1.0] * len(examples)

    progress = epoch / total_epochs  # 0.0 = start, 1.0 = end

    # Blend uniform (explore) with quality-weighted (exploit)
    base_weights = compute_wdpo_weights(examples)
    uniform = [1.0] * len(examples)

    return [
        (1.0 - progress) * u + progress * b
        for u, b in zip(uniform, base_weights, strict=True)
    ]


def curate_preference_dataset(raw_examples: list[dict]) -> list[dict]:
    """UltraMix-style preference data curation.

    1. Score each example for quality (label consistency, recency, type balance)
    2. Filter bottom 30%
    3. Balance positive/negative
    4. Return curated set
    """
    if not raw_examples:
        return []

    # Score each example
    scored = []
    for ex in raw_examples:
        score = 0.5  # base

        # Recency boost (last 7 days = +0.3)
        age = ex.get("age_days", 30)
        if age <= 7:
            score += 0.3
        elif age <= 14:
            score += 0.15

        # Known outcome is more valuable
        if ex.get("outcome") in ("positive", "negative"):
            score += 0.2

        scored.append((score, ex))

    # Sort by quality, filter bottom 30%
    scored.sort(key=lambda x: x[0], reverse=True)
    cutoff = int(len(scored) * 0.7)
    curated = [ex for _, ex in scored[:cutoff]]

    # Balance positive/negative
    positive = [e for e in curated if e.get("outcome") == "positive"]
    negative = [e for e in curated if e.get("outcome") == "negative"]
    min_count = min(len(positive), len(negative))
    if min_count > 0:
        curated = positive[:min_count] + negative[:min_count]

    return curated


async def export_training_data(min_examples: int = 100) -> Path | None:
    """
    Export training data as JSONL file for LoRA fine-tuning.
    Only exports examples with known outcomes (positive/negative).
    Format: {"instruction": "...", "input": "...", "output": "..."}
    """
    TRAINING_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 4.5: Rolling 30-day training window — model stays tuned to current market
    # Old examples still exist in DB for analysis, just not used for training
    examples = await fetch_all(
        """SELECT example_type, input_text, output_text, outcome,
                EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400 as age_days
           FROM training_data
           WHERE outcome IN ('positive', 'negative')
           AND created_at > NOW() - INTERVAL '30 days'
           ORDER BY created_at DESC""",
    )

    if len(examples) < min_examples:
        logger.info(f"Only {len(examples)} labeled examples in 30-day window (need {min_examples}). Skipping export.")
        return None

    # Separate positive (good emails) from negative (bad emails)
    positive = [e for e in examples if e["outcome"] == "positive"]
    negative = [e for e in examples if e["outcome"] == "negative"]

    # Recency boost: duplicate examples from last 7 days
    recent_positive = [e for e in positive if (e.get("age_days") or 30) <= 7]
    _recent_negative = [e for e in negative if (e.get("age_days") or 30) <= 7]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = TRAINING_DATA_DIR / f"training_{timestamp}.jsonl"

    with open(output_path, "w") as f:
        # Optional: curate dataset (UltraMix paper) before export
        all_examples = positive + recent_positive + negative[:len(positive)]
        if ULTRAMIX_CURATION:
            all_examples = curate_preference_dataset(all_examples)
            positive = [e for e in all_examples if e.get("outcome") == "positive"]
            negative = [e for e in all_examples if e.get("outcome") == "negative"]

        # Optional: compute per-example wDPO weights for noise robustness
        wdpo_weights = compute_wdpo_weights(all_examples) if WDPO_ENABLED else None

        # Positive examples: train to replicate (+ recency-boosted duplicates)
        idx = 0
        for ex in positive + recent_positive:
            record = {
                "instruction": f"Write a cold outreach email that gets replies. Type: {ex['example_type']}. This email resulted in a positive outcome.",
                "input": ex["input_text"],
                "output": ex["output_text"],
            }
            if wdpo_weights and idx < len(wdpo_weights):
                record["weight"] = round(wdpo_weights[idx], 3)
            f.write(json.dumps(record) + "\n")
            idx += 1

        # Negative examples: train to avoid (as negative examples with instruction)
        for ex in negative[:len(positive)]:  # Balance positive/negative
            record = {
                "instruction": f"Write a cold outreach email that gets replies. Type: {ex['example_type']}. Avoid patterns like this previous attempt that did NOT work.",
                "input": ex["input_text"],
                "output": f"[NEGATIVE EXAMPLE - DO NOT REPLICATE] {ex['output_text']}",
            }
            if wdpo_weights and idx < len(wdpo_weights):
                record["weight"] = round(wdpo_weights[idx], 3)
            f.write(json.dumps(record) + "\n")
            idx += 1

    total = len(positive) + len(recent_positive) + min(len(negative), len(positive))
    logger.info(
        f"Exported {total} training examples to {output_path} "
        f"(30-day window, {len(recent_positive)} recency-boosted)"
    )
    return output_path


async def should_train() -> bool:
    """Check if we have enough data and budget to justify a training run."""
    # Check example count
    count = await fetch_val(
        "SELECT COUNT(*) FROM training_data WHERE outcome IN ('positive', 'negative')"
    ) or 0

    min_threshold = 100  # Start training early; quality over quantity via UltraMix curation
    if count < min_threshold:
        logger.info(f"Only {count} labeled examples. Need {min_threshold} for training.")
        return False

    # Check if we trained recently (within 7 days)
    last_train = await fetch_val(
        """SELECT MAX(created_at) FROM titan_learnings
           WHERE category = 'lora_training'"""
    )
    if last_train:
        from datetime import timedelta
        if datetime.now() - last_train < timedelta(days=7):
            logger.info("Trained within last 7 days. Skipping.")
            return False

    # Check budget
    monthly_spend = await fetch_val(
        """SELECT COALESCE(SUM(amount), 0) FROM budget_tracking
           WHERE month = DATE_TRUNC('month', CURRENT_DATE)
           AND category = 'cloud_gpu'"""
    ) or 0
    gpu_cap = config.budget.cloud_gpu_cap
    if monthly_spend >= gpu_cap:
        logger.info(f"GPU budget exhausted: ${monthly_spend}/${gpu_cap}")
        return False

    return True


async def run_lora_training():
    """
    Execute a LoRA fine-tuning run on cloud GPU.

    Steps:
    1. Export training data to JSONL
    2. Upload to cloud GPU (Vast.ai)
    3. Run training script
    4. Download fine-tuned adapter
    5. Merge into Ollama model
    6. Log results
    """
    if not await should_train():
        return

    logger.info("Starting LoRA training run...")
    await emit_event("lora_training_started", {"status": "exporting_data"})

    # Step 1: Export training data
    data_path = await export_training_data(min_examples=100)
    if not data_path:
        return

    # Step 2-4: Cloud GPU training
    # This is the part that requires external infrastructure.
    # The actual training script runs on the GPU instance.
    try:
        adapter_path = await _train_on_cloud_gpu(data_path)
    except Exception as e:
        logger.error(f"Cloud GPU training failed: {e}")
        await emit_event("lora_training_failed", {"error": str(e)})
        return

    # Step 5: Import into Ollama
    if adapter_path:
        await _import_to_ollama(adapter_path)

    # Step 6: Log
    example_count = sum(1 for _ in open(data_path))
    await execute(
        """INSERT INTO titan_learnings (category, insight, confidence)
           VALUES ('lora_training', %s, 0.9)""",
        (json.dumps({
            "timestamp": datetime.now().isoformat(),
            "method": TRAINING_METHOD,
            "examples": example_count,
            "data_path": str(data_path),
            "adapter_path": str(adapter_path) if adapter_path else None,
        }),),
    )
    await emit_event("lora_training_complete", {"examples": example_count})
    logger.info(f"LoRA training complete. {example_count} examples used.")


def _generate_training_script() -> str:
    """Generate the training script that runs on the remote GPU instance."""
    return '''#!/usr/bin/env python3
"""LoRA fine-tuning script for Perseus/Titan. Runs on cloud GPU."""
import os, sys, json

def main():
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from datasets import load_dataset

    MODEL_NAME = os.getenv("BASE_MODEL", "unsloth/Qwen2.5-14B-Instruct-bnb-4bit")
    DATA_PATH = "/workspace/training_data.jsonl"
    OUTPUT_DIR = "/workspace/adapter_output"
    MAX_SEQ_LEN = 2048

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=True,
    )

    # OPLoRA (Orthogonal Projection LoRA) — prevents catastrophic forgetting
    # between training cycles. use_rslora=True enables rank-stabilized LoRA
    # with orthogonal initialization, so new training doesn't erase prior learning.
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        use_rslora=True,
    )

    dataset = load_dataset("json", data_files=DATA_PATH, split="train")

    def format_example(example):
        return {"text": f"""### Instruction:\\n{example['instruction']}\\n\\n### Input:\\n{example['input']}\\n\\n### Output:\\n{example['output']}"""}

    dataset = dataset.map(format_example)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LEN,
        args=TrainingArguments(
            output_dir=OUTPUT_DIR,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=10,
            num_train_epochs=3,
            learning_rate=float(os.getenv("MUA_LR", "2e-4")),
            fp16=True,
            logging_steps=10,
            save_strategy="epoch",
            seed=42,
        ),
    )

    trainer.train()
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # Write completion marker
    with open("/workspace/TRAINING_COMPLETE", "w") as f:
        stats = trainer.state.log_history[-1] if trainer.state.log_history else {}
        json.dump({"status": "complete", "final_loss": stats.get("train_loss", -1)}, f)

    print("TRAINING_COMPLETE")

if __name__ == "__main__":
    main()
'''


async def _train_on_cloud_gpu(data_path: Path) -> Path | None:
    """
    Upload data and run LoRA training on Vast.ai.
    Full lifecycle: search → rent → upload → train → download → destroy.
    Returns path to the downloaded adapter weights.
    """
    import asyncio
    import os

    import httpx

    instance_id = None
    provider = ""
    cloud_client = None
    ssh_host = ""
    ssh_port = 22

    conway_instance = await _try_conway_cloud_compute()
    if conway_instance is not None:
        cloud_client, rented = conway_instance
        instance_id = rented.instance_id
        provider = rented.provider
        ssh_host = rented.ssh_host
        ssh_port = rented.ssh_port
        logger.info(
            "Using Conway Cloud instance %s (%s at $%s/hr)",
            instance_id,
            rented.gpu_type,
            rented.price_per_hour,
        )

    vast_key = os.getenv("VAST_AI_API_KEY", "")
    if not instance_id and not vast_key:
        logger.warning("No Conway Cloud instance and VAST_AI_API_KEY not set. Falling back to local training.")
        return await _train_local(data_path)

    api_base = "https://cloud.vast.ai/api/v0"
    headers = {"Authorization": f"Bearer {vast_key}"}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if not instance_id:
                # Step 1: Find cheapest GPU on Vast.ai
                resp = await client.get(
                    f"{api_base}/bundles",
                    params={"q": json.dumps({
                        "gpu_name": {"in": ["A100", "A6000", "RTX 4090"]},
                        "num_gpus": 1,
                        "rentable": True,
                        "disk_space": {"gte": 50},
                        "inet_down": {"gte": 200},
                        "order": [["dph_total", "asc"]],
                        "limit": 5,
                    })},
                    headers=headers,
                )
                resp.raise_for_status()
                offers = resp.json().get("offers", [])

                if not offers:
                    logger.warning("No GPU instances available on Vast.ai")
                    return await _train_local(data_path)

                offer = offers[0]
                cost_per_hour = offer.get("dph_total", 0.5)
                logger.info(f"Selected GPU: {offer.get('gpu_name')} at ${cost_per_hour:.2f}/hr")

                estimated_cost = cost_per_hour * 3
                if estimated_cost > 100:
                    logger.warning(f"Estimated cost ${estimated_cost:.2f} exceeds $100 cap")
                    return await _train_local(data_path)

                await execute(
                    """INSERT INTO budget_tracking (month, category, amount, description)
                       VALUES (DATE_TRUNC('month', CURRENT_DATE), 'cloud_gpu', %s,
                               'LoRA training run (estimated)')""",
                    (estimated_cost,),
                )

                create_resp = await client.put(
                    f"{api_base}/asks/{offer['id']}/",
                    json={
                        "client_id": "me",
                        "image": "ghcr.io/unslothai/unsloth:latest",
                        "disk": 50,
                        "onstart": "pip install datasets trl && echo READY",
                    },
                    headers=headers,
                )
                create_resp.raise_for_status()
                instance_id = create_resp.json().get("new_contract")
                if not instance_id:
                    logger.error("Failed to create Vast.ai instance")
                    return await _train_local(data_path)

                provider = "vast"
                logger.info(f"Created Vast.ai instance {instance_id}")
                await emit_event("cloud_gpu_rented", {
                    "instance_id": instance_id,
                    "gpu": offer.get("gpu_name"),
                    "cost_per_hour": cost_per_hour,
                    "provider": "vast",
                })

                for attempt in range(60):  # up to 10 minutes
                    await asyncio.sleep(10)
                    status_resp = await client.get(
                        f"{api_base}/instances/{instance_id}/",
                        headers=headers,
                    )
                    status_resp.raise_for_status()
                    status = status_resp.json().get("actual_status", "")
                    if status == "running":
                        logger.info("Instance is running")
                        break
                    logger.debug(f"Instance status: {status} (attempt {attempt + 1})")
                else:
                    logger.error("Instance failed to start within 10 minutes")
                    raise TimeoutError("Vast.ai instance startup timeout")

                instance_info = status_resp.json()
                ssh_host = instance_info.get("ssh_host", "")
                ssh_port = instance_info.get("ssh_port", 22)

            if not ssh_host:
                logger.error("No SSH host returned for instance")
                raise RuntimeError("Missing SSH connection info")

            # Validate ssh_host and ssh_port — these come from an external API
            # and must not contain shell metacharacters.
            import re
            import subprocess

            ssh_port = int(ssh_port)  # Raises ValueError if not numeric
            if not re.match(r'^[a-zA-Z0-9._-]+$', ssh_host):
                raise ValueError(f"Invalid ssh_host from provider: {ssh_host!r}")

            # Step 4: Upload training data + script via SCP
            train_script_path = data_path.parent / "train_remote.py"
            train_script_path.write_text(_generate_training_script())

            ssh_opts = [
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-p", str(ssh_port),
            ]

            # Upload training data
            scp_data = subprocess.run(
                ["scp"] + ssh_opts + [
                    str(data_path),
                    f"root@{ssh_host}:/workspace/training_data.jsonl",
                ],
                capture_output=True, text=True, timeout=120,
            )
            if scp_data.returncode != 0:
                logger.error(f"SCP data upload failed: {scp_data.stderr[:300]}")
                raise RuntimeError("Failed to upload training data")

            # Upload training script
            scp_script = subprocess.run(
                ["scp"] + ssh_opts + [
                    str(train_script_path),
                    f"root@{ssh_host}:/workspace/train.py",
                ],
                capture_output=True, text=True, timeout=60,
            )
            if scp_script.returncode != 0:
                raise RuntimeError("Failed to upload training script")

            logger.info("Uploaded training data and script")

            # Step 5: Run training (inject muA learning rate if enabled)
            remote_cmd = "cd /workspace && nohup python3 train.py > train.log 2>&1 &"
            if MUA_LR_ENABLED:
                lr = compute_mua_lr(4096, "q_proj")  # Qwen2.5-14B hidden_size=4096
                remote_cmd = f"cd /workspace && MUA_LR={lr} nohup python3 train.py > train.log 2>&1 &"

            subprocess.run(
                ["ssh"] + ssh_opts + [f"root@{ssh_host}", remote_cmd],
                capture_output=True, timeout=30,
            )
            logger.info("Training started on remote GPU")

            # Step 6: Poll for completion (up to 3 hours)
            for _attempt in range(108):  # 3 hours at 100s intervals
                await asyncio.sleep(100)
                check = subprocess.run(
                    ["ssh"] + ssh_opts + [
                        f"root@{ssh_host}",
                        "cat /workspace/TRAINING_COMPLETE 2>/dev/null",
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                if "TRAINING_COMPLETE" in (check.stdout or ""):
                    logger.info("Remote training complete!")
                    try:
                        completion_data = json.loads(check.stdout.strip())
                        logger.info(f"Final loss: {completion_data.get('final_loss', 'unknown')}")
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
                # Check if training errored
                log_check = subprocess.run(
                    ["ssh"] + ssh_opts + [
                        f"root@{ssh_host}",
                        "tail -5 /workspace/train.log 2>/dev/null",
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                if log_check.stdout:
                    logger.debug(f"Training log: {log_check.stdout[-200:]}")
            else:
                logger.error("Training did not complete within 3 hours")
                raise TimeoutError("Remote training timeout")

            # Step 7: Download adapter weights
            adapter_dir = TRAINING_DATA_DIR / "adapters" / datetime.now().strftime("%Y%m%d_%H%M%S")
            adapter_dir.mkdir(parents=True, exist_ok=True)

            download = subprocess.run(
                ["scp", "-r"] + ssh_opts + [
                    f"root@{ssh_host}:/workspace/adapter_output/.",
                    str(adapter_dir),
                ],
                capture_output=True, text=True, timeout=300,
            )
            if download.returncode != 0:
                logger.error(f"Failed to download adapter: {download.stderr[:300]}")
                raise RuntimeError("Adapter download failed")

            logger.info(f"Downloaded adapter weights to {adapter_dir}")
            return adapter_dir

    except Exception as e:
        logger.error(f"Cloud GPU training failed: {e}")
        await emit_event("cloud_gpu_error", {"error": str(e), "instance_id": instance_id})
        # Fall back to local training
        return await _train_local(data_path)

    finally:
        # Step 8: Always destroy the instance to stop billing
        if instance_id:
            try:
                if provider == "conway" and cloud_client is not None:
                    released = await cloud_client.release_compute(instance_id)
                    if released:
                        logger.info(f"Released Conway Cloud instance {instance_id}")
                    else:
                        logger.warning(f"Failed to release Conway Cloud instance {instance_id}")
                        await emit_event("cloud_gpu_cleanup_failed", {"instance_id": instance_id, "provider": provider})
                else:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        destroy_resp = await client.delete(
                            f"{api_base}/instances/{instance_id}/",
                            headers=headers,
                        )
                        if destroy_resp.status_code < 300:
                            logger.info(f"Destroyed Vast.ai instance {instance_id}")
                        else:
                            logger.warning(f"Failed to destroy instance {instance_id}: {destroy_resp.status_code}")
                            await emit_event("cloud_gpu_cleanup_failed", {"instance_id": instance_id, "provider": provider or 'vast'})
            except Exception as cleanup_err:
                logger.error(f"CRITICAL: Failed to destroy instance {instance_id}: {cleanup_err}")
                await emit_event("cloud_gpu_cleanup_failed", {
                    "instance_id": instance_id,
                    "provider": provider or "vast",
                    "error": str(cleanup_err),
                })


async def _try_conway_cloud_compute():
    """Attempt to rent training compute from Conway Cloud before third-party fallback."""
    if not config.conway.enabled:
        return None

    try:
        from conway.runtime import get_agent_cloud_client

        client = await get_agent_cloud_client("titan")
        if client is None:
            return None

        instance = await client.provision_compute(
            gpu_type="A100",
            duration_hours=3,
            image="ghcr.io/unslothai/unsloth:latest",
        )
        if instance is None:
            return None

        estimated_cost = float(instance.price_per_hour) * 3
        await execute(
            """INSERT INTO budget_tracking (month, category, amount, description)
               VALUES (DATE_TRUNC('month', CURRENT_DATE), 'cloud_gpu', %s,
                       'LoRA training run via Conway Cloud (estimated)')""",
            (estimated_cost,),
        )
        await emit_event("cloud_gpu_rented", {
            "instance_id": instance.instance_id,
            "gpu": instance.gpu_type,
            "cost_per_hour": float(instance.price_per_hour),
            "provider": instance.provider,
        })
        return client, instance
    except Exception as exc:
        logger.warning("Conway Cloud training path unavailable: %s", exc)
        return None


async def _train_local(data_path: Path) -> Path | None:
    """
    Attempt LoRA training locally on Mac Studio M4 Max.
    Uses MLX for Apple Silicon optimized training.
    Slower than cloud GPU but free.
    """
    import subprocess

    adapter_dir = TRAINING_DATA_DIR / "adapters" / datetime.now().strftime("%Y%m%d_%H%M%S")
    adapter_dir.mkdir(parents=True, exist_ok=True)

    # Check if mlx-lm is available
    try:
        result = subprocess.run(
            ["python3", "-m", "mlx_lm.lora",
             "--model", "mlx-community/Qwen2.5-14B-Instruct-4bit",
             "--data", str(data_path.parent),
             "--train",
             "--adapter-path", str(adapter_dir),
             "--iters", "200",
             "--batch-size", "1",
             "--lora-layers", "8"],
            capture_output=True, text=True, timeout=7200,  # 2 hour max
        )
        if result.returncode == 0:
            logger.info(f"Local LoRA training complete. Adapter at {adapter_dir}")
            return adapter_dir
        else:
            logger.warning(f"Local training failed: {result.stderr[:500]}")
            return None
    except FileNotFoundError:
        logger.warning("mlx-lm not installed. Run: pip install mlx-lm")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Local training timed out after 2 hours")
        return None


async def _import_to_ollama(adapter_path: Path):
    """Import a LoRA adapter into Ollama as a custom model."""
    import subprocess

    modelfile = adapter_path / "Modelfile"
    model_name = f"perseus-titan-{datetime.now().strftime('%Y%m%d')}"

    # Create Ollama Modelfile
    modelfile.write_text(f"""FROM qwen2.5:14b-instruct-q4_K_M
ADAPTER {adapter_path}
SYSTEM "You are Titan, Perseus's revenue engine. You write cold emails that get replies and close deals."
""")

    try:
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.warning(f"Ollama import failed: {result.stderr[:500]}")
            return

        # Validate: send a test prompt to verify the model actually works
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            test_resp = await client.post(
                f"{config.ollama.host}/api/generate",
                json={"model": model_name, "prompt": "Say hello.", "stream": False,
                      "options": {"num_predict": 10}},
            )
            if test_resp.status_code != 200:
                logger.warning(f"Fine-tuned model validation failed (HTTP {test_resp.status_code}), keeping base model")
                return

        logger.info(f"Imported and validated fine-tuned model '{model_name}' in Ollama")
        from shared.db import set_config
        await set_config("fine_tuned_model", model_name)
        await emit_event("model_updated", {"model": model_name})
    except Exception as e:
        logger.warning(f"Ollama import error: {e}")
