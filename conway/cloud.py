"""
Conway Cloud API client.

Provides Python access to Conway Cloud services:
- Compute provisioning (GPU/CPU instances)
- Frontier model inference (Claude, GPT, Gemini)
- Domain registration
- Credit management

Auth via SIWE (Sign-In With Ethereum) provisioned API key.
Payments via x402 (USDC on Base).
"""

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger("conway.cloud")

DEFAULT_API_URL = "https://api.conway.tech"


@dataclass
class ComputeInstance:
    """A rented compute instance from Conway Cloud."""

    instance_id: str
    provider: str  # "conway"
    gpu_type: str
    price_per_hour: Decimal
    status: str  # "active", "stopped", "terminated"
    ssh_host: str = ""
    ssh_port: int = 22
    metadata: dict[str, Any] = field(default_factory=dict)


class ConwayCloudClient:
    """Python client for Conway Cloud services."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("CONWAY_API_KEY", "")
        self._base_url = (base_url or os.environ.get("CONWAY_API_URL", DEFAULT_API_URL)).rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def get_credits(self) -> Decimal:
        """Get current Conway Cloud credit balance."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/credits",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return Decimal(str(data.get("balance", 0)))
        except Exception as e:
            logger.error(f"Failed to get credits: {e}")
            return Decimal("0")

    async def provision_compute(
        self,
        gpu_type: str = "A100",
        duration_hours: int = 1,
        image: str = "ubuntu:22.04",
    ) -> ComputeInstance | None:
        """Provision a compute instance."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/compute/provision",
                    headers=self._headers(),
                    json={
                        "gpu_type": gpu_type,
                        "duration_hours": duration_hours,
                        "image": image,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return ComputeInstance(
                    instance_id=data["instance_id"],
                    provider="conway",
                    gpu_type=gpu_type,
                    price_per_hour=Decimal(str(data.get("price_per_hour", 0))),
                    status="active",
                    ssh_host=data.get("ssh_host", ""),
                    ssh_port=data.get("ssh_port", 22),
                    metadata=data,
                )
        except Exception as e:
            logger.error(f"Failed to provision compute: {e}")
            return None

    async def release_compute(self, instance_id: str) -> bool:
        """Release a rented compute instance."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/compute/release",
                    headers=self._headers(),
                    json={"instance_id": instance_id},
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Failed to release compute {instance_id}: {e}")
            return False

    async def list_compute(self) -> list[ComputeInstance]:
        """List all active compute instances."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/compute",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                instances = []
                for item in resp.json().get("instances", []):
                    instances.append(
                        ComputeInstance(
                            instance_id=item["instance_id"],
                            provider="conway",
                            gpu_type=item.get("gpu_type", ""),
                            price_per_hour=Decimal(str(item.get("price_per_hour", 0))),
                            status=item.get("status", "unknown"),
                            ssh_host=item.get("ssh_host", ""),
                            ssh_port=item.get("ssh_port", 22),
                        )
                    )
                return instances
        except Exception as e:
            logger.error(f"Failed to list compute: {e}")
            return []

    async def request_inference(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Request inference from a frontier model via Conway Cloud."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/inference",
                    headers=self._headers(),
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("content", "")
        except Exception as e:
            logger.error(f"Conway inference failed: {e}")
            return ""

    async def register_domain(self, domain: str) -> dict[str, Any]:
        """Register a domain via Conway Cloud."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/domains/register",
                    headers=self._headers(),
                    json={"domain": domain},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Domain registration failed: {e}")
            return {"error": str(e)}
