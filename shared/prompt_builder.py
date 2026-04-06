"""
PromptBuilder — structured system prompt assembly with cache-aware scoping.

Phase 18a-01 of the Anatomy Integration plan.

Three scopes control cache eligibility:
  STATIC  — daemon identity, tool definitions, safety rules (cacheable, changes rarely)
  SESSION — memory index, campaign context (changes per session)
  TURN    — task-specific data, lead details (changes every call)

Sections are always ordered STATIC → SESSION → TURN so that Anthropic prompt
caching can reuse the static prefix across calls.  The builder enforces this
ordering at add-time and raises ValueError on violations.

Usage:
    pb = PromptBuilder()
    pb.add_section("identity", "You are Perseus...", PromptScope.STATIC, priority=10)
    pb.add_section("memory", memory_index, PromptScope.SESSION, priority=5)
    pb.add_section("task", lead_details, PromptScope.TURN, priority=1)
    system_str = pb.build_system()
    blocks = pb.build_system_blocks()  # for API calls with cache_control
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("perseus.prompt_builder")


# ---------------------------------------------------------------------------
# StickyLatch — Phase 18b-04: latch cache-sensitive config values
# ---------------------------------------------------------------------------

class StickyLatch:
    """Latch a value on first read; subsequent reads return the latched value.

    When system_config flags that affect LLM prompts (model tier, prompt template
    version, DNA version) change mid-session via the dashboard, they bust the
    Anthropic prompt cache because the system prompt content changes.  The latch
    preserves the first-seen value for the lifetime of the process (or until
    ``reset()`` is called), keeping the prompt prefix stable and cache-friendly.

    Thread-safe: uses a lock so daemon threads and async tasks can share one
    instance without races.
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str, factory: Callable[[], Any]) -> Any:
        """Return the latched value for *key*, calling *factory* only on first access."""
        with self._lock:
            if key not in self._values:
                self._values[key] = factory()
            return self._values[key]

    def peek(self, key: str) -> Any | None:
        """Return the latched value without invoking a factory, or None."""
        with self._lock:
            return self._values.get(key)

    def reset(self) -> None:
        """Clear all latched values (e.g. on session boundary / daemon restart)."""
        with self._lock:
            self._values.clear()


# Module-level instance — one per process, shared across all callers.
_session_latch = StickyLatch()


def get_session_latch() -> StickyLatch:
    """Return the module-level StickyLatch instance."""
    return _session_latch


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_BOUNDARY_MARKER = "\n\n---\n\n## Session Context"
MAX_SYSTEM_TOKENS = 100_000

_SECTION_SEPARATOR = "\n\n---\n\n"


# ---------------------------------------------------------------------------
# Scope enum
# ---------------------------------------------------------------------------

class PromptScope(enum.IntEnum):
    """Prompt section scope — determines cache eligibility and ordering.

    Integer values enforce the required ordering: STATIC < SESSION < TURN.
    """

    STATIC = 0   # daemon identity, tool defs, safety rules — cacheable
    SESSION = 1  # memory index, campaign context — changes per session
    TURN = 2     # task-specific data, lead details — changes every call


# ---------------------------------------------------------------------------
# Section dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromptSection:
    """A single named section of a system prompt."""

    name: str
    content: str
    scope: PromptScope
    priority: int = 0
    cache_control: dict | None = field(default=None)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class PromptBuilder:
    """Assemble system prompts with scope-aware ordering and cache annotations.

    Sections are maintained in insertion order but must respect the invariant:
    all STATIC sections before all SESSION sections before all TURN sections.
    """

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []
        self._names: set[str] = set()

    # -- mutation -----------------------------------------------------------

    def add_section(
        self,
        name: str,
        content: str,
        scope: PromptScope,
        priority: int = 0,
    ) -> None:
        """Add a section.  Raises ValueError on duplicate names or scope violations."""
        if name in self._names:
            raise ValueError(f"Duplicate section name: {name!r}")

        # Enforce ordering: new scope must be >= highest existing scope
        if self._sections:
            last_scope = self._sections[-1].scope
            if scope < last_scope:
                raise ValueError(
                    f"Scope violation: cannot add {scope.name} section {name!r} "
                    f"after {last_scope.name} section {self._sections[-1].name!r}. "
                    f"Sections must be ordered STATIC → SESSION → TURN."
                )

        # DANGEROUS_ prefix: no cache_control, emit warning
        cache_control: dict | None = None
        if name.startswith("DANGEROUS_"):
            logger.warning(
                "Section %r uses DANGEROUS_ prefix — cache control disabled. "
                "This should be rare; consider renaming if caching is safe.",
                name,
            )
            cache_control = None
        elif scope == PromptScope.STATIC:
            cache_control = {"cache_control": {"type": "ephemeral"}}

        section = PromptSection(
            name=name,
            content=content,
            scope=scope,
            priority=priority,
            cache_control=cache_control,
        )
        self._sections.append(section)
        self._names.add(name)

    # -- read ---------------------------------------------------------------

    def build_system(self) -> str:
        """Join all sections into a single system prompt string.

        Sections are separated by ``\\n\\n---\\n\\n``.  Returns empty string
        when no sections have been added.
        """
        if not self._sections:
            return ""

        result = _SECTION_SEPARATOR.join(s.content for s in self._sections)

        estimated = self.token_estimate()
        if estimated > MAX_SYSTEM_TOKENS:
            logger.warning(
                "System prompt exceeds MAX_SYSTEM_TOKENS (%d > %d). "
                "Consider trimming low-priority sections.",
                estimated,
                MAX_SYSTEM_TOKENS,
            )
        return result

    def build_system_blocks(self) -> list[dict]:
        """Return structured content blocks with cache_control annotations.

        Each block is a dict with ``type``, ``text``, and optionally
        ``cache_control`` — suitable for passing to the Anthropic messages API
        ``system`` parameter as a list of content blocks.

        Static sections (except DANGEROUS_ prefixed) get ephemeral cache_control.
        """
        blocks: list[dict] = []
        for section in self._sections:
            block: dict = {
                "type": "text",
                "text": section.content,
            }
            if section.cache_control:
                block["cache_control"] = section.cache_control["cache_control"]
            blocks.append(block)
        return blocks

    def token_estimate(self) -> int:
        """Rough token count: total characters / 4."""
        if not self._sections:
            return 0
        total_chars = sum(len(s.content) for s in self._sections)
        # Add separator chars
        total_chars += len(_SECTION_SEPARATOR) * max(0, len(self._sections) - 1)
        return total_chars // 4

    def section_names(self) -> list[str]:
        """Return section names in current order."""
        return [s.name for s in self._sections]
