"""Operator taste profile -- persistent design preference storage.

Stores extracted visual DNA from reference sources. The aggregated profile
influences all site builds as a soft bias via get_taste_overlay().
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("perseus.clawdbot.taste_profile")

TASTE_DIR = Path(__file__).resolve().parent.parent / "soul" / "taste"
PROFILE_PATH = TASTE_DIR / "profile.yaml"

_EMPTY_PROFILE: dict[str, Any] = {
    "version": 1,
    "updated_at": "",
    "aesthetic_keywords": [],
    "style_lineage": [],
    "mood_atmosphere": [],
    "color_palettes": [],
    "typography": {
        "display_preference": "",
        "body_preference": "",
        "weight_bias": "",
    },
    "layout_patterns": {
        "grid_approach": "",
        "whitespace_level": "",
        "preferred_patterns": [],
    },
    "visual_characteristics": {
        "contrast_level": "",
        "border_radius": "",
    },
    "references": [],
}

_DIRECTION_TRAITS: dict[str, dict[str, Any]] = {
    "minimal-geometric": {
        "mood": ["minimal", "clean", "geometric"],
        "contrast": "low",
        "weight": "light",
        "whitespace": "generous",
        "layout": "grid",
    },
    "bold-editorial": {
        "mood": ["bold", "editorial", "dramatic"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "generous",
        "layout": "editorial",
    },
    "dark-cinematic": {
        "mood": ["dark", "cinematic", "moody"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "moderate",
        "layout": "full-bleed",
    },
    "organic-illustrated": {
        "mood": ["warm", "organic", "natural"],
        "contrast": "medium",
        "weight": "regular",
        "whitespace": "moderate",
        "layout": "asymmetric",
    },
    "playful-animated": {
        "mood": ["playful", "vibrant", "energetic"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "moderate",
        "layout": "dynamic",
    },
    "immersive-3d": {
        "mood": ["immersive", "cinematic", "spatial"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "moderate",
        "layout": "full-bleed",
    },
    "audio-reactive-canvas": {
        "mood": ["rhythmic", "reactive", "sensory"],
        "contrast": "high",
        "weight": "regular",
        "whitespace": "moderate",
        "layout": "single-column",
    },
    "cosmos-dark-curation": {
        "mood": ["immersive", "curated", "dark-canvas", "discovery", "color-coded"],
        "contrast": "high",
        "weight": "regular",
        "whitespace": "moderate",
        "layout": "bento",
    },
    "arena-monastic-grid": {
        "mood": ["monastic", "intentional", "calm", "minimal", "research-library"],
        "contrast": "low",
        "weight": "regular",
        "whitespace": "generous",
        "layout": "grid",
    },
    "yokoo-psychedelic-maximalism": {
        "mood": ["psychedelic", "maximalist", "theatrical", "electric", "collage"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "minimal",
        "layout": "freeform",
    },
    "fukuda-optical-precision": {
        "mood": ["minimal", "witty", "precise", "optical-illusion", "intellectual"],
        "contrast": "high",
        "weight": "bold",
        "whitespace": "generous",
        "layout": "single-column",
    },
}


def load_taste_profile() -> dict[str, Any]:
    """Load taste profile from YAML. Returns empty structure if file missing."""
    if not PROFILE_PATH.exists():
        return {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _EMPTY_PROFILE.items()}
    try:
        data = yaml.safe_load(PROFILE_PATH.read_text()) or {}
        for key, default in _EMPTY_PROFILE.items():
            if key not in data:
                data[key] = default.copy() if isinstance(default, (list, dict)) else default
        return data
    except Exception:
        logger.exception("Failed to load taste profile from %s", PROFILE_PATH)
        return {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in _EMPTY_PROFILE.items()}


def save_taste_profile(profile: dict[str, Any]) -> None:
    """Write taste profile to YAML."""
    TASTE_DIR.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROFILE_PATH.write_text(
        yaml.safe_dump(profile, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )
    logger.info("Taste profile saved to %s", PROFILE_PATH)


def add_reference(profile: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Append a reference and recompute aggregates. Saves to disk."""
    if "references" not in profile:
        profile["references"] = []
    reference.setdefault("added_at", datetime.now(timezone.utc).isoformat())
    profile["references"].append(reference)
    profile = _recompute_aggregates(profile)
    save_taste_profile(profile)
    return profile


def _mode(values: list[str]) -> str:
    """Return most common non-empty value, or empty string."""
    filtered = [v for v in values if v]
    if not filtered:
        return ""
    return Counter(filtered).most_common(1)[0][0]


def _recompute_aggregates(profile: dict[str, Any]) -> dict[str, Any]:
    """Recompute all aggregate fields from references."""
    refs = profile.get("references", [])
    if not refs:
        return profile

    all_mood: list[str] = []
    all_style: list[str] = []
    all_atmosphere: list[str] = []
    all_palettes: list[dict] = []
    display_prefs: list[str] = []
    body_prefs: list[str] = []
    weight_prefs: list[str] = []
    layout_styles: list[str] = []
    whitespace_levels: list[str] = []
    all_patterns: list[str] = []
    contrast_levels: list[str] = []
    border_radii: list[str] = []

    for ref in refs:
        # Data may be at top level or nested under "extracted"
        data = ref.get("extracted", ref)

        all_mood.extend(data.get("mood_keywords", []))
        all_style.extend(data.get("style_lineage", []))
        all_atmosphere.extend(data.get("atmosphere", []))

        colors = data.get("colors")
        if colors:
            all_palettes.append({
                "source": ref.get("source", "unknown"),
                "colors": colors,
                "mood": data.get("color_mood", ""),
            })

        typo_val = data.get("typography", "")
        if isinstance(typo_val, str) and typo_val:
            display_prefs.append(typo_val)
        elif isinstance(typo_val, dict):
            if typo_val.get("display"):
                display_prefs.append(typo_val["display"])
            if typo_val.get("body"):
                body_prefs.append(typo_val["body"])
            if typo_val.get("weight"):
                weight_prefs.append(typo_val["weight"])

        layout_val = data.get("layout_style", "")
        if isinstance(layout_val, str) and layout_val:
            layout_styles.append(layout_val)

        ws_val = data.get("whitespace", "")
        if isinstance(ws_val, str) and ws_val:
            whitespace_levels.append(ws_val)

        contrast_val = data.get("contrast", "")
        if isinstance(contrast_val, str) and contrast_val:
            contrast_levels.append(contrast_val)

        br_val = data.get("border_radius", "")
        if isinstance(br_val, str) and br_val:
            border_radii.append(br_val)

    mood_counter = Counter(all_mood)
    profile["aesthetic_keywords"] = [kw for kw, _ in mood_counter.most_common()]
    profile["style_lineage"] = list(dict.fromkeys(all_style))
    profile["mood_atmosphere"] = list(dict.fromkeys(all_atmosphere))
    profile["color_palettes"] = all_palettes

    profile["typography"] = {
        "display_preference": _mode(display_prefs),
        "body_preference": _mode(body_prefs),
        "weight_bias": _mode(weight_prefs),
    }
    profile["layout_patterns"] = {
        "grid_approach": _mode(layout_styles),
        "whitespace_level": _mode(whitespace_levels),
        "preferred_patterns": list(dict.fromkeys(all_patterns)),
    }
    profile["visual_characteristics"] = {
        "contrast_level": _mode(contrast_levels),
        "border_radius": _mode(border_radii),
    }

    return profile


def get_taste_overlay(profile: dict[str, Any]) -> str:
    """Generate a prompt-ready aesthetic overlay.

    Goes beyond colors — communicates compositional philosophy, visual tension,
    and the specific design traditions the operator channels.
    """
    refs = profile.get("references", [])
    if not refs:
        return ""

    keywords = profile.get("aesthetic_keywords", [])
    style = profile.get("style_lineage", [])
    mood = profile.get("mood_atmosphere", [])
    typo = profile.get("typography", {})
    layout = profile.get("layout_patterns", {})
    visual = profile.get("visual_characteristics", {})

    # Build palette summary from all sources
    palette_lines: list[str] = []
    for p in profile.get("color_palettes", []):
        colors = p.get("colors", [])[:4]
        p_mood = p.get("mood", "")
        if colors:
            palette_lines.append(f"  {p.get('source', '?')}: {', '.join(colors)} ({p_mood})")

    palette_block = "\n".join(palette_lines) if palette_lines else "  No palettes extracted"

    # Synthesize the aesthetic philosophy from all references
    aesthetic_desc = ", ".join(keywords[:8]) if keywords else "contemporary"
    lineage_desc = ", ".join(style[:4]) if style else "modern design"
    mood_desc = ", ".join(mood[:5]) if mood else "professional"
    contrast = visual.get("contrast_level", "medium")
    border = visual.get("border_radius", "subtle")
    display = typo.get("display_preference", "sans-serif")
    grid = layout.get("grid_approach", "standard")
    ws = layout.get("whitespace_level", "moderate")

    return (
        "OPERATOR AESTHETIC IDENTITY (infuse this sensibility — client needs still lead):\n"
        f"\n"
        f"Design DNA: {aesthetic_desc}\n"
        f"Lineage: {lineage_desc}\n"
        f"Atmosphere: {mood_desc}\n"
        f"\n"
        f"Visual system:\n"
        f"- Contrast: {contrast} | Edges: {border} | Grid: {grid} | Whitespace: {ws}\n"
        f"- Typography: {display} display type\n"
        f"- Color palettes from references:\n"
        f"{palette_block}\n"
        f"\n"
        f"Compositional principles:\n"
        f"- Dark canvas with vivid content — backgrounds recede, content pops\n"
        f"- Typography carries identity — type choices are personality, not decoration\n"
        f"- Cultural specificity over bland universalism — every site should feel like it belongs to someone\n"
        f"- Hidden depth rewards attention — hover states, scroll reveals, dual-reading elements\n"
        f"- The tension between restraint and expression — know when to be quiet and when to be loud"
    )


def get_direction_weights(profile: dict[str, Any]) -> dict[str, float]:
    """Score each design direction against the taste profile."""
    refs = profile.get("references", [])
    if not refs:
        return {name: 0.5 for name in _DIRECTION_TRAITS}

    taste_contrast = (profile.get("visual_characteristics") or {}).get("contrast_level", "").lower()
    taste_weight = (profile.get("typography") or {}).get("weight_bias", "").lower()
    taste_whitespace = (profile.get("layout_patterns") or {}).get("whitespace_level", "").lower()
    taste_layout = (profile.get("layout_patterns") or {}).get("grid_approach", "").lower()
    taste_moods = {kw.lower() for kw in profile.get("aesthetic_keywords", [])}

    scores: dict[str, float] = {}
    for name, traits in _DIRECTION_TRAITS.items():
        score = 0.5

        if taste_contrast and taste_contrast == traits.get("contrast", "").lower():
            score += 0.2

        if taste_weight and taste_weight == traits.get("weight", "").lower():
            score += 0.2

        dir_moods = {m.lower() for m in traits.get("mood", [])}
        if taste_moods & dir_moods:
            score += 0.2

        if taste_whitespace and taste_whitespace == traits.get("whitespace", "").lower():
            score += 0.1

        if taste_layout and taste_layout == traits.get("layout", "").lower():
            score += 0.1

        scores[name] = min(score, 1.0)

    return scores
