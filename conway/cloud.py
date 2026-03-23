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

from conway.wallet import AgentWallet
from conway.x402_client import X402Client

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
        wallet: AgentWallet | None = None,
        facilitator_url: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("CONWAY_API_KEY", "")
        self._base_url = (base_url or os.environ.get("CONWAY_API_URL", DEFAULT_API_URL)).rstrip("/")
        self._wallet = wallet
        self._x402 = X402Client(
            facilitator_url or os.environ.get("X402_FACILITATOR_URL", ""),
        ) if wallet is not None else None

    @property
    def available(self) -> bool:
        return bool(self._api_key or self._wallet)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Make a Conway Cloud request with optional x402 fallback."""
        url = path if path.startswith("http://") or path.startswith("https://") else f"{self._base_url}{path}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
            )

            if resp.status_code == 402 and self._wallet and self._x402:
                paid = await self._x402.pay_and_request(
                    url,
                    self._wallet,
                    method=method,
                    body=json_body,
                )
                status = int(paid.get("status", 0) or 0)
                if 200 <= status < 300:
                    return paid.get("data", {}) or {}
                raise RuntimeError(
                    paid.get("error") or f"x402 payment request failed with status {status}"
                )

            resp.raise_for_status()
            if not resp.content:
                return {}
            return resp.json()

    async def get_credits(self) -> Decimal:
        """Get current Conway Cloud credit balance."""
        try:
            data = await self._request_json("GET", "/v1/credits", timeout=10.0)
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
            data = await self._request_json(
                "POST",
                "/v1/compute/provision",
                json_body={
                    "gpu_type": gpu_type,
                    "duration_hours": duration_hours,
                    "image": image,
                },
                timeout=30.0,
            )
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
            await self._request_json(
                "POST",
                "/v1/compute/release",
                json_body={"instance_id": instance_id},
                timeout=15.0,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to release compute {instance_id}: {e}")
            return False

    async def list_compute(self) -> list[ComputeInstance]:
        """List all active compute instances."""
        try:
            data = await self._request_json("GET", "/v1/compute", timeout=10.0)
            instances = []
            for item in data.get("instances", []):
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
            data = await self._request_json(
                "POST",
                "/v1/inference",
                json_body={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60.0,
            )
            return data.get("content", "")
        except Exception as e:
            logger.error(f"Conway inference failed: {e}")
            return ""

    async def register_domain(self, domain: str) -> dict[str, Any]:
        """Register a domain via Conway Cloud."""
        try:
            return await self._request_json(
                "POST",
                "/v1/domains/register",
                json_body={"domain": domain},
                timeout=30.0,
            )
        except Exception as e:
            logger.error(f"Domain registration failed: {e}")
            return {"error": str(e)}
