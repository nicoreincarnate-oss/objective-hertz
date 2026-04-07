"""Canonical MAGMA edge types — single source of truth for both backend writes
and frontend rendering. Mirror this in `hermes/web/frontend/lib/magma-edge-types.ts`.
"""

from enum import Enum


class EdgeType(str, Enum):
    DERIVED_FROM = "DERIVED_FROM"
    RELATED_TO = "RELATED_TO"
    CONTRADICTS = "CONTRADICTS"
    SUPPORTS = "SUPPORTS"
    MENTIONS = "MENTIONS"
    OWNED_BY = "OWNED_BY"
    VERSION_OF = "VERSION_OF"
    CAUSED_BY = "CAUSED_BY"


# Visual hints used by the frontend (color, opacity, dashes, etc.)
EDGE_VISUAL: dict[EdgeType, dict] = {
    EdgeType.DERIVED_FROM: {"color": "#888888", "opacity": 0.3, "width": 1, "dashes": False},
    EdgeType.RELATED_TO: {"color": "#88ddff", "opacity": 0.5, "width": 1.5, "dashes": False},
    EdgeType.CONTRADICTS: {"color": "#ff5a7a", "opacity": 0.7, "width": 1.5, "dashes": True},
    EdgeType.SUPPORTS: {"color": "#44ff88", "opacity": 0.6, "width": 2, "dashes": False},
    EdgeType.MENTIONS: {"color": "#ffffff", "opacity": 0.3, "width": 0.8, "dashes": False},
    EdgeType.OWNED_BY: {"color": "#ffd700", "opacity": 0.4, "width": 1, "dashes": False},
    EdgeType.VERSION_OF: {"color": "#aa88ff", "opacity": 0.7, "width": 2, "dashes": False},
    EdgeType.CAUSED_BY: {"color": "#ffb347", "opacity": 0.6, "width": 1.5, "dashes": False},
}


def visual_for(edge_type: str) -> dict:
    """Return visual hints for an edge type, with safe fallback."""
    try:
        return EDGE_VISUAL[EdgeType(edge_type)]
    except (KeyError, ValueError):
        return EDGE_VISUAL[EdgeType.RELATED_TO]
