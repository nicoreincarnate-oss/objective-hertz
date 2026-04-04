"""Runtime helpers for the DeerFlow-backed research daemon."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .sources import ResearchSourceFetcher, ResearchSourceItem
from .upstream import DeerFlowEmbeddedEngine, DeerFlowEngineResult


UTC = timezone.utc

DEFAULT_TOPIC = (
    "Continuously research how Objective Hertz / Perseus should evolve across "
    "AI research papers, GitHub repos, tooling, and agent architecture."
)

DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "stanford": {
        "label": "Stanford AI stream",
        "channels": ["Stanford HAI", "SAIL", "CS25", "Stanford NLP"],
        "query_bias": ["agents", "reasoning", "memory", "tool use", "automation"],
    },
    "arxiv": {
        "label": "arXiv frontier stream",
        "channels": ["cs.AI", "cs.CL", "cs.LG", "cs.SE"],
        "query_bias": ["agentic systems", "long context", "automation", "browsing", "memory"],
    },
    "github": {
        "label": "GitHub repo stream",
        "channels": ["trending", "new releases", "agent tooling", "browser automation"],
        "query_bias": ["agents", "MCP", "browser use", "local inference", "memory"],
    },
}


@dataclass(slots=True)
class ResearchArtifact:
    type: str
    path: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": self.path,
            "label": self.label,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ResearchCycleRecord:
    cycle_id: str
    created_at: str
    topic: str
    mode: str
    status: str
    sources: list[str]
    objectives: list[str]
    recommended_lane: str
    items: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[ResearchArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "topic": self.topic,
            "mode": self.mode,
            "status": self.status,
            "sources": list(self.sources),
            "objectives": list(self.objectives),
            "recommended_lane": self.recommended_lane,
            "items": list(self.items),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


class DeerFlowResearchRuntime:
    """Persistent local state and report generation for research cycles."""

    def __init__(self, root_dir: Path | None = None) -> None:
        repo_root = root_dir or Path(__file__).resolve().parent.parent
        self._state_path = repo_root / "data" / "deerflow_research" / "state.json"
        self._cycle_dir = repo_root / "output" / "deerflow" / "cycles"
        self._brief_dir = repo_root / "output" / "deerflow" / "daily"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._cycle_dir.mkdir(parents=True, exist_ok=True)
        self._brief_dir.mkdir(parents=True, exist_ok=True)
        self._source_fetcher = ResearchSourceFetcher()
        self._engine = DeerFlowEmbeddedEngine(repo_root)

    async def health_check(self) -> dict[str, Any]:
        state = self._load_state()
        engine = await self._engine.health_check()
        return {
            "agent": "deerflow_research",
            "status": "ready",
            "engine": engine,
            "preferred_mode": "continuous_research",
            "supported_sources": list(DEFAULT_SOURCES.keys()),
            "cycle_count": len(state.get("cycles", [])),
            "last_cycle_at": state.get("last_cycle_at"),
            "last_daily_brief_at": state.get("last_daily_brief_at"),
            "state_path": str(self._state_path),
            "cycle_dir": str(self._cycle_dir),
            "brief_dir": str(self._brief_dir),
        }

    async def evolution_research_cycle(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        sources = self._normalize_sources(payload.get("sources"))
        mode = str(payload.get("mode") or "continuous")
        topic = str(payload.get("topic") or DEFAULT_TOPIC)
        objectives = self._objectives_for_mode(mode)
        items = await self._collect_source_items(
            sources=sources,
            topic=topic,
            limit=int(payload.get("limit") or 10),
        )
        analysis = await self._analyze_with_deerflow(
            mode=mode,
            topic=topic,
            sources=sources,
            objectives=objectives,
            items=items,
        )
        cycle = self._create_cycle(
            topic=topic,
            mode=mode,
            sources=sources,
            objectives=objectives,
            items=items,
            analysis=analysis,
        )
        self._persist_cycle(cycle)
        artifact = cycle.artifacts[0].to_dict() if cycle.artifacts else None
        return {
            "ok": True,
            "status": "completed",
            "cycle_id": cycle.cycle_id,
            "recommended_inference_lane": cycle.recommended_lane,
            "sources": cycle.sources,
            "objectives": cycle.objectives,
            "item_count": len(cycle.items),
            "fresh_items": cycle.items[:8],
            "engine": analysis.engine,
            "engine_metadata": analysis.metadata,
            "artifact": artifact,
            "artifacts": [item.to_dict() for item in cycle.artifacts],
        }

    async def paper_scan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        topic = str(payload.get("topic") or "AI research papers relevant to Objective Hertz")
        sources = self._normalize_sources(payload.get("sources"), fallback=["stanford", "arxiv"])
        objectives = [
            "Collect newly published papers and project pages.",
            "Rank them by relevance to agents, memory, browsing, local inference, and orchestration.",
            "Produce concise adopt / watch / ignore recommendations for Perseus.",
        ]
        items = await self._collect_source_items(
            sources=sources,
            topic=topic,
            limit=int(payload.get("limit") or 12),
        )
        analysis = await self._analyze_with_deerflow(
            mode="paper_scan",
            topic=topic,
            sources=sources,
            objectives=objectives,
            items=items,
        )
        cycle = self._create_cycle(
            topic=topic,
            mode="paper_scan",
            sources=sources,
            objectives=objectives,
            items=items,
            analysis=analysis,
        )
        self._persist_cycle(cycle)
        return {
            "ok": True,
            "status": "completed",
            "cycle_id": cycle.cycle_id,
            "recommended_inference_lane": cycle.recommended_lane,
            "sources": cycle.sources,
            "item_count": len(cycle.items),
            "fresh_items": cycle.items[:8],
            "engine": analysis.engine,
            "engine_metadata": analysis.metadata,
            "artifact": cycle.artifacts[0].to_dict(),
        }

    async def repo_scan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        topic = str(payload.get("topic") or "Trending and newly released GitHub repos relevant to Perseus")
        sources = self._normalize_sources(payload.get("sources"), fallback=["github"])
        objectives = [
            "Watch trending, newly released, and fast-growing GitHub repos.",
            "Identify donor repos worth stealing for Hermes, Titan, ClawdBot, OpenJarvis, and local inference.",
            "Summarize integration opportunities and migration risks.",
        ]
        items = await self._collect_source_items(
            sources=sources,
            topic=topic,
            limit=int(payload.get("limit") or 12),
        )
        analysis = await self._analyze_with_deerflow(
            mode="repo_scan",
            topic=topic,
            sources=sources,
            objectives=objectives,
            items=items,
        )
        cycle = self._create_cycle(
            topic=topic,
            mode="repo_scan",
            sources=sources,
            objectives=objectives,
            items=items,
            analysis=analysis,
        )
        self._persist_cycle(cycle)
        return {
            "ok": True,
            "status": "completed",
            "cycle_id": cycle.cycle_id,
            "recommended_inference_lane": cycle.recommended_lane,
            "sources": cycle.sources,
            "item_count": len(cycle.items),
            "fresh_items": cycle.items[:8],
            "engine": analysis.engine,
            "engine_metadata": analysis.metadata,
            "artifact": cycle.artifacts[0].to_dict(),
        }

    async def daily_evolution_brief(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        state = self._load_state()
        now = datetime.now(UTC)
        since = now - timedelta(hours=int(payload.get("lookback_hours", 24) or 24))
        recent_cycles = [
            cycle
            for cycle in state.get("cycles", [])
            if self._parse_dt(cycle.get("created_at")) >= since
        ]
        brief_path = self._brief_dir / f"{now.date().isoformat()}-daily-evolution-brief.md"
        analysis = await self._analyze_with_deerflow(
            mode="daily_evolution_brief",
            topic="24 hour Perseus evolution brief",
            sources=sorted({source for cycle in recent_cycles for source in cycle.get("sources", [])}),
            objectives=[
                "Synthesize the last 24 hours of DeerFlow research into a concise operator brief.",
                "Highlight the most urgent changes Perseus should make next.",
                "Name specific experiments and implementation targets for the next 24 hours.",
            ],
            items=[
                {
                    "source": cycle.get("mode", "cycle"),
                    "title": cycle.get("topic", "DeerFlow cycle"),
                    "url": next(
                        (artifact.get("path") for artifact in cycle.get("artifacts", []) if artifact.get("path")),
                        "",
                    ),
                    "published_at": cycle.get("created_at", ""),
                    "summary": f"{len(cycle.get('items', []))} fresh items, {len(cycle.get('artifacts', []))} artifacts.",
                    "tags": cycle.get("sources", []),
                }
                for cycle in recent_cycles
            ],
            lookback_hours=int(payload.get("lookback_hours", 24) or 24),
        )
        lines = [
            "# DeerFlow Daily Evolution Brief",
            "",
            f"- Generated at: {now.isoformat()}",
            f"- Lookback window: last {int(payload.get('lookback_hours', 24) or 24)} hours",
            f"- Research cycles reviewed: {len(recent_cycles)}",
            f"- Engine: {analysis.engine}",
            "",
            "## Current Recommendation",
            "",
            "Perseus should keep running continuous research loops on Stanford, arXiv, and GitHub,",
            "then feed adoption candidates into OpenJarvis, ClawdBot, Titan, and Ruflo as ranked implementation work.",
            "",
            "## Cycles Reviewed",
            "",
        ]
        if recent_cycles:
            for cycle in recent_cycles:
                lines.extend(
                    [
                        f"### {cycle.get('mode', 'cycle')} — {cycle.get('cycle_id', 'unknown')}",
                        "",
                        f"- Topic: {cycle.get('topic', '')}",
                        f"- Sources: {', '.join(cycle.get('sources', []))}",
                        f"- Recommended lane: {cycle.get('recommended_lane', 'local-heavy')}",
                        f"- Artifact count: {len(cycle.get('artifacts', []))}",
                        f"- Fresh items captured: {len(cycle.get('items', []))}",
                        "",
                    ]
                )
                for item in cycle.get("items", [])[:5]:
                    lines.append(
                        f"  - {item.get('source', 'source')}: [{item.get('title', 'untitled')}]({item.get('url', '')})"
                    )
                lines.append("")
        else:
            lines.extend(
                [
                    "No completed cycles in the window yet.",
                    "",
                    "Keep the daemon researching its own backlog until new external material arrives.",
                    "",
                ]
            )

        if analysis.analysis_markdown:
            lines.extend(
                [
                    "## DeerFlow Engine Synthesis",
                    "",
                    analysis.analysis_markdown,
                    "",
                ]
            )

        lines.extend(
            [
                "## Next Actions",
                "",
                "1. Pull the highest-confidence adoption candidates into OpenJarvis implementation planning.",
                "2. Route browser automation/tooling donors to ClawdBot evaluation.",
                "3. Route memory/orchestration donors to OpenJarvis and Hermes architecture review.",
                "4. Route revenue-impacting research to Titan workflow upgrades.",
                "",
            ]
        )

        brief_path.write_text("\n".join(lines), encoding="utf-8")
        state["last_daily_brief_at"] = now.isoformat()
        state.setdefault("daily_briefs", []).append(
            {
                "generated_at": now.isoformat(),
                "path": str(brief_path),
                "lookback_hours": int(payload.get("lookback_hours", 24) or 24),
                "cycle_count": len(recent_cycles),
            }
        )
        self._save_state(state)
        return {
            "ok": True,
            "status": "completed",
            "artifact": {
                "type": "markdown",
                "path": str(brief_path),
                "label": "daily_evolution_brief",
            },
            "cycle_count": len(recent_cycles),
            "engine": analysis.engine,
            "engine_metadata": analysis.metadata,
            "recommended_inference_lane": "local-heavy",
        }

    def _create_cycle(
        self,
        *,
        topic: str,
        mode: str,
        sources: list[str],
        objectives: list[str],
        items: list[dict[str, Any]],
        analysis: DeerFlowEngineResult,
    ) -> ResearchCycleRecord:
        now = datetime.now(UTC)
        cycle_id = f"df_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        cycle_path = self._cycle_dir / f"{cycle_id}.md"
        cycle_lines = [
            "# DeerFlow Research Cycle",
            "",
            f"- cycle_id: {cycle_id}",
            f"- created_at: {now.isoformat()}",
            f"- mode: {mode}",
            f"- topic: {topic}",
            f"- recommended_inference_lane: local-heavy",
            f"- engine: {analysis.engine}",
            "",
            "## Sources",
            "",
        ]
        for source in sources:
            metadata = DEFAULT_SOURCES.get(source, {})
            cycle_lines.append(
                f"- {source}: {metadata.get('label', source)} "
                f"({', '.join(metadata.get('channels', []))})"
            )
        cycle_lines.extend(
            [
                "",
                "## Objectives",
                "",
            ]
        )
        for objective in objectives:
            cycle_lines.append(f"- {objective}")
        cycle_lines.extend(
            [
                "",
                "## Fresh Finds",
                "",
            ]
        )
        if items:
            for item in items[:20]:
                cycle_lines.extend(
                    [
                        f"### {item.get('title', 'Untitled')}",
                        "",
                        f"- Source: {item.get('source', 'unknown')}",
                        f"- Published: {item.get('published_at', '')}",
                        f"- URL: {item.get('url', '')}",
                        f"- Tags: {', '.join(item.get('tags', []))}",
                        f"- Summary: {item.get('summary', '')}",
                        "",
                    ]
                )
        else:
            cycle_lines.extend(
                [
                    "No fresh source items were captured in this cycle.",
                    "",
                ]
            )
        if analysis.analysis_markdown:
            cycle_lines.extend(
                [
                    "",
                    "## DeerFlow Engine Analysis",
                    "",
                    analysis.analysis_markdown,
                    "",
                ]
            )
        elif analysis.metadata.get("error"):
            cycle_lines.extend(
                [
                    "",
                    "## DeerFlow Engine Analysis",
                    "",
                    f"Engine call failed: {analysis.metadata['error']}",
                    "",
                ]
            )
        cycle_lines.extend(
            [
                "",
                "## Output Contract",
                "",
                "- Collect new external material whenever available.",
                "- If no new material is available, recursively refine unresolved research questions.",
                "- Produce ranked adoption candidates for Hermes, Titan, ClawdBot, OpenJarvis, and local inference lanes.",
                "",
            ]
        )
        cycle_path.write_text("\n".join(cycle_lines), encoding="utf-8")
        return ResearchCycleRecord(
            cycle_id=cycle_id,
            created_at=now.isoformat(),
            topic=topic,
            mode=mode,
            status="completed",
            sources=sources,
            objectives=objectives,
            recommended_lane="local-heavy",
            items=items,
            artifacts=[
                ResearchArtifact(
                    type="markdown",
                    path=str(cycle_path),
                    label=mode,
                    metadata={
                        "item_count": len(items),
                        "sources": sources,
                        "engine": analysis.engine,
                        **analysis.metadata,
                    },
                )
            ],
        )

    def _persist_cycle(self, cycle: ResearchCycleRecord) -> None:
        state = self._load_state()
        state.setdefault("cycles", []).append(cycle.to_dict())
        state["last_cycle_at"] = cycle.created_at
        state["last_cycle_id"] = cycle.cycle_id
        self._save_state(state)

    def _load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"cycles": [], "daily_briefs": []}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"cycles": [], "daily_briefs": []}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize_sources(self, raw_sources: Any, fallback: list[str] | None = None) -> list[str]:
        if isinstance(raw_sources, str):
            values = [item.strip().lower() for item in raw_sources.split(",") if item.strip()]
        elif isinstance(raw_sources, list):
            values = [str(item).strip().lower() for item in raw_sources if str(item).strip()]
        else:
            values = []
        allowed = list(DEFAULT_SOURCES.keys())
        normalized = [source for source in values if source in allowed]
        if normalized:
            return normalized
        return list(fallback or DEFAULT_SOURCES.keys())

    def _objectives_for_mode(self, mode: str) -> list[str]:
        if mode == "backfill":
            return [
                "Revisit unresolved research questions from prior cycles.",
                "Strengthen weak evidence chains before escalating adoption advice.",
                "Merge new findings into the running Perseus evolution narrative.",
            ]
        return [
            "Scan Stanford, arXiv, and GitHub for relevant new material.",
            "Extract the highest-leverage ideas for Hermes, Titan, ClawdBot, OpenJarvis, and local inference.",
            "Queue concrete implementation candidates and experiments for the next 24 hours.",
        ]

    async def _collect_source_items(self, *, sources: list[str], topic: str, limit: int) -> list[dict[str, Any]]:
        per_source = max(4, min(limit, 12))
        collected: list[ResearchSourceItem] = []
        for source in sources:
            try:
                items = await self._source_fetcher.fetch(source, topic=topic, limit=per_source)
                collected.extend(items)
            except Exception:
                continue

        deduped: list[ResearchSourceItem] = []
        seen: set[str] = set()
        for item in sorted(collected, key=lambda candidate: candidate.published_at, reverse=True):
            key = item.url or item.title
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return [item.to_dict() for item in deduped[: max(limit, 8)]]

    async def _analyze_with_deerflow(
        self,
        *,
        mode: str,
        topic: str,
        sources: list[str],
        objectives: list[str],
        items: list[dict[str, Any]],
        lookback_hours: int | None = None,
    ) -> DeerFlowEngineResult:
        result = await self._engine.analyze(
            mode=mode,
            topic=topic,
            sources=sources,
            objectives=objectives,
            items=items,
            lookback_hours=lookback_hours,
        )
        if result.ok:
            return result
        return DeerFlowEngineResult(
            ok=False,
            engine=result.engine,
            analysis_markdown="",
            metadata={"error": result.metadata.get("error") or "embedded DeerFlow analysis unavailable"},
        )

    def _parse_dt(self, value: Any) -> datetime:
        if not isinstance(value, str):
            return datetime.fromtimestamp(0, tz=UTC)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return datetime.fromtimestamp(0, tz=UTC)
