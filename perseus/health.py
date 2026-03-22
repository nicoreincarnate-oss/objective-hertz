"""
Infrastructure health monitor — checks all dependencies and attempts auto-recovery.
Called by Perseus every tick. Results stored in system_config for all agents to read.
"""

import asyncio
import logging
import shutil
import subprocess
import time

import httpx

from shared.config import config

logger = logging.getLogger("perseus.health")

# Cooldown: don't spam recovery attempts
_last_recovery_attempt: dict[str, float] = {}
RECOVERY_COOLDOWN_SECONDS = 300  # 5 minutes between recovery attempts per service


async def check_infrastructure() -> dict:
    """Check all infrastructure dependencies. Returns health status dict."""
    results = await asyncio.gather(
        _check_ollama(),
        _check_mem0(),
        _check_instantly(),
        _check_disk_space(),
        return_exceptions=True,
    )

    ollama_health = results[0] if not isinstance(results[0], Exception) else {"status": "error", "error": str(results[0])}
    mem0_health = results[1] if not isinstance(results[1], Exception) else {"status": "error", "error": str(results[1])}
    instantly_health = results[2] if not isinstance(results[2], Exception) else {"status": "error", "error": str(results[2])}
    disk_health = results[3] if not isinstance(results[3], Exception) else {"status": "error", "error": str(results[3])}

    # Postgres is implicitly healthy if we got here (we're using it)
    health = {
        "postgres": {"status": "ok"},
        "ollama": ollama_health,
        "mem0": mem0_health,
        "instantly": instantly_health,
        "disk": disk_health,
        "checked_at": time.time(),
    }

    # Attempt auto-recovery for down services
    for service, status in health.items():
        if service == "checked_at":
            continue
        if isinstance(status, dict) and status.get("status") != "ok":
            recovered = await _attempt_recovery(service)
            if recovered:
                health[service]["status"] = "recovered"
                health[service]["recovery"] = "auto"

    return health


async def _check_ollama() -> dict:
    """Check if Ollama is responding."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.ollama.host}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                has_primary = any(config.ollama.model.split(":")[0] in n for n in model_names)
                return {
                    "status": "ok",
                    "models_loaded": len(models),
                    "has_primary_model": has_primary,
                }
            return {"status": "degraded", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_mem0() -> dict:
    """Check if Mem0 vector store is responding."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{config.memory.mem0_host}/v1/memories/search/",
                json={"query": "health check", "user_id": "perseus", "limit": 1},
            )
            if resp.status_code in (200, 404):
                return {"status": "ok"}
            return {"status": "degraded", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_instantly() -> dict:
    """Check if Instantly API is reachable."""
    if not config.instantly.api_key:
        return {"status": "not_configured"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.instantly.ai/api/v2/campaigns",
                headers={"Authorization": f"Bearer {config.instantly.api_key}"},
                params={"limit": 1},
            )
            if resp.status_code == 200:
                return {"status": "ok"}
            if resp.status_code == 429:
                return {"status": "rate_limited"}
            return {"status": "degraded", "http_status": resp.status_code}
    except Exception as e:
        return {"status": "down", "error": str(e)[:200]}


async def _check_disk_space() -> dict:
    """Check local disk headroom on the root volume."""
    usage = shutil.disk_usage("/")
    percent_used = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0

    if percent_used >= 90:
        status = "down"
    elif percent_used >= 80:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "percent_used": percent_used,
        "free_gb": round(usage.free / (1024 ** 3), 2),
    }


async def _attempt_recovery(service: str) -> bool:
    """Try to auto-recover a down service. Returns True if recovery attempted."""
    now = time.time()
    last = _last_recovery_attempt.get(service, 0)
    if now - last < RECOVERY_COOLDOWN_SECONDS:
        return False

    _last_recovery_attempt[service] = now
    logger.warning(f"Attempting auto-recovery for {service}")

    try:
        if service == "ollama":
            return await _recover_ollama()
        elif service == "mem0":
            return await _recover_docker_service("mem0")
        else:
            return False
    except Exception as e:
        logger.error(f"Recovery failed for {service}: {e}")
        return False


async def _recover_ollama() -> bool:
    """Try to restart Ollama."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ollama", "serve",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Give it a moment to start
        await asyncio.sleep(3)
        # Check if it's alive now
        result = await _check_ollama()
        if result.get("status") == "ok":
            logger.info("Ollama auto-recovered successfully")
            from shared.db import emit_event
            await emit_event("infra_recovered", {"service": "ollama", "method": "ollama serve"})
            return True
        return False
    except FileNotFoundError:
        logger.error("ollama binary not found — cannot auto-recover")
        return False


async def _recover_docker_service(service_name: str) -> bool:
    """Try to restart a Docker container by name."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "restart", service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            logger.info(f"Docker container '{service_name}' restarted successfully")
            from shared.db import emit_event
            await emit_event("infra_recovered", {"service": service_name, "method": "docker restart"})
            return True
        logger.warning(f"Docker restart failed for {service_name}: {stderr.decode()[:200]}")
        return False
    except FileNotFoundError:
        logger.error("docker binary not found — cannot auto-recover")
        return False
    except asyncio.TimeoutError:
        logger.error(f"Docker restart timed out for {service_name}")
        return False


def is_service_ok(infra_health: dict, service: str) -> bool:
    """Helper for other agents: check if a specific service is usable."""
    if not infra_health:
        return True  # Fail open if health data is missing
    svc = infra_health.get(service, {})
    if not isinstance(svc, dict):
        return True
    return svc.get("status") in ("ok", "recovered", "not_configured", None)
