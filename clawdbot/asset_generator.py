"""Asset generation pipeline for ClawdBot site builds.

Generates hero images, SVG icons, video backgrounds, and generative
canvas sketches. Uses Recraft V4 for SVG, Flux for images, Kling AI
for video, and p5.js for generative backgrounds.
"""

from __future__ import annotations

import logging
import os
import textwrap
from typing import Any

import httpx

from clawdbot.design_tokens import DesignTokens

logger = logging.getLogger("perseus.clawdbot.asset_generator")

_DEFAULT_TIMEOUT = 30.0


async def generate_hero_assets(
    business_context: dict[str, Any],
    design_tokens: DesignTokens,
    tier: str = "demo",
) -> dict[str, str]:
    """Generate hero assets based on build tier.

    Demo tier: Recraft SVG icon + CSS gradient background (free/cheap).
    Premium tier: Recraft SVG + Flux hero image + optional Kling video.

    Args:
        business_context: Dict with business_name, industry, etc.
        design_tokens: DesignTokens for the current build.
        tier: 'demo' or 'premium'.

    Returns:
        Dict with keys like 'icon_svg', 'hero_image', 'video_url',
        'generative_bg'. Values are content strings or URLs.
    """
    business_name = business_context.get("business_name", "Business")
    industry = business_context.get("industry", "services")
    result: dict[str, str] = {}

    # Always generate an SVG icon
    icon_prompt = f"Minimal geometric logo mark for {business_name}, a {industry} business. Simple, professional, single color."
    try:
        result["icon_svg"] = await generate_svg_icon(icon_prompt)
    except Exception:
        logger.warning("SVG icon generation failed", exc_info=True)
        result["icon_svg"] = ""

    if tier == "demo":
        # Demo: CSS gradient background (free)
        result["hero_bg_css"] = (
            f"background: linear-gradient(135deg, {design_tokens.primary}, "
            f"{design_tokens.accent});"
        )
        # Generative p5.js background as optional enhancement
        try:
            result["generative_bg"] = generate_generative_background(
                design_tokens, style="particles"
            )
        except Exception:
            logger.debug("Generative background generation failed", exc_info=True)
            result["generative_bg"] = ""

    elif tier == "premium":
        # Premium: Flux hero image
        hero_prompt = (
            f"Professional editorial photograph for {business_name}, "
            f"a {industry} business. High quality, natural lighting, "
            f"modern aesthetic."
        )
        try:
            image_bytes = await generate_hero_image(hero_prompt, style="editorial")
            if image_bytes:
                result["hero_image_bytes_len"] = str(len(image_bytes))
        except Exception:
            logger.warning("Hero image generation failed", exc_info=True)

        # Optional: Kling AI video background
        if design_tokens.direction_name in (
            "dark-cinematic",
            "immersive-3d",
            "cosmos-dark-curation",
        ):
            video_prompt = (
                f"Ambient abstract motion background, dark theme, "
                f"subtle {design_tokens.direction_name} aesthetic, "
                f"seamless loop, 4 seconds."
            )
            try:
                video_url = await generate_video_background(
                    video_prompt, style=design_tokens.direction_name
                )
                result["video_url"] = video_url
            except Exception:
                logger.warning("Video background generation failed", exc_info=True)

    return result


async def generate_svg_icon(prompt: str) -> str:
    """Generate an SVG icon via Recraft V4 Vector API.

    Only model that produces real editable SVG paths.
    Requires RECRAFT_API_KEY environment variable.

    Returns:
        SVG string content.
    """
    api_key = os.environ.get("RECRAFT_API_KEY", "")
    if not api_key:
        logger.warning("RECRAFT_API_KEY not set, skipping SVG generation")
        return ""

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.post(
            "https://external.api.recraft.ai/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "style": "vector_illustration",
                "model": "recraftv3",
                "response_format": "url",
                "size": "1024x1024",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    images = data.get("data", [])
    if not images:
        return ""

    # Download the SVG content from the URL
    svg_url = images[0].get("url", "")
    if not svg_url:
        return ""

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        svg_resp = await client.get(svg_url)
        svg_resp.raise_for_status()
        return svg_resp.text


async def generate_hero_image(prompt: str, style: str = "editorial") -> bytes:
    """Generate a hero image via an image generation API.

    Currently a placeholder that returns empty bytes when no API is configured.
    Can be wired to Flux 2 Pro via fal.ai ($0.055/img) or similar.

    Returns:
        Image bytes (PNG/JPEG).
    """
    # Phase 3 Wave 5: Draw Things opt-in local fallback (dead until operator
    # installs Draw Things at 127.0.0.1:7860 AND sets ENABLE_DRAW_THINGS=true).
    # Recraft/fal.ai remain the production default per CARL decision 2026-03-30.
    if os.environ.get("ENABLE_DRAW_THINGS", "false").lower() == "true":
        try:
            from shared.imagegen.draw_things_client import DrawThingsClient

            dt = DrawThingsClient()
            if await dt.health_check():
                # Map style hint to a Draw Things preset; "editorial" → hero_quality
                preset = "hero_quality" if style == "editorial" else "hero_fast"
                try:
                    result = await dt.generate(prompt=prompt, preset=preset)
                except Exception as gen_exc:
                    logger.warning(
                        "Draw Things generate() failed, falling through to Recraft/fal.ai: %s",
                        gen_exc,
                    )
                else:
                    if result.image_bytes:
                        logger.info(
                            "asset_generator: served hero via Draw Things local fallback (model=%s)",
                            result.model,
                        )
                        return result.image_bytes
                    logger.warning(
                        "Draw Things returned empty image_bytes, falling through to Recraft/fal.ai"
                    )
            else:
                logger.debug(
                    "Draw Things health_check failed (service unreachable) — falling through"
                )
        except ImportError:
            logger.debug(
                "Draw Things client import failed — Phase 3 dead path (expected)"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Draw Things branch errored, falling through to Recraft/fal.ai: %s", exc
            )

    # Existing fal.ai/Flux production path (unchanged)
    api_key = os.environ.get("FAL_KEY", "")
    if not api_key:
        logger.info("FAL_KEY not set, hero image generation unavailable")
        return b""

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://fal.run/fal-ai/flux-pro/v1.1",
            headers={
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "image_size": "landscape_16_9",
                "num_images": 1,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    images = data.get("images", [])
    if not images:
        return b""

    image_url = images[0].get("url", "")
    if not image_url:
        return b""

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        img_resp = await client.get(image_url)
        img_resp.raise_for_status()
        return img_resp.content


async def generate_video_background(prompt: str, style: str = "ambient") -> str:
    """Generate a short video loop via Kling AI 3.0.

    For premium tier immersive heroes. Returns video URL.
    Requires KLING_ACCESS_KEY and KLING_SECRET_KEY environment variables.
    """
    access_key = os.environ.get("KLING_ACCESS_KEY", "")
    secret_key = os.environ.get("KLING_SECRET_KEY", "")
    if not access_key or not secret_key:
        logger.info("Kling AI keys not set, video generation unavailable")
        return ""

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.klingai.com/v1/videos/text2video",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_key}",
            },
            json={
                "prompt": prompt,
                "negative_prompt": "text, watermark, low quality",
                "cfg_scale": 0.5,
                "mode": "std",
                "duration": "5",
                "aspect_ratio": "16:9",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Kling returns a task ID; in production we'd poll for completion
    task_id = data.get("data", {}).get("task_id", "")
    if task_id:
        logger.info("Kling video task created: %s (poll for result)", task_id)

    return task_id


def generate_generative_background(
    tokens: DesignTokens,
    style: str = "particles",
) -> str:
    """Generate a p5.js sketch for generative canvas backgrounds.

    Returns JavaScript code to embed in a <script> tag.
    """
    if style == "particles":
        return _p5_particle_sketch(tokens)
    if style == "gradient_mesh":
        return _p5_gradient_mesh(tokens)
    return _p5_particle_sketch(tokens)


def _p5_particle_sketch(tokens: DesignTokens) -> str:
    """Generate a particle system p5.js sketch using design token colors."""
    return textwrap.dedent(f"""\
        new p5(function(p) {{
          var particles = [];
          p.setup = function() {{
            var c = p.createCanvas(window.innerWidth, window.innerHeight);
            c.style('position', 'fixed');
            c.style('top', '0');
            c.style('left', '0');
            c.style('z-index', '-1');
            c.style('pointer-events', 'none');
            for (var i = 0; i < 40; i++) {{
              particles.push({{
                x: p.random(p.width), y: p.random(p.height),
                vx: p.random(-0.2, 0.2), vy: p.random(-0.2, 0.2),
                size: p.random(2, 6)
              }});
            }}
          }};
          p.draw = function() {{
            p.clear();
            for (var i = 0; i < particles.length; i++) {{
              var pt = particles[i];
              pt.x += pt.vx; pt.y += pt.vy;
              if (pt.x < 0 || pt.x > p.width) pt.vx *= -1;
              if (pt.y < 0 || pt.y > p.height) pt.vy *= -1;
              p.noStroke();
              p.fill(p.red(p.color('{tokens.accent}')), p.green(p.color('{tokens.accent}')), p.blue(p.color('{tokens.accent}')), 60);
              p.ellipse(pt.x, pt.y, pt.size);
              for (var j = i + 1; j < particles.length; j++) {{
                var d = p.dist(pt.x, pt.y, particles[j].x, particles[j].y);
                if (d < 120) {{
                  p.stroke(p.red(p.color('{tokens.accent}')), p.green(p.color('{tokens.accent}')), p.blue(p.color('{tokens.accent}')), p.map(d, 0, 120, 40, 0));
                  p.line(pt.x, pt.y, particles[j].x, particles[j].y);
                }}
              }}
            }}
          }};
          p.windowResized = function() {{ p.resizeCanvas(window.innerWidth, window.innerHeight); }};
        }});""")


def _p5_gradient_mesh(tokens: DesignTokens) -> str:
    """Generate an animated gradient mesh sketch."""
    return textwrap.dedent(f"""\
        new p5(function(p) {{
          var t = 0;
          p.setup = function() {{
            var c = p.createCanvas(window.innerWidth, window.innerHeight);
            c.style('position', 'fixed');
            c.style('top', '0');
            c.style('left', '0');
            c.style('z-index', '-1');
            c.style('pointer-events', 'none');
            p.noStroke();
          }};
          p.draw = function() {{
            p.clear();
            t += 0.005;
            for (var x = 0; x < p.width; x += 40) {{
              for (var y = 0; y < p.height; y += 40) {{
                var n = p.noise(x * 0.003 + t, y * 0.003 + t);
                var c1 = p.color('{tokens.primary}');
                var c2 = p.color('{tokens.accent}');
                var col = p.lerpColor(c1, c2, n);
                p.fill(p.red(col), p.green(col), p.blue(col), 30);
                p.ellipse(x + p.sin(t + x * 0.01) * 10, y + p.cos(t + y * 0.01) * 10, 50, 50);
              }}
            }}
          }};
          p.windowResized = function() {{ p.resizeCanvas(window.innerWidth, window.innerHeight); }};
        }});""")
