"""Image generation for Clawdbot.

Local-first via Draw Things (best Apple Silicon image gen, supports SDXL/Flux/Qwen-Image).
Production assets still go through Recraft API per CARL decision 2026-03-30.

Routing logic:
- Iteration drafts (hero exploration, logo iterations) → Draw Things local (free)
- Production hero/logo final → Recraft API (premium)
- Inline icons (small, deterministic) → existing Lucide icons or Recraft
"""

from shared.imagegen.draw_things_client import (
    DrawThingsClient,
    generate_image_local,
    ImageGenResult,
)

__all__ = [
    "DrawThingsClient",
    "generate_image_local",
    "ImageGenResult",
]
