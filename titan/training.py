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
from datetime import datetime
from pathlib import Path

from shared.config import config
from shared.db import fetch_all, fetch_one, fetch_val, execute, emit_event
from shared.llm_client import llm

logger = logging.getLogger("perseus.titan.training")

TRAINING_DATA_DIR = config.root_dir / "training_data"


async def collect_training_example(
    example_type: str,
    input_text: str,
    output_text: str,
    outcome: str = "",
    metadata: dict = None,
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

    await collect_training_example(
        example_type="email_compose",
        input_text=input_text,
        output_text=output_text,
        outcome=outcome,
        metadata={"email_seq_id": email_seq_id, "client_id": email["client_id"]},
    )


async def export_training_data(min_examples: int = 100) -> Path:
    """
    Export training data as JSONL file for LoRA fine-tuning.
    Only exports examples with known outcomes (positive/negative).
    Format: {"instruction": "...", "input": "...", "output": "..."}
    """
    TRAINING_DATA_DIR.mkdir(parents=True, exist_ok=True)

    examples = await fetch_all(
        """SELECT example_type, input_text, output_text, outcome
           FROM training_data
           WHERE outcome IN ('positive', 'negative')
           ORDER BY created_at DESC""",
    )

    if len(examples) < min_examples:
        logger.info(f"Only {len(examples)} labeled examples (need {min_examples}). Skipping export.")
        return None

    # Separate positive (good emails) from negative (bad emails)
    positive = [e for e in examples if e["outcome"] == "positive"]
    negative = [e for e in examples if e["outcome"] == "negative"]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = TRAINING_DATA_DIR / f"training_{timestamp}.jsonl"

    with open(output_path, "w") as f:
        # Positive examples: train to replicate
        for ex in positive:
            record = {
                "instruction": f"Write a cold outreach email that gets replies. Type: {ex['example_type']}. This email resulted in a positive outcome.",
                "input": ex["input_text"],
                "output": ex["output_text"],
            }
            f.write(json.dumps(record) + "\n")

        # Negative examples: train to avoid (as negative examples with instruction)
        for ex in negative[:len(positive)]:  # Balance positive/negative
            record = {
                "instruction": f"Write a cold outreach email that gets replies. Type: {ex['example_type']}. Avoid patterns like this previous attempt that did NOT work.",
                "input": ex["input_text"],
                "output": f"[NEGATIVE EXAMPLE - DO NOT REPLICATE] {ex['output_text']}",
            }
            f.write(json.dumps(record) + "\n")

    total = len(positive) + min(len(negative), len(positive))
    logger.info(f"Exported {total} training examples to {output_path}")
    return output_path


async def should_train() -> bool:
    """Check if we have enough data and budget to justify a training run."""
    # Check example count
    count = await fetch_val(
        "SELECT COUNT(*) FROM training_data WHERE outcome IN ('positive', 'negative')"
    ) or 0

    if count < 500:
        logger.info(f"Only {count} labeled examples. Need 500 for training.")
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
    from shared.db import get_config
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
    data_path = await export_training_data(min_examples=500)
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

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
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
            learning_rate=2e-4,
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


async def _train_on_cloud_gpu(data_path: Path) -> Path:
    """
    Upload data and run LoRA training on Vast.ai.
    Full lifecycle: search → rent → upload → train → download → destroy.
    Returns path to the downloaded adapter weights.
    """
    import asyncio
    import httpx
    import os
    import tempfile

    vast_key = os.getenv("VAST_AI_API_KEY", "")
    if not vast_key:
        logger.warning("VAST_AI_API_KEY not set. Cannot run cloud training.")
        return await _train_local(data_path)

    api_base = "https://cloud.vast.ai/api/v0"
    headers = {"Authorization": f"Bearer {vast_key}"}
    instance_id = None

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Step 1: Find cheapest GPU
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

            # Budget check: cap at $100/run, expect ~2 hours
            estimated_cost = cost_per_hour * 3  # buffer for setup + training
            if estimated_cost > 100:
                logger.warning(f"Estimated cost ${estimated_cost:.2f} exceeds $100 cap")
                return await _train_local(data_path)

            # Record estimated cost
            await execute(
                """INSERT INTO budget_tracking (month, category, amount, description)
                   VALUES (DATE_TRUNC('month', CURRENT_DATE), 'cloud_gpu', %s,
                           'LoRA training run (estimated)')""",
                (estimated_cost,),
            )

            # Step 2: Create instance
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

            logger.info(f"Created Vast.ai instance {instance_id}")
            await emit_event("cloud_gpu_rented", {
                "instance_id": instance_id,
                "gpu": offer.get("gpu_name"),
                "cost_per_hour": cost_per_hour,
            })

            # Step 3: Wait for instance to be running
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

            # Get SSH connection info
            instance_info = status_resp.json()
            ssh_host = instance_info.get("ssh_host", "")
            ssh_port = instance_info.get("ssh_port", 22)

            if not ssh_host:
                logger.error("No SSH host returned for instance")
                raise RuntimeError("Missing SSH connection info")

            # Step 4: Upload training data + script via SCP
            import subprocess

            train_script_path = data_path.parent / "train_remote.py"
            train_script_path.write_text(_generate_training_script())

            ssh_opts = f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {ssh_port}"

            # Upload training data
            scp_data = subprocess.run(
                f"scp {ssh_opts} {data_path} root@{ssh_host}:/workspace/training_data.jsonl",
                shell=True, capture_output=True, text=True, timeout=120,
            )
            if scp_data.returncode != 0:
                logger.error(f"SCP data upload failed: {scp_data.stderr[:300]}")
                raise RuntimeError("Failed to upload training data")

            # Upload training script
            scp_script = subprocess.run(
                f"scp {ssh_opts} {train_script_path} root@{ssh_host}:/workspace/train.py",
                shell=True, capture_output=True, text=True, timeout=60,
            )
            if scp_script.returncode != 0:
                raise RuntimeError("Failed to upload training script")

            logger.info("Uploaded training data and script")

            # Step 5: Run training
            ssh_cmd = (
                f"ssh {ssh_opts} root@{ssh_host} "
                f"'cd /workspace && nohup python3 train.py > train.log 2>&1 &'"
            )
            subprocess.run(ssh_cmd, shell=True, capture_output=True, timeout=30)
            logger.info("Training started on remote GPU")

            # Step 6: Poll for completion (up to 3 hours)
            for attempt in range(108):  # 3 hours at 100s intervals
                await asyncio.sleep(100)
                check = subprocess.run(
                    f"ssh {ssh_opts} root@{ssh_host} 'cat /workspace/TRAINING_COMPLETE 2>/dev/null'",
                    shell=True, capture_output=True, text=True, timeout=30,
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
                    f"ssh {ssh_opts} root@{ssh_host} 'tail -5 /workspace/train.log 2>/dev/null'",
                    shell=True, capture_output=True, text=True, timeout=30,
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
                f"scp -r {ssh_opts} root@{ssh_host}:/workspace/adapter_output/* {adapter_dir}/",
                shell=True, capture_output=True, text=True, timeout=300,
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
                async with httpx.AsyncClient(timeout=30.0) as client:
                    destroy_resp = await client.delete(
                        f"{api_base}/instances/{instance_id}/",
                        headers=headers,
                    )
                    if destroy_resp.status_code < 300:
                        logger.info(f"Destroyed Vast.ai instance {instance_id}")
                    else:
                        logger.warning(f"Failed to destroy instance {instance_id}: {destroy_resp.status_code}")
                        await emit_event("cloud_gpu_cleanup_failed", {"instance_id": instance_id})
            except Exception as cleanup_err:
                logger.error(f"CRITICAL: Failed to destroy instance {instance_id}: {cleanup_err}")
                await emit_event("cloud_gpu_cleanup_failed", {
                    "instance_id": instance_id,
                    "error": str(cleanup_err),
                })


async def _train_local(data_path: Path) -> Path:
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
        if result.returncode == 0:
            logger.info(f"Imported fine-tuned model as '{model_name}' in Ollama")
            # Update config to use the new model
            from shared.db import set_config
            await set_config("fine_tuned_model", model_name)
            await emit_event("model_updated", {"model": model_name})
        else:
            logger.warning(f"Ollama import failed: {result.stderr[:500]}")
    except Exception as e:
        logger.warning(f"Ollama import error: {e}")
