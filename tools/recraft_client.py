"""Recraft AI client — production-grade image and vector generation.

Used by ClawdBot and Titan to generate:
- Logos for client websites
- Branded visuals for demo sites
- Social media graphics (upsell)
- Email header images
- Portfolio/gallery content for client sites

API: https://www.recraft.ai/docs/api-reference
Cost: ~$0.01/image. At 100 images/month = $1.
"""

import logging
import os
from typing import Any

import httpx

from tools.runtime_honesty import env_is_configured, truth_payload

logger = logging.getLogger("perseus.tools.recraft")

RECRAFT_API_BASE = "https://external.api.recraft.ai/v1"


def get_recraft_status() -> dict[str, Any]:
    """Check if Recraft API is configured and reachable."""
    if not env_is_configured("RECRAFT_API_KEY"):
        return truth_payload(
            "blocked", "recraft_api", False,
            summary="RECRAFT_API_KEY not configured. Image generation unavailable.",
            provider="recraft",
        )
    return truth_payload(
        "live", "recraft_api", True,
        summary="Recraft V4 API is configured and ready for image generation.",
        provider="recraft",
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('RECRAFT_API_KEY', '')}",
        "Content-Type": "application/json",
    }


def _check_recraft_available() -> dict[str, Any] | None:
    """Return error payload if Recraft is unavailable, else None."""
    status = get_recraft_status()
    if not status.get("available"):
        return status
    return None


async def generate_image(
    prompt: str,
    *,
    style: str = "realistic_image",
    width: int = 1024,
    height: int = 1024,
    model: str = "recraftv3",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Generate an image from a text prompt.

    Args:
        prompt: What to generate
        style: "realistic_image", "digital_illustration", "vector_illustration", "icon"
        width/height: Output dimensions (must be multiples of 64, min 512)
        model: "recraftv3" or "recraft20b"

    Returns:
        {"url": "...", "prompt": "...", "style": "..."} or error dict
    """
    unavailable = _check_recraft_available()
    if unavailable:
        return unavailable

    body = {
        "prompt": prompt,
        "style": style,
        "size": f"{width}x{height}",
        "model": model,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{RECRAFT_API_BASE}/images/generations",
                json=body,
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            images = data.get("data", [])
            if not images:
                return {"url": "", "error": "No images returned", "prompt": prompt}

            url = images[0].get("url", "")
            return {
                "url": url,
                "prompt": prompt,
                "style": style,
                "model": model,
            }

    except Exception as e:
        logger.warning(f"Recraft image generation failed: {e}")
        return {"url": "", "error": str(e), "prompt": prompt}


async def generate_logo(
    business_name: str,
    industry: str = "",
    style: str = "vector_illustration",
) -> dict[str, Any]:
    """Generate a logo for a client website."""
    prompt = f"Professional minimalist logo for '{business_name}'"
    if industry:
        prompt += f", a {industry} business"
    prompt += ". Clean, modern, scalable. White background."

    return await generate_image(
        prompt,
        style=style,
        width=1024,
        height=1024,
    )


async def generate_hero_image(
    business_name: str,
    industry: str = "",
    city: str = "",
) -> dict[str, Any]:
    """Generate a hero section image for a client website."""
    location = f" in {city}" if city else ""
    prompt = (
        f"Professional hero image for {business_name}, a {industry or 'local'} business{location}. "
        f"Modern, inviting, high quality. Suitable for a website hero section."
    )

    return await generate_image(
        prompt,
        style="realistic_image",
        width=1536,
        height=1024,
    )


async def generate_social_graphic(
    business_name: str,
    text: str,
    style: str = "digital_illustration",
) -> dict[str, Any]:
    """Generate a social media graphic with text overlay."""
    prompt = (
        f"Social media graphic for {business_name}. "
        f"Text: '{text}'. "
        f"Professional, eye-catching, suitable for Instagram or Facebook post."
    )

    return await generate_image(
        prompt,
        style=style,
        width=1024,
        height=1024,
    )


async def remove_background(image_url: str, timeout: float = 30.0) -> dict[str, Any]:
    """Remove background from an image. $0.01 per use."""
    unavailable = _check_recraft_available()
    if unavailable:
        return unavailable

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{RECRAFT_API_BASE}/images/removeBackground",
                json={"image": image_url},
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return {"url": data.get("image", {}).get("url", ""), "original": image_url}

    except Exception as e:
        logger.warning(f"Recraft background removal failed: {e}")
        return {"url": "", "error": str(e), "original": image_url}
