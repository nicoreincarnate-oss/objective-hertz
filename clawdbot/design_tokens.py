"""Design token system for ClawdBot site generation.

Converts design directions and taste profiles into structured tokens:
colors, typography, spacing, motion, layout. Generates CSS custom
properties and HTML <head> blocks for CDN-compatible builds.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# CDN Stack — pinned versions for every tool ClawdBot can use
# ---------------------------------------------------------------------------

CDN_STACK: dict[str, str] = {
    # Core (always loaded)
    "tailwind": "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4",
    "alpine": "https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js",
    # Animation (loaded per-direction)
    "gsap": "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
    "scrolltrigger": "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
    "motion": "https://cdn.jsdelivr.net/npm/motion@12/+esm",
    "lenis": "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
    "lottie": "https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/build/player/lottie.min.js",
    # Interactive animations
    "rive": "https://cdn.jsdelivr.net/npm/@rive-app/canvas@2/+esm",
    # 3D (premium tier only)
    "threejs": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js",
    "spline": "https://cdn.jsdelivr.net/npm/@splinetool/runtime@1/+esm",
    # Generative art
    "p5": "https://cdn.jsdelivr.net/npm/p5@2/lib/p5.min.js",
    # Audio reactive (rare, opt-in)
    "tonejs": "https://cdn.jsdelivr.net/npm/tone@15.0.4/build/Tone.js",
    # Legacy (kept for compatibility)
    "pixijs": "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
}

# Direction -> required CDN deps mapping
DIRECTION_CDN_MAP: dict[str, list[str]] = {
    "minimal-geometric": ["gsap", "scrolltrigger", "lenis"],
    "bold-editorial": ["gsap", "scrolltrigger", "lenis"],
    "dark-cinematic": ["gsap", "scrolltrigger", "lenis", "motion"],
    "organic-illustrated": ["gsap", "scrolltrigger"],
    "playful-animated": ["gsap", "scrolltrigger", "motion", "rive"],
    "immersive-3d": ["threejs", "gsap", "scrolltrigger", "lenis"],
    "audio-reactive-canvas": ["p5", "tonejs", "gsap"],
    "cosmos-dark-curation": ["gsap", "scrolltrigger", "motion", "rive"],
    "arena-monastic-grid": ["gsap", "scrolltrigger"],
    "yokoo-psychedelic-maximalism": ["gsap", "scrolltrigger", "motion", "p5"],
    "fukuda-optical-precision": ["gsap", "scrolltrigger"],
}

# Google Fonts URL template
_GOOGLE_FONTS_BASE = "https://fonts.googleapis.com/css2"

# ---------------------------------------------------------------------------
# Font URL mappings — Google Fonts import URLs for each font
# ---------------------------------------------------------------------------

_FONT_URLS: dict[str, str] = {
    "Satoshi": "https://api.fontshare.com/v2/css?f[]=satoshi@400;500;700&display=swap",
    "IBM Plex Sans": f"{_GOOGLE_FONTS_BASE}?family=IBM+Plex+Sans:wght@400;500;700&display=swap",
    "Instrument Serif": f"{_GOOGLE_FONTS_BASE}?family=Instrument+Serif:ital@0;1&display=swap",
    "Source Serif Pro": f"{_GOOGLE_FONTS_BASE}?family=Source+Serif+4:wght@400;600&display=swap",
    "Clash Display": "https://api.fontshare.com/v2/css?f[]=clash-display@400;500;600;700&display=swap",
    "Outfit": f"{_GOOGLE_FONTS_BASE}?family=Outfit:wght@300;400;600;700&display=swap",
    "Fraunces": f"{_GOOGLE_FONTS_BASE}?family=Fraunces:opsz,wght@9..144,400;9..144,700&display=swap",
    "Work Sans": f"{_GOOGLE_FONTS_BASE}?family=Work+Sans:wght@400;500;600&display=swap",
    "Cabinet Grotesk": "https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@400;500;700;800&display=swap",
    "Plus Jakarta Sans": f"{_GOOGLE_FONTS_BASE}?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap",
    "Space Grotesk": f"{_GOOGLE_FONTS_BASE}?family=Space+Grotesk:wght@400;500;700&display=swap",
    "JetBrains Mono": f"{_GOOGLE_FONTS_BASE}?family=JetBrains+Mono:wght@400;500&display=swap",
    "Inter": f"{_GOOGLE_FONTS_BASE}?family=Inter:wght@400;500;600;700&display=swap",
    "DM Sans": f"{_GOOGLE_FONTS_BASE}?family=DM+Sans:wght@400;500;700&display=swap",
    "DM Serif Display": f"{_GOOGLE_FONTS_BASE}?family=DM+Serif+Display&display=swap",
    "Playfair Display": f"{_GOOGLE_FONTS_BASE}?family=Playfair+Display:wght@400;700;900&display=swap",
    "Syne": f"{_GOOGLE_FONTS_BASE}?family=Syne:wght@400;600;700;800&display=swap",
    "Space Mono": f"{_GOOGLE_FONTS_BASE}?family=Space+Mono:wght@400;700&display=swap",
    "Newsreader": f"{_GOOGLE_FONTS_BASE}?family=Newsreader:opsz,wght@6..72,400;6..72,600&display=swap",
    "Anybody": f"{_GOOGLE_FONTS_BASE}?family=Anybody:wght@400;700;900&display=swap",
}


def _font_url(name: str) -> str:
    """Return import URL for a font name, with a sensible fallback."""
    if name in _FONT_URLS:
        return _FONT_URLS[name]
    # Try Google Fonts with the raw name
    slug = name.replace(" ", "+")
    return f"{_GOOGLE_FONTS_BASE}?family={slug}:wght@400;500;700&display=swap"


# ---------------------------------------------------------------------------
# DesignTokens dataclass
# ---------------------------------------------------------------------------


@dataclass
class DesignTokens:
    """Structured design constraints for a site build."""

    # Colors (hex)
    primary: str = "#1a1a1a"
    secondary: str = "#f5f5f5"
    accent: str = "#ff6b6b"
    background: str = "#ffffff"
    surface: str = "#f8f8f8"
    text_primary: str = "#111111"
    text_secondary: str = "#555555"
    text_muted: str = "#999999"

    # Typography
    font_display: str = "Inter"
    font_display_url: str = ""
    font_body: str = "Inter"
    font_body_url: str = ""
    scale_ratio: float = 1.25
    base_size: str = "16px"
    heading_weight: str = "700"
    heading_line_height: str = "1.1"
    body_line_height: str = "1.6"
    letter_spacing_heading: str = "-0.02em"

    # Spacing
    section_padding_y: str = "6rem"
    container_max_width: str = "1280px"
    container_padding_x: str = "1.5rem"
    card_gap: str = "2rem"
    element_gap: str = "1rem"

    # Visual
    border_radius_sm: str = "0.375rem"
    border_radius_md: str = "0.75rem"
    border_radius_lg: str = "1rem"
    shadow: str = "sm"

    # Motion
    animation_enabled: bool = True
    transition_duration: str = "300ms"
    entrance_style: str = "fade-up"

    # Layout
    grid_columns: int = 12

    # Direction metadata
    direction_name: str = ""
    cdn_deps: list[str] = field(default_factory=list)
    runtime_hints: list[str] = field(default_factory=list)
    fallback_rules: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pre-defined token mappings for all 11 directions
# ---------------------------------------------------------------------------

_DIRECTION_TOKENS: dict[str, dict[str, Any]] = {
    "minimal-geometric": {
        "primary": "#1a1a1a",
        "secondary": "#f5f5f5",
        "accent": "#ff6b5b",
        "background": "#ffffff",
        "surface": "#fafafa",
        "text_primary": "#111111",
        "text_secondary": "#444444",
        "text_muted": "#999999",
        "font_display": "Satoshi",
        "font_body": "IBM Plex Sans",
        "scale_ratio": 1.333,
        "heading_weight": "700",
        "heading_line_height": "1.05",
        "letter_spacing_heading": "-0.03em",
        "border_radius_sm": "0",
        "border_radius_md": "0",
        "border_radius_lg": "0",
        "shadow": "none",
        "entrance_style": "fade-in",
        "transition_duration": "400ms",
        "section_padding_y": "8rem",
        "container_max_width": "1200px",
        "runtime_hints": ["Subtle fade-ins only", "Maximum whitespace", "Mathematical precision"],
        "fallback_rules": ["No animation fallback needed — already minimal"],
    },
    "bold-editorial": {
        "primary": "#1b2a4a",
        "secondary": "#faf3e8",
        "accent": "#c9a84c",
        "background": "#faf3e8",
        "surface": "#ffffff",
        "text_primary": "#1b2a4a",
        "text_secondary": "#3d4f6f",
        "text_muted": "#8a8a7a",
        "font_display": "Instrument Serif",
        "font_body": "Source Serif Pro",
        "scale_ratio": 1.414,
        "heading_weight": "400",
        "heading_line_height": "1.0",
        "letter_spacing_heading": "-0.02em",
        "border_radius_sm": "0.25rem",
        "border_radius_md": "0.5rem",
        "border_radius_lg": "0.75rem",
        "shadow": "sm",
        "entrance_style": "fade-up",
        "transition_duration": "500ms",
        "section_padding_y": "6rem",
        "container_max_width": "1320px",
        "runtime_hints": ["Text reveals on scroll", "Parallax on images", "Multi-column editorial grid"],
        "fallback_rules": ["Static layout with pull-quotes visible"],
    },
    "dark-cinematic": {
        "primary": "#0a0a0a",
        "secondary": "#c0c0c0",
        "accent": "#3b82f6",
        "background": "#000000",
        "surface": "#111111",
        "text_primary": "#f0f0f0",
        "text_secondary": "#a0a0a0",
        "text_muted": "#666666",
        "font_display": "Clash Display",
        "font_body": "Outfit",
        "scale_ratio": 1.333,
        "heading_weight": "600",
        "heading_line_height": "1.05",
        "letter_spacing_heading": "-0.02em",
        "border_radius_sm": "0.5rem",
        "border_radius_md": "1rem",
        "border_radius_lg": "1.5rem",
        "shadow": "lg",
        "entrance_style": "fade-up",
        "transition_duration": "400ms",
        "section_padding_y": "6rem",
        "container_max_width": "1400px",
        "runtime_hints": ["Glow pulses", "Particle effects", "Cursor glow effects", "Floating cards with glow border"],
        "fallback_rules": ["Replace glow with solid border", "Hide particle canvas"],
    },
    "organic-illustrated": {
        "primary": "#2d5a3d",
        "secondary": "#f5efe6",
        "accent": "#c4623a",
        "background": "#f5efe6",
        "surface": "#ffffff",
        "text_primary": "#2a2a2a",
        "text_secondary": "#5a5a4a",
        "text_muted": "#8a8a7a",
        "font_display": "Fraunces",
        "font_body": "Work Sans",
        "scale_ratio": 1.25,
        "heading_weight": "700",
        "heading_line_height": "1.15",
        "letter_spacing_heading": "normal",
        "border_radius_sm": "0.75rem",
        "border_radius_md": "1.25rem",
        "border_radius_lg": "2rem",
        "shadow": "md",
        "entrance_style": "fade-up",
        "transition_duration": "500ms",
        "section_padding_y": "5rem",
        "container_max_width": "1200px",
        "runtime_hints": ["Gentle fades", "SVG path drawing animations", "Wave section dividers"],
        "fallback_rules": ["Show static SVG illustrations", "Hide wave dividers on no-JS"],
    },
    "playful-animated": {
        "primary": "#4f46e5",
        "secondary": "#ffffff",
        "accent": "#ec4899",
        "background": "#ffffff",
        "surface": "#f5f3ff",
        "text_primary": "#1e1b4b",
        "text_secondary": "#4338ca",
        "text_muted": "#9ca3af",
        "font_display": "Cabinet Grotesk",
        "font_body": "Plus Jakarta Sans",
        "scale_ratio": 1.25,
        "heading_weight": "800",
        "heading_line_height": "1.1",
        "letter_spacing_heading": "-0.03em",
        "border_radius_sm": "0.75rem",
        "border_radius_md": "1rem",
        "border_radius_lg": "1.5rem",
        "shadow": "md",
        "entrance_style": "scale",
        "transition_duration": "300ms",
        "section_padding_y": "5rem",
        "container_max_width": "1280px",
        "runtime_hints": ["Bouncy springs on hover", "Count-up numbers", "Gradient mesh backgrounds", "Bento grid layout"],
        "fallback_rules": ["Replace spring with simple scale", "Show final numbers without animation"],
    },
    "immersive-3d": {
        "primary": "#0d1117",
        "secondary": "#c9d1d9",
        "accent": "#58a6ff",
        "background": "#010409",
        "surface": "#161b22",
        "text_primary": "#f0f6fc",
        "text_secondary": "#8b949e",
        "text_muted": "#484f58",
        "font_display": "Space Grotesk",
        "font_body": "Inter",
        "scale_ratio": 1.333,
        "heading_weight": "700",
        "heading_line_height": "1.05",
        "letter_spacing_heading": "-0.02em",
        "border_radius_sm": "0.5rem",
        "border_radius_md": "1rem",
        "border_radius_lg": "1.5rem",
        "shadow": "lg",
        "entrance_style": "fade-up",
        "transition_duration": "400ms",
        "section_padding_y": "6rem",
        "container_max_width": "1440px",
        "runtime_hints": ["3D hero scene via Three.js or Spline", "Parallax depth layers", "Smooth scroll with Lenis"],
        "fallback_rules": ["Replace 3D scene with static gradient", "Hide WebGL canvas on no-JS"],
    },
    "audio-reactive-canvas": {
        "primary": "#1a1a2e",
        "secondary": "#e0e0e0",
        "accent": "#ff2d55",
        "background": "#0f0f1a",
        "surface": "#1a1a2e",
        "text_primary": "#f0f0f0",
        "text_secondary": "#a0a0b0",
        "text_muted": "#606070",
        "font_display": "Space Grotesk",
        "font_body": "JetBrains Mono",
        "scale_ratio": 1.25,
        "heading_weight": "500",
        "heading_line_height": "1.1",
        "letter_spacing_heading": "0.05em",
        "border_radius_sm": "0",
        "border_radius_md": "0.25rem",
        "border_radius_lg": "0.5rem",
        "shadow": "none",
        "entrance_style": "fade-in",
        "transition_duration": "200ms",
        "section_padding_y": "4rem",
        "container_max_width": "1200px",
        "runtime_hints": ["p5.js canvas background", "Tone.js audio reactive", "Frequency-driven visual"],
        "fallback_rules": ["Show static canvas snapshot", "Disable audio features gracefully"],
    },
    "cosmos-dark-curation": {
        "primary": "#0a0a14",
        "secondary": "#e8e0d5",
        "accent": "#6366f1",
        "background": "#0a0a14",
        "surface": "#141422",
        "text_primary": "#e8e0d5",
        "text_secondary": "#a0a0b0",
        "text_muted": "#606070",
        "font_display": "Syne",
        "font_body": "DM Sans",
        "scale_ratio": 1.333,
        "heading_weight": "700",
        "heading_line_height": "1.05",
        "letter_spacing_heading": "-0.02em",
        "border_radius_sm": "0.5rem",
        "border_radius_md": "1rem",
        "border_radius_lg": "1.5rem",
        "shadow": "lg",
        "entrance_style": "fade-up",
        "transition_duration": "400ms",
        "section_padding_y": "6rem",
        "container_max_width": "1400px",
        "runtime_hints": ["Dark canvas with vivid content", "Color-coded categories", "Bento grid layout", "Rive interactive elements"],
        "fallback_rules": ["Replace Rive with static SVG", "Maintain dark background on no-JS"],
    },
    "arena-monastic-grid": {
        "primary": "#f5f5f0",
        "secondary": "#1a1a1a",
        "accent": "#2563eb",
        "background": "#f5f5f0",
        "surface": "#ffffff",
        "text_primary": "#1a1a1a",
        "text_secondary": "#4a4a4a",
        "text_muted": "#9a9a9a",
        "font_display": "Newsreader",
        "font_body": "Space Mono",
        "scale_ratio": 1.2,
        "heading_weight": "400",
        "heading_line_height": "1.2",
        "letter_spacing_heading": "normal",
        "border_radius_sm": "0",
        "border_radius_md": "0",
        "border_radius_lg": "0",
        "shadow": "none",
        "entrance_style": "fade-in",
        "transition_duration": "500ms",
        "section_padding_y": "8rem",
        "container_max_width": "1080px",
        "runtime_hints": ["Calm intentional transitions", "Research-library grid", "Generous whitespace"],
        "fallback_rules": ["No animation needed — already monastic"],
    },
    "yokoo-psychedelic-maximalism": {
        "primary": "#D42B2B",
        "secondary": "#F5C518",
        "accent": "#FF6B35",
        "background": "#1A1A2E",
        "surface": "#2a2a3e",
        "text_primary": "#E8E0D5",
        "text_secondary": "#c0b8a8",
        "text_muted": "#807868",
        "font_display": "Anybody",
        "font_body": "DM Sans",
        "scale_ratio": 1.5,
        "heading_weight": "900",
        "heading_line_height": "0.95",
        "letter_spacing_heading": "-0.04em",
        "border_radius_sm": "0",
        "border_radius_md": "0",
        "border_radius_lg": "0",
        "shadow": "none",
        "entrance_style": "scale",
        "transition_duration": "200ms",
        "section_padding_y": "4rem",
        "container_max_width": "1440px",
        "runtime_hints": ["Psychedelic color shifts", "Collage layering", "p5.js generative patterns", "Maximum visual density"],
        "fallback_rules": ["Show static collage layout", "Replace generative art with CSS gradient"],
    },
    "fukuda-optical-precision": {
        "primary": "#000000",
        "secondary": "#ffffff",
        "accent": "#CC0000",
        "background": "#ffffff",
        "surface": "#f5f5f5",
        "text_primary": "#000000",
        "text_secondary": "#333333",
        "text_muted": "#888888",
        "font_display": "DM Serif Display",
        "font_body": "DM Sans",
        "scale_ratio": 1.414,
        "heading_weight": "400",
        "heading_line_height": "1.0",
        "letter_spacing_heading": "normal",
        "border_radius_sm": "0",
        "border_radius_md": "0",
        "border_radius_lg": "0",
        "shadow": "none",
        "entrance_style": "fade-in",
        "transition_duration": "600ms",
        "section_padding_y": "8rem",
        "container_max_width": "1080px",
        "runtime_hints": ["Optical illusion transitions", "Figure-ground plays", "Precise geometric placement"],
        "fallback_rules": ["Static layout preserves optical illusion", "No-JS safe by design"],
    },
}


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _parse_colors_from_prose(color_str: str) -> dict[str, str]:
    """Extract hex-like color semantics from a prose color description.

    e.g. 'Near-black + white + single coral accent' -> rough mapping.
    This is a best-effort parser; pre-defined _DIRECTION_TOKENS take
    precedence.
    """
    colors: dict[str, str] = {}
    lower = color_str.lower()

    hex_matches = re.findall(r"#[0-9a-fA-F]{3,8}", color_str)
    if hex_matches:
        keys = ["primary", "secondary", "accent"]
        for i, hx in enumerate(hex_matches[:3]):
            colors[keys[i]] = hx

    if "black" in lower and "primary" not in colors:
        colors["primary"] = "#1a1a1a"
    if "white" in lower and "secondary" not in colors:
        colors["secondary"] = "#ffffff"
    if "navy" in lower and "primary" not in colors:
        colors["primary"] = "#1b2a4a"
    if "cream" in lower and "secondary" not in colors:
        colors["secondary"] = "#faf3e8"
    if "gold" in lower and "accent" not in colors:
        colors["accent"] = "#c9a84c"
    if "coral" in lower and "accent" not in colors:
        colors["accent"] = "#ff6b5b"
    if "blue" in lower and "accent" not in colors:
        colors["accent"] = "#3b82f6"
    if "terracotta" in lower and "accent" not in colors:
        colors["accent"] = "#c4623a"
    if "green" in lower and "primary" not in colors:
        colors["primary"] = "#2d5a3d"
    if "sand" in lower and "secondary" not in colors:
        colors["secondary"] = "#f5efe6"

    return colors


def _parse_fonts_from_prose(font_str: str) -> tuple[str, str]:
    """Extract display and body font names from a prose description.

    e.g. 'Satoshi (display) + IBM Plex Sans (body)' -> ('Satoshi', 'IBM Plex Sans')
    """
    display = "Inter"
    body = "Inter"

    parts = re.split(r"\s*\+\s*", font_str)
    for part in parts:
        cleaned = re.sub(r"\(.*?\)", "", part).strip()
        lower_part = part.lower()
        if "display" in lower_part or "heading" in lower_part:
            display = cleaned
        elif "body" in lower_part:
            body = cleaned

    if len(parts) >= 2 and display == "Inter" and body == "Inter":
        display = re.sub(r"\(.*?\)", "", parts[0]).strip()
        body = re.sub(r"\(.*?\)", "", parts[1]).strip()

    return display, body


def extract_tokens_from_direction(direction: dict[str, Any]) -> DesignTokens:
    """Parse a DESIGN_DIRECTIONS entry into a DesignTokens instance.

    Uses pre-defined token mappings when available, falls back to
    parsing the prose description fields.
    """
    name = direction.get("name", "")

    # Start with pre-defined tokens if available
    preset = _DIRECTION_TOKENS.get(name, {})

    # Parse fonts from direction prose as fallback
    font_str = direction.get("fonts", "")
    parsed_display, parsed_body = _parse_fonts_from_prose(font_str)

    display_font = preset.get("font_display", parsed_display)
    body_font = preset.get("font_body", parsed_body)

    # Parse colors from direction prose as fallback
    color_str = direction.get("colors", "")
    parsed_colors = _parse_colors_from_prose(color_str)

    # CDN deps from map
    cdn_deps_keys = DIRECTION_CDN_MAP.get(name, ["gsap", "scrolltrigger"])
    cdn_deps = [CDN_STACK[k] for k in cdn_deps_keys if k in CDN_STACK]

    return DesignTokens(
        primary=preset.get("primary", parsed_colors.get("primary", "#1a1a1a")),
        secondary=preset.get("secondary", parsed_colors.get("secondary", "#f5f5f5")),
        accent=preset.get("accent", parsed_colors.get("accent", "#ff6b6b")),
        background=preset.get("background", "#ffffff"),
        surface=preset.get("surface", "#f8f8f8"),
        text_primary=preset.get("text_primary", "#111111"),
        text_secondary=preset.get("text_secondary", "#555555"),
        text_muted=preset.get("text_muted", "#999999"),
        font_display=display_font,
        font_display_url=_font_url(display_font),
        font_body=body_font,
        font_body_url=_font_url(body_font),
        scale_ratio=preset.get("scale_ratio", 1.25),
        base_size="16px",
        heading_weight=preset.get("heading_weight", "700"),
        heading_line_height=preset.get("heading_line_height", "1.1"),
        body_line_height="1.6",
        letter_spacing_heading=preset.get("letter_spacing_heading", "-0.02em"),
        section_padding_y=preset.get("section_padding_y", "6rem"),
        container_max_width=preset.get("container_max_width", "1280px"),
        container_padding_x="1.5rem",
        card_gap="2rem",
        element_gap="1rem",
        border_radius_sm=preset.get("border_radius_sm", "0.375rem"),
        border_radius_md=preset.get("border_radius_md", "0.75rem"),
        border_radius_lg=preset.get("border_radius_lg", "1rem"),
        shadow=preset.get("shadow", "sm"),
        animation_enabled=True,
        transition_duration=preset.get("transition_duration", "300ms"),
        entrance_style=preset.get("entrance_style", "fade-up"),
        grid_columns=12,
        direction_name=name,
        cdn_deps=cdn_deps,
        runtime_hints=preset.get("runtime_hints", []),
        fallback_rules=preset.get("fallback_rules", []),
    )


def extract_tokens_from_taste(
    profile: dict[str, Any],
    direction: dict[str, Any],
) -> DesignTokens:
    """Merge taste profile overlay colors into direction-based tokens.

    The taste profile can override colors extracted from VLM analysis.
    Direction structure (fonts, spacing, motion) is preserved; only
    color palette is influenced by taste.
    """
    tokens = extract_tokens_from_direction(direction)

    palettes = profile.get("color_palettes", [])
    if not palettes:
        return tokens

    # Use the most recent palette's colors
    latest_palette = palettes[-1]
    colors = latest_palette.get("colors", [])
    if not colors:
        return tokens

    # Map palette colors to token slots
    if len(colors) >= 1:
        tokens.primary = colors[0]
    if len(colors) >= 2:
        tokens.secondary = colors[1]
    if len(colors) >= 3:
        tokens.accent = colors[2]
    if len(colors) >= 4:
        tokens.background = colors[3]
    if len(colors) >= 5:
        tokens.surface = colors[4]

    return tokens


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------


def tokens_to_css_vars(tokens: DesignTokens) -> str:
    """Generate a CSS :root block with custom properties."""
    lines = [
        ":root {",
        f"  --color-primary: {tokens.primary};",
        f"  --color-secondary: {tokens.secondary};",
        f"  --color-accent: {tokens.accent};",
        f"  --color-bg: {tokens.background};",
        f"  --color-surface: {tokens.surface};",
        f"  --color-text: {tokens.text_primary};",
        f"  --color-text-secondary: {tokens.text_secondary};",
        f"  --color-text-muted: {tokens.text_muted};",
        f"  --font-display: '{tokens.font_display}', sans-serif;",
        f"  --font-body: '{tokens.font_body}', sans-serif;",
        f"  --scale-ratio: {tokens.scale_ratio};",
        f"  --base-size: {tokens.base_size};",
        f"  --heading-weight: {tokens.heading_weight};",
        f"  --heading-lh: {tokens.heading_line_height};",
        f"  --body-lh: {tokens.body_line_height};",
        f"  --ls-heading: {tokens.letter_spacing_heading};",
        f"  --section-py: {tokens.section_padding_y};",
        f"  --container-max: {tokens.container_max_width};",
        f"  --container-px: {tokens.container_padding_x};",
        f"  --card-gap: {tokens.card_gap};",
        f"  --element-gap: {tokens.element_gap};",
        f"  --radius-sm: {tokens.border_radius_sm};",
        f"  --radius-md: {tokens.border_radius_md};",
        f"  --radius-lg: {tokens.border_radius_lg};",
        f"  --shadow: {_shadow_value(tokens.shadow)};",
        f"  --transition-duration: {tokens.transition_duration};",
        "}",
    ]
    return "\n".join(lines)


def _shadow_value(level: str) -> str:
    """Convert shadow level name to CSS value."""
    mapping = {
        "none": "none",
        "sm": "0 1px 2px rgba(0,0,0,0.05)",
        "md": "0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1)",
        "lg": "0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -4px rgba(0,0,0,0.1)",
    }
    return mapping.get(level, mapping["sm"])


def tokens_to_prompt_block(tokens: DesignTokens) -> str:
    """Generate a prompt-friendly text block describing the design tokens."""
    hints = "\n".join(f"  - {h}" for h in tokens.runtime_hints) if tokens.runtime_hints else "  - None"
    return textwrap.dedent(f"""\
        DESIGN TOKENS ({tokens.direction_name}):
        Colors:
          Primary: {tokens.primary}
          Secondary: {tokens.secondary}
          Accent: {tokens.accent}
          Background: {tokens.background}
          Surface: {tokens.surface}
          Text: {tokens.text_primary} / {tokens.text_secondary} / {tokens.text_muted}
        Typography:
          Display: {tokens.font_display} (weight {tokens.heading_weight}, lh {tokens.heading_line_height})
          Body: {tokens.font_body} (lh {tokens.body_line_height})
          Scale ratio: {tokens.scale_ratio}
          Heading letter-spacing: {tokens.letter_spacing_heading}
        Spacing:
          Section padding: {tokens.section_padding_y}
          Container: {tokens.container_max_width} (px {tokens.container_padding_x})
          Card gap: {tokens.card_gap}
        Visual:
          Border radius: {tokens.border_radius_sm} / {tokens.border_radius_md} / {tokens.border_radius_lg}
          Shadow: {tokens.shadow}
        Motion:
          Entrance: {tokens.entrance_style}
          Duration: {tokens.transition_duration}
          Enabled: {tokens.animation_enabled}
        Runtime hints:
        {hints}

        USE CSS CUSTOM PROPERTIES: var(--color-primary), var(--color-accent), var(--font-display), etc.
        DO NOT hardcode hex values or font names in HTML.""")


def tokens_to_tailwind_config(tokens: DesignTokens) -> str:
    """Generate a Tailwind CSS v4 @theme extension block."""
    return textwrap.dedent(f"""\
        @theme {{
          --color-primary: {tokens.primary};
          --color-secondary: {tokens.secondary};
          --color-accent: {tokens.accent};
          --color-bg: {tokens.background};
          --color-surface: {tokens.surface};
          --color-text: {tokens.text_primary};
          --color-text-secondary: {tokens.text_secondary};
          --color-text-muted: {tokens.text_muted};
          --font-display: '{tokens.font_display}', sans-serif;
          --font-body: '{tokens.font_body}', sans-serif;
        }}""")


def build_head_block(tokens: DesignTokens) -> str:
    """Generate a complete HTML <head> block with meta, fonts, CDN deps, and CSS vars."""
    font_links: list[str] = []
    seen_urls: set[str] = set()
    for url in (tokens.font_display_url, tokens.font_body_url):
        if url and url not in seen_urls:
            seen_urls.add(url)
            font_links.append(f'  <link rel="stylesheet" href="{url}">')

    css_vars = tokens_to_css_vars(tokens)

    # CDN scripts — always include tailwind + alpine, then direction-specific
    core_scripts = [
        f'  <script src="{CDN_STACK["tailwind"]}"></script>',
        f'  <script defer src="{CDN_STACK["alpine"]}"></script>',
    ]

    direction_scripts: list[str] = []
    for dep_url in tokens.cdn_deps:
        if dep_url in (CDN_STACK["tailwind"], CDN_STACK["alpine"]):
            continue
        # ESM modules need type="module"
        if "+esm" in dep_url:
            direction_scripts.append(f'  <script type="module" src="{dep_url}"></script>')
        else:
            direction_scripts.append(f'  <script src="{dep_url}"></script>')

    all_scripts = core_scripts + direction_scripts

    parts = [
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '  <meta name="robots" content="index, follow">',
        "",
        "  <!-- Fonts -->",
        *font_links,
        "",
        "  <!-- CDN Dependencies -->",
        *all_scripts,
        "",
        "  <!-- Design Tokens -->",
        "  <style>",
        *[f"    {line}" for line in css_vars.split("\n")],
        "",
        "    body {",
        "      font-family: var(--font-body);",
        "      line-height: var(--body-lh);",
        "      color: var(--color-text);",
        "      background-color: var(--color-bg);",
        "    }",
        "",
        "    h1, h2, h3, h4, h5, h6 {",
        "      font-family: var(--font-display);",
        "      font-weight: var(--heading-weight);",
        "      line-height: var(--heading-lh);",
        "      letter-spacing: var(--ls-heading);",
        "    }",
        "",
        "    @media (prefers-reduced-motion: reduce) {",
        "      *, *::before, *::after {",
        "        animation-duration: 0.01ms !important;",
        "        animation-iteration-count: 1 !important;",
        "        transition-duration: 0.01ms !important;",
        "      }",
        "    }",
        "  </style>",
        "</head>",
    ]
    return "\n".join(parts)
