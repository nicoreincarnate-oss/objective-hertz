# Image Generation Routing Guide — Clawdbot

Clawdbot has THREE image generation paths after Phase 42.5:

| Path | Use case | Cost | Latency |
|---|---|---|---|
| Draw Things (local Apple Silicon) | Iteration drafts, A/B exploration | $0 | 5-70s/image |
| 21st.dev MCP | Component-style images per CARL decision | API quota | varies |
| Recraft API | Production-grade hero/logo finals | $0.03-0.08/image | 5-15s |

## Routing decision tree

```
Need an image for Clawdbot?
│
├─ Is this a draft / exploration / A/B candidate?
│  └─ YES → Draw Things local (free, 5-70s)
│
├─ Is this a production-grade hero, logo, or final asset?
│  └─ YES → Recraft API (premium quality)
│
├─ Is this a structured component (button, card, badge, icon set)?
│  └─ YES → 21st.dev MCP (per CARL decision 2026-03-30)
│
└─ Default → Draw Things, escalate to Recraft if visual scorer rejects
```

## Draw Things presets

Draw Things runs as a native Metal app on the Mac Studio. Enable "Allow Remote
Connection" in its settings → bind 127.0.0.1:7860.

| Preset | Model | Steps | Time | Use for |
|---|---|---|---|---|
| `hero_fast` | SDXL 1.0 | 25 | ~5s | Quick hero exploration, iteration drafts |
| `hero_quality` | Flux.1 dev | 25 | ~50s | High-quality hero options worth showing |
| `logo_iteration` | Flux.1 schnell | 4 | ~12s | Fast logo iteration |
| `complex_scene` | Qwen-Image | 30 | ~70s | Scenes with text, complex composition |
| `icon` | SDXL 1.0 | 20 | ~3s | Small icon set generation |

## Code

### Local draft generation
```python
from shared.imagegen import generate_image_local

result = await generate_image_local(
    prompt="modern dental office hero, warm natural light, friendly staff, 2026 photography style",
    preset="hero_fast",
)
# result.image_bytes — PNG bytes
# result.duration_inference_ms — actual latency
```

### Production asset (existing path)
```python
from clawdbot.asset_generator import generate_asset_via_recraft  # existing per CARL

result = await generate_asset_via_recraft(
    prompt="...",
    style="photography",
    width=1536,
    height=1024,
)
```

### Component fetching (existing path)
```python
from clawdbot.design_sources import resolve_design_sources_with_components  # existing

components = await resolve_design_sources_with_components(
    industry="dentist",
    section_type="hero",
)
```

## Routing logic in Clawdbot Aider loop

```python
async def get_assets_for_section(plan_section, section_type):
    """Choose image path based on section type and plan tier."""
    if plan_section.get("priority") == "draft":
        return await generate_image_local(plan_section["prompt"], preset="hero_fast")

    if section_type in ("hero", "logo") and plan_section.get("priority") == "production":
        return await generate_asset_via_recraft(plan_section["prompt"])

    if section_type == "component":
        return await resolve_design_sources_with_components(
            industry=plan_section["industry"],
            section_type=section_type,
        )

    # Default: try local, escalate if visual scorer rejects
    local = await generate_image_local(plan_section["prompt"], preset="hero_quality")
    if visual_scorer.score(local.image_bytes) < 0.7:
        return await generate_asset_via_recraft(plan_section["prompt"])
    return local
```

## Cost projection

Per Clawdbot site build (5-7 sections, ~3 iterations average):
- All draft path (Draw Things): $0
- Recraft for hero+logo finals: ~$0.10
- 21st.dev component fetches: ~$0.05 (existing budget)
- **Total: ~$0.15/site** vs ~$0.40/site if everything went through Recraft

At 100 sites/month: $15 instead of $40 — $25/month savings on a quality wash
or quality improvement (because we have more iteration cycles for free).

## Setup

```bash
# 1. Install Draw Things from App Store or drawthings.ai
# 2. Open Draw Things, go to Settings → Network
# 3. Enable "Allow Remote Connection"
# 4. Verify localhost binding: lsof -i :7860 should show drawthings binding 127.0.0.1
# 5. Install models from Draw Things UI:
#    - SDXL 1.0
#    - Flux.1 schnell
#    - Flux.1 dev
#    - Qwen-Image
# 6. Test: python -m shared.imagegen.draw_things_client --test
```

## Failure modes

| Failure | Mitigation |
|---|---|
| Draw Things not running | Falls back to Recraft (cost +$0.05/image) |
| Draw Things model not loaded | Returns error, Clawdbot retries with Recraft |
| Visual scorer rejects all attempts | Escalates to human review queue |
| Recraft API rate-limited | Queue and retry with exponential backoff |
