"""Draw Things HTTP client for Clawdbot iteration drafts.

Draw Things is the best Apple Silicon native image gen app — supports SDXL,
Flux.1 schnell/dev, Qwen-Image, Stable Diffusion 3.5, LTX video. Native Metal.

It exposes a localhost HTTP API when "Allow Remote Connection" is enabled in
its settings (stays local — bind 127.0.0.1).

Routing for Clawdbot:
- Hero exploration: SDXL 25-step → ~5 sec/image
- Logo iteration: Flux.1 schnell 4-step → ~12 sec
- High-quality drafts: Flux.1 dev 25-step → ~50 sec
- Complex scenes with text: Qwen-Image → ~70 sec

Recraft API stays for production-grade output where premium quality is required.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("perseus.imagegen.draw_things")


@dataclass
class ImageGenResult:
    image_bytes: bytes
    image_url: str = ""              # Local file path or data: URL
    width: int = 1024
    height: int = 1024
    model: str = ""
    duration_inference_ms: int = 0
    seed: int = 0
    prompt: str = ""


# Model presets — picked for speed-quality tradeoff per use case
MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "hero_fast": {
        "model": "sdxl-1.0",
        "steps": 25,
        "cfg_scale": 7.0,
        "sampler": "DPM++ 2M Karras",
        "width": 1536,
        "height": 1024,
    },
    "hero_quality": {
        "model": "flux.1-dev",
        "steps": 25,
        "cfg_scale": 3.5,
        "sampler": "Euler",
        "width": 1536,
        "height": 1024,
    },
    "logo_iteration": {
        "model": "flux.1-schnell",
        "steps": 4,
        "cfg_scale": 1.0,
        "sampler": "Euler",
        "width": 1024,
        "height": 1024,
    },
    "complex_scene": {
        "model": "qwen-image",
        "steps": 30,
        "cfg_scale": 5.0,
        "sampler": "DPM++ 2M",
        "width": 1024,
        "height": 1024,
    },
    "icon": {
        "model": "sdxl-1.0",
        "steps": 20,
        "cfg_scale": 8.0,
        "sampler": "Euler a",
        "width": 512,
        "height": 512,
    },
}


class DrawThingsClient:
    def __init__(
        self,
        api_base: str | None = None,
        timeout: float = 120.0,
    ):
        self.api_base = api_base or os.environ.get(
            "DRAW_THINGS_API_BASE", "http://127.0.0.1:7860"
        )
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        *,
        preset: str = "hero_fast",
        negative_prompt: str = "blurry, low quality, watermark, signature, text",
        seed: int | None = None,
        custom_params: dict[str, Any] | None = None,
    ) -> ImageGenResult:
        """Generate one image via Draw Things HTTP API."""
        import time
        import httpx
        import base64

        params = MODEL_PRESETS.get(preset, MODEL_PRESETS["hero_fast"]).copy()
        if custom_params:
            params.update(custom_params)

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            **params,
        }
        if seed is not None:
            payload["seed"] = seed

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.api_base}/sdapi/v1/txt2img",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        if not data.get("images"):
            raise RuntimeError(f"Draw Things returned no images: {data}")

        image_b64 = data["images"][0]
        image_bytes = base64.b64decode(image_b64.split(",", 1)[-1])

        return ImageGenResult(
            image_bytes=image_bytes,
            width=params.get("width", 1024),
            height=params.get("height", 1024),
            model=params.get("model", "unknown"),
            duration_inference_ms=int((time.perf_counter() - t0) * 1000),
            seed=data.get("info", {}).get("seed", 0),
            prompt=prompt,
        )

    async def health_check(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.api_base}/sdapi/v1/options")
                return response.status_code == 200
        except Exception:
            return False


_default_client: DrawThingsClient | None = None


def _get_default_client() -> DrawThingsClient:
    global _default_client
    if _default_client is None:
        _default_client = DrawThingsClient()
    return _default_client


async def generate_image_local(prompt: str, **kwargs: Any) -> ImageGenResult:
    return await _get_default_client().generate(prompt, **kwargs)


__all__ = ["DrawThingsClient", "ImageGenResult", "generate_image_local", "MODEL_PRESETS"]
