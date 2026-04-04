"""Deterministic Kirito/Hermes command routing.

This module turns a freeform operator request into a structured routing plan
that the rest of the system can hand to Hermes, OpenJarvis, Titan, ClawdBot,
or Ruflo.

The router is intentionally offline and deterministic. Optional hooks allow
callers to override intent selection, target-agent selection, or payload
enrichment without replacing the core heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class KiritoRoutePlan:
    """Structured routing output for a Kirito command."""

    source_text: str
    intent: str
    target_agent: str
    task_type: str
    capability: str
    domain: str
    executor: str
    risk: str
    supports_auth: bool
    supports_full_autonomy: bool
    visibility_mode: str
    fallback_executor: str
    capability_family: str
    rationale: str
    requires_followup: bool
    local_action: bool
    followup_question: str | None = None
    payload: dict[str, Any] | None = None
    signals: tuple[str, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class KiritoRoutingHooks:
    """Optional extension hooks for callers that want to bend the heuristics."""

    intent_override: Callable[[str, Mapping[str, Any]], str | None] | None = None
    agent_override: Callable[[KiritoRoutePlan, str, Mapping[str, Any]], str | None] | None = None
    payload_enricher: Callable[[dict[str, Any], KiritoRoutePlan], Mapping[str, Any] | None] | None = None
    plan_override: Callable[[KiritoRoutePlan, str, Mapping[str, Any]], KiritoRoutePlan | None] | None = None


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    intent: str
    target_agent: str
    task_type: str
    capability: str
    domain: str
    executor: str
    risk: str
    supports_auth: bool
    supports_full_autonomy: bool
    visibility_mode: str
    fallback_executor: str
    capability_family: str
    local_action: bool
    confidence: float
    default_followup: str | None
    rationale_prefix: str
    match_phrases: tuple[str, ...]
    signal_phrases: tuple[str, ...]


_NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

_ROUTE_REGISTRY: tuple[_RouteSpec, ...] = (
    _RouteSpec(
        intent="open_dashboard",
        target_agent="hermes",
        task_type="open_dashboard",
        capability="operator_command",
        domain="desktop",
        executor="hermes",
        risk="low",
        supports_auth=True,
        supports_full_autonomy=True,
        visibility_mode="local",
        fallback_executor="orchestrator",
        capability_family="desktop",
        local_action=True,
        confidence=0.96,
        default_followup=None,
        rationale_prefix="Dashboard/UI command detected",
        match_phrases=("open dashboard", "pull up", "show me", "bring up", "war room", "dashboard"),
        signal_phrases=("dashboard", "sales", "open", "pull up", "war room"),
    ),
    _RouteSpec(
        intent="sales_analysis",
        target_agent="titan",
        task_type="sync_analytics",
        capability="sync_analytics",
        domain="analytics",
        executor="titan",
        risk="low",
        supports_auth=False,
        supports_full_autonomy=True,
        visibility_mode="private",
        fallback_executor="orchestrator",
        capability_family="analytics",
        local_action=False,
        confidence=0.88,
        default_followup=None,
        rationale_prefix="Revenue/sales analysis detected",
        match_phrases=("sales", "revenue", "pipeline", "leads", "forecast", "quota", "deal"),
        signal_phrases=("sales", "revenue", "pipeline", "leads", "forecast", "deal"),
    ),
    _RouteSpec(
        intent="audit_workflow",
        target_agent="orchestrator",
        task_type="operator_command",
        capability="operator_command",
        domain="workflow",
        executor="orchestrator",
        risk="medium",
        supports_auth=False,
        supports_full_autonomy=False,
        visibility_mode="audited",
        fallback_executor="hermes",
        capability_family="workflow",
        local_action=False,
        confidence=0.9,
        default_followup="Which agent and time window should I audit?",
        rationale_prefix="Audit request detected",
        match_phrases=("audit", "review titan", "last three hours", "last 3 hours"),
        signal_phrases=("audit", "workflow", "titan", "hours", "review"),
    ),
    _RouteSpec(
        intent="research_and_apply",
        target_agent="deerflow_research",
        task_type="evolution_research_cycle",
        capability="evolution_research_cycle",
        domain="research",
        executor="deerflow_research",
        risk="medium",
        supports_auth=False,
        supports_full_autonomy=True,
        visibility_mode="audited",
        fallback_executor="orchestrator",
        capability_family="research.*",
        local_action=False,
        confidence=0.87,
        default_followup="What topic and recency window should I research?",
        rationale_prefix="Research and apply request detected",
        match_phrases=("research", "papers", "paper", "apply them", "study", "latest ai"),
        signal_phrases=("research", "papers", "apply", "ai", "days"),
    ),
    _RouteSpec(
        intent="evolution_research",
        target_agent="deerflow_research",
        task_type="evolution_research_cycle",
        capability="evolution_research_cycle",
        domain="research",
        executor="deerflow_research",
        risk="medium",
        supports_auth=False,
        supports_full_autonomy=True,
        visibility_mode="audited",
        fallback_executor="orchestrator",
        capability_family="research.*",
        local_action=False,
        confidence=0.94,
        default_followup=None,
        rationale_prefix="Continuous system-evolution research request detected",
        match_phrases=(
            "how should perseus evolve",
            "research how we can be better",
            "new ai papers",
            "stanford papers",
            "arxiv",
            "trending github repos",
            "24 hours of me thinking",
        ),
        signal_phrases=("perseus", "evolve", "stanford", "arxiv", "github", "research"),
    ),
    _RouteSpec(
        intent="desktop_task",
        target_agent="hermes",
        task_type="desktop_task",
        capability="desktop_task",
        domain="desktop",
        executor="hermes",
        risk="low",
        supports_auth=True,
        supports_full_autonomy=True,
        visibility_mode="local",
        fallback_executor="orchestrator",
        capability_family="desktop",
        local_action=True,
        confidence=0.93,
        default_followup="Which desktop app or window should I work on?",
        rationale_prefix="Desktop control request detected",
        match_phrases=("desktop", "window", "app", "bring to front", "focus the app", "launch app"),
        signal_phrases=("desktop", "window", "app", "focus", "launch"),
    ),
    _RouteSpec(
        intent="observe_task",
        target_agent="orchestrator",
        task_type="observe_task",
        capability="observe_task",
        domain="observe",
        executor="orchestrator",
        risk="low",
        supports_auth=False,
        supports_full_autonomy=True,
        visibility_mode="public",
        fallback_executor="hermes",
        capability_family="observe",
        local_action=False,
        confidence=0.82,
        default_followup="What should I observe or monitor?",
        rationale_prefix="Observation or monitoring request detected",
        match_phrases=("observe", "monitor", "watch", "track", "inspect logs", "anomaly"),
        signal_phrases=("observe", "monitor", "watch", "track", "logs"),
    ),
    _RouteSpec(
        intent="system_task",
        target_agent="orchestrator",
        task_type="system_task",
        capability="system_task",
        domain="system",
        executor="orchestrator",
        risk="high",
        supports_auth=True,
        supports_full_autonomy=False,
        visibility_mode="audited",
        fallback_executor="hermes",
        capability_family="system",
        local_action=False,
        confidence=0.91,
        default_followup="Which system area or service should I target?",
        rationale_prefix="System control request detected",
        match_phrases=("system", "service", "daemon", "restart", "shutdown", "settings", "config"),
        signal_phrases=("system", "service", "daemon", "restart", "shutdown", "settings", "config"),
    ),
    _RouteSpec(
        intent="browser_task",
        target_agent="clawdbot",
        task_type="browser_task",
        capability="browser_task",
        domain="browser",
        executor="clawdbot",
        risk="medium",
        supports_auth=True,
        supports_full_autonomy=True,
        visibility_mode="visible",
        fallback_executor="orchestrator",
        capability_family="browser",
        local_action=False,
        confidence=0.95,
        default_followup="What site or browser objective should I work on?",
        rationale_prefix="Browser or web-execution request detected",
        match_phrases=("browser", "click", "download", "image", "picture", "photo", "website", "web", "navigate", "scrape"),
        signal_phrases=("browser", "download", "pictures", "images", "click", "scrape", "website"),
    ),
    _RouteSpec(
        intent="workflow_task",
        target_agent="orchestrator",
        task_type="workflow_task",
        capability="workflow_task",
        domain="workflow",
        executor="orchestrator",
        risk="medium",
        supports_auth=False,
        supports_full_autonomy=False,
        visibility_mode="audited",
        fallback_executor="hermes",
        capability_family="workflow",
        local_action=False,
        confidence=0.9,
        default_followup="Which workflow should I operate on?",
        rationale_prefix="Workflow automation request detected",
        match_phrases=("workflow", "automation", "pipeline", "n8n", "runbook", "sequence"),
        signal_phrases=("workflow", "automation", "pipeline", "n8n", "runbook", "sequence"),
    ),
    _RouteSpec(
        intent="repo_task",
        target_agent="ruflo",
        task_type="repo_task",
        capability="repo_task",
        domain="repo",
        executor="ruflo",
        risk="medium",
        supports_auth=True,
        supports_full_autonomy=False,
        visibility_mode="audited",
        fallback_executor="orchestrator",
        capability_family="repo",
        local_action=False,
        confidence=0.92,
        default_followup="Which repository or code area should I target?",
        rationale_prefix="Repository request detected",
        match_phrases=("repo", "repository", "git", "commit", "branch", "pull request", "codebase"),
        signal_phrases=("repo", "repository", "git", "commit", "branch", "pull request", "codebase"),
    ),
    _RouteSpec(
        intent="code_task",
        target_agent="ruflo",
        task_type="code_fix",
        capability="code_fix",
        domain="repo",
        executor="ruflo",
        risk="medium",
        supports_auth=True,
        supports_full_autonomy=False,
        visibility_mode="audited",
        fallback_executor="orchestrator",
        capability_family="repo",
        local_action=False,
        confidence=0.92,
        default_followup="What code area should I work on?",
        rationale_prefix="Code or repository request detected",
        match_phrases=("code", "bug", "fix", "repo", "repository", "test", "refactor", "implement", "pull request"),
        signal_phrases=("code", "bug", "fix", "repo", "test", "refactor", "implement"),
    ),
    _RouteSpec(
        intent="memory_query",
        target_agent="orchestrator",
        task_type="ask",
        capability="ask",
        domain="memory",
        executor="orchestrator",
        risk="low",
        supports_auth=False,
        supports_full_autonomy=True,
        visibility_mode="private",
        fallback_executor="hermes",
        capability_family="memory",
        local_action=False,
        confidence=0.8,
        default_followup=None,
        rationale_prefix="Memory or recall request detected",
        match_phrases=("remember", "memory", "recall", "what do you know", "what do you remember", "knowledge"),
        signal_phrases=("memory", "remember", "recall", "knowledge"),
    ),
    _RouteSpec(
        intent="general_orchestration",
        target_agent="orchestrator",
        task_type="operator_command",
        capability="operator_command",
        domain="meta",
        executor="orchestrator",
        risk="low",
        supports_auth=False,
        supports_full_autonomy=False,
        visibility_mode="visible",
        fallback_executor="orchestrator",
        capability_family="meta",
        local_action=False,
        confidence=0.55,
        default_followup="What should I route this to?",
        rationale_prefix="Broad orchestration fallback",
        match_phrases=(),
        signal_phrases=(),
    ),
)

_INTENT_SPECS: dict[str, _RouteSpec] = {spec.intent: spec for spec in _ROUTE_REGISTRY}


def route_kirito_command(
    text: str,
    *,
    context: Mapping[str, Any] | None = None,
    hooks: KiritoRoutingHooks | None = None,
) -> KiritoRoutePlan:
    """Route a freeform Kirito command into a structured execution plan."""
    source_text = _normalize_text(text)
    if not source_text:
        raise ValueError("Command cannot be empty.")

    context_map = dict(context or {})
    detected_intent, signals = _detect_intent(source_text, context_map)
    resolved_intent = _apply_intent_override(detected_intent, source_text, context_map, hooks)
    spec = _INTENT_SPECS.get(resolved_intent, _INTENT_SPECS["general_orchestration"])

    payload = _build_payload(spec.intent, source_text, signals, context_map)
    plan = KiritoRoutePlan(
        source_text=source_text,
        intent=spec.intent,
        target_agent=spec.target_agent,
        task_type=spec.task_type,
        capability=spec.capability,
        domain=spec.domain,
        executor=spec.executor,
        risk=spec.risk,
        supports_auth=spec.supports_auth,
        supports_full_autonomy=spec.supports_full_autonomy,
        visibility_mode=spec.visibility_mode,
        fallback_executor=spec.fallback_executor,
        capability_family=spec.capability_family,
        rationale=_build_rationale(spec, signals, payload, context_map),
        requires_followup=_requires_followup(spec.intent, payload, source_text),
        local_action=spec.local_action,
        followup_question=_followup_question(spec.intent, payload, spec.default_followup),
        payload=payload,
        signals=signals,
        confidence=spec.confidence,
    )

    if hooks and hooks.agent_override:
        override_agent = hooks.agent_override(plan, source_text, context_map)
        if override_agent:
            plan = replace(plan, target_agent=override_agent)

    if hooks and hooks.payload_enricher:
        extras = hooks.payload_enricher(dict(plan.payload or {}), plan)
        if extras:
            merged = dict(plan.payload or {})
            merged.update(dict(extras))
            plan = replace(plan, payload=merged)

    if hooks and hooks.plan_override:
        overridden = hooks.plan_override(plan, source_text, context_map)
        if overridden is not None:
            plan = overridden

    return plan


def _apply_intent_override(
    detected_intent: str,
    source_text: str,
    context: Mapping[str, Any],
    hooks: KiritoRoutingHooks | None,
) -> str:
    if not hooks or not hooks.intent_override:
        return detected_intent

    override = hooks.intent_override(source_text, context)
    if not override:
        return detected_intent

    normalized = _normalize_intent(override)
    return normalized if normalized in _INTENT_SPECS else detected_intent


def _detect_intent(source_text: str, context: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    text = source_text.lower()
    best_match: tuple[int, int, _RouteSpec] | None = None

    for index, spec in enumerate(_ROUTE_REGISTRY):
        match_count = _count_matches(text, spec.match_phrases)
        if match_count <= 0:
            continue
        score = (match_count, -index)
        if best_match is None or score > (best_match[0], best_match[1]):
            best_match = (match_count, -index, spec)

    if best_match is not None:
        spec = best_match[2]
        return spec.intent, _collect_signals(text, spec.signal_phrases)

    if context.get("intent_hint"):
        hint = _normalize_intent(str(context["intent_hint"]))
        if hint in _INTENT_SPECS:
            return hint, (f"intent_hint:{hint}",)

    return "general_orchestration", ()


def _build_payload(
    intent: str,
    source_text: str,
    signals: tuple[str, ...],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_text": source_text,
        "normalized_text": source_text.lower(),
        "matched_signals": list(signals),
        "intent": intent,
    }
    if context:
        payload["context"] = dict(context)

    if intent == "open_dashboard":
        payload["dashboard"] = _infer_dashboard(source_text, context)
        payload["local_action"] = True
    elif intent == "audit_workflow":
        payload["subject_agent"] = _infer_audit_subject(source_text, context)
        payload["time_window_hours"] = _extract_time_window(source_text, "hours")
        payload["audit_scope"] = "workflow"
    elif intent == "research_and_apply":
        payload["topic"] = _infer_research_topic(source_text, context)
        payload["time_window_days"] = _extract_time_window(source_text, "days")
        payload["desired_outcome"] = "apply" if "apply" in source_text.lower() else "research"
        payload["preferred_local_model"] = "local-heavy"
        payload["preferred_inference_lane"] = "airllm"
    elif intent == "desktop_task":
        payload["desktop_goal"] = source_text
        payload["desktop_surface"] = _infer_desktop_surface(source_text, context)
    elif intent == "system_task":
        payload["system_goal"] = source_text
        payload["system_scope"] = _infer_system_scope(source_text, context)
    elif intent == "browser_task":
        payload["requested_count"] = _extract_requested_count(source_text)
        payload["browser_goal"] = source_text
    elif intent == "workflow_task":
        payload["workflow_goal"] = source_text
        payload["workflow_kind"] = _infer_workflow_kind(source_text, context)
    elif intent == "repo_task":
        payload["repo_goal"] = source_text
        payload["repo_scope"] = _infer_repo_scope(source_text, context)
    elif intent == "code_task":
        payload["code_area"] = _infer_code_area(source_text, context)
    elif intent == "memory_query":
        payload["query"] = source_text
        payload["preferred_local_model"] = "local-heavy"
        payload["preferred_inference_lane"] = "airllm"
    elif intent == "observe_task":
        payload["observation_goal"] = source_text
        payload["observation_scope"] = _infer_observation_scope(source_text, context)
        if payload["observation_scope"] in {"screen", "memory", "logs"}:
            payload["preferred_local_model"] = "local-heavy"
            payload["preferred_inference_lane"] = "airllm"
    elif intent == "sales_analysis":
        payload["topic"] = "sales"

    return payload


def _build_rationale(
    spec: _RouteSpec,
    signals: tuple[str, ...],
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    if spec.intent == "open_dashboard":
        dashboard = payload.get("dashboard", "dashboard")
        return f"{spec.rationale_prefix}: opening the {dashboard} surface through Hermes."
    if spec.intent == "audit_workflow":
        subject = payload.get("subject_agent", "target agent")
        window = payload.get("time_window_hours", "recent")
        return f"{spec.rationale_prefix}: inspect {subject} activity over the last {window} hours."
    if spec.intent == "research_and_apply":
        topic = payload.get("topic", "the requested topic")
        window = payload.get("time_window_days", "recent")
        return f"{spec.rationale_prefix}: gather and apply recent research on {topic} from the last {window} days."
    if spec.intent == "browser_task":
        count = payload.get("requested_count")
        extra = f" and extract {count} items" if count else ""
        return f"{spec.rationale_prefix}: delegate browser work to ClawdBot{extra}."
    if spec.intent == "desktop_task":
        surface = payload.get("desktop_surface", "desktop")
        return f"{spec.rationale_prefix}: manipulate the {surface} surface through Hermes."
    if spec.intent == "system_task":
        scope = payload.get("system_scope", "system")
        return f"{spec.rationale_prefix}: apply an operator-safe change to the {scope} layer."
    if spec.intent == "workflow_task":
        workflow_kind = payload.get("workflow_kind", "workflow")
        return f"{spec.rationale_prefix}: route {workflow_kind} automation through the orchestrator."
    if spec.intent == "repo_task":
        scope = payload.get("repo_scope", "repository")
        return f"{spec.rationale_prefix}: delegate {scope} work to Ruflo."
    if spec.intent == "code_task":
        area = payload.get("code_area", "the codebase")
        return f"{spec.rationale_prefix}: route code work to Ruflo for {area}."
    if spec.intent == "memory_query":
        return f"{spec.rationale_prefix}: answer from shared memory and operator context."
    if spec.intent == "observe_task":
        scope = payload.get("observation_scope", "system")
        return f"{spec.rationale_prefix}: monitor the {scope} surface and report back."
    if spec.intent == "sales_analysis":
        return f"{spec.rationale_prefix}: Titan owns sales and revenue analysis."
    if context.get("intent_hint"):
        return f"{spec.rationale_prefix}: overridden by intent hint."
    return f"{spec.rationale_prefix}: no specialized signal was strong enough to choose a narrower executor."


def _requires_followup(intent: str, payload: Mapping[str, Any], source_text: str) -> bool:
    text = source_text.lower()
    vague = any(word in text for word in ("help me", "something", "stuff", "whatever", "thing", "do it"))

    if intent == "audit_workflow":
        return not bool(payload.get("subject_agent")) or not bool(payload.get("time_window_hours"))
    if intent == "research_and_apply":
        return not bool(payload.get("topic")) or not bool(payload.get("time_window_days"))
    if intent == "browser_task":
        return not bool(payload.get("browser_goal")) and not bool(payload.get("requested_count"))
    if intent == "desktop_task":
        return not bool(payload.get("desktop_goal"))
    if intent == "system_task":
        return not bool(payload.get("system_goal"))
    if intent == "workflow_task":
        return not bool(payload.get("workflow_goal"))
    if intent == "repo_task":
        return not bool(payload.get("repo_goal"))
    if intent == "code_task":
        return not bool(payload.get("code_area"))
    if intent == "observe_task":
        return not bool(payload.get("observation_goal"))
    if intent == "general_orchestration":
        return True
    return vague and intent not in {"open_dashboard", "sales_analysis", "memory_query"}


def _followup_question(intent: str, payload: Mapping[str, Any], default: str | None) -> str | None:
    if intent == "audit_workflow" and (not payload.get("subject_agent") or not payload.get("time_window_hours")):
        return "Which agent and time window should I audit?"
    if intent == "research_and_apply" and (not payload.get("topic") or not payload.get("time_window_days")):
        return "What topic and recency window should I research?"
    if intent == "browser_task" and not payload.get("browser_goal"):
        return "What should I do in the browser?"
    if intent == "desktop_task" and not payload.get("desktop_goal"):
        return "Which desktop app or window should I work on?"
    if intent == "system_task" and not payload.get("system_goal"):
        return "Which system area or service should I target?"
    if intent == "workflow_task" and not payload.get("workflow_goal"):
        return "Which workflow should I operate on?"
    if intent == "repo_task" and not payload.get("repo_goal"):
        return "Which repository or code area should I target?"
    if intent == "code_task" and not payload.get("code_area"):
        return "What code area should I work on?"
    if intent == "observe_task" and not payload.get("observation_goal"):
        return "What should I observe or monitor?"
    return default


def _infer_dashboard(source_text: str, context: Mapping[str, Any]) -> str:
    lower = source_text.lower()
    if "sales" in lower or "revenue" in lower or context.get("dashboard") == "sales":
        return "sales"
    if "pipeline" in lower:
        return "pipeline"
    if "health" in lower or "status" in lower:
        return "health"
    return str(context.get("dashboard") or "main")


def _infer_audit_subject(source_text: str, context: Mapping[str, Any]) -> str:
    lower = source_text.lower()
    if "titan" in lower:
        return "titan"
    if "clawdbot" in lower:
        return "clawdbot"
    if "hermes" in lower:
        return "hermes"
    if "orchestrator" in lower or "openjarvis" in lower:
        return "orchestrator"
    return str(context.get("subject_agent") or "titan")


def _infer_research_topic(source_text: str, context: Mapping[str, Any]) -> str:
    lower = source_text.lower()
    if "ai papers" in lower:
        return "new ai papers"
    if "paper" in lower:
        return "papers"
    if context.get("topic"):
        return str(context["topic"])
    return "research"


def _infer_code_area(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("code_area"):
        return str(context["code_area"])
    lower = source_text.lower()
    if "router" in lower:
        return "command router"
    if "dashboard" in lower:
        return "dashboard"
    if "voice" in lower:
        return "voice bridge"
    return "codebase"


def _infer_desktop_surface(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("desktop_surface"):
        return str(context["desktop_surface"])
    lower = source_text.lower()
    if "terminal" in lower:
        return "terminal"
    if "browser" in lower:
        return "browser"
    if "window" in lower:
        return "window"
    if "app" in lower:
        return "app"
    return "desktop"


def _infer_system_scope(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("system_scope"):
        return str(context["system_scope"])
    lower = source_text.lower()
    if "service" in lower:
        return "service"
    if "daemon" in lower:
        return "daemon"
    if "config" in lower or "settings" in lower:
        return "config"
    return "system"


def _infer_workflow_kind(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("workflow_kind"):
        return str(context["workflow_kind"])
    lower = source_text.lower()
    if "n8n" in lower:
        return "n8n"
    if "pipeline" in lower:
        return "pipeline"
    if "automation" in lower:
        return "automation"
    return "workflow"


def _infer_repo_scope(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("repo_scope"):
        return str(context["repo_scope"])
    lower = source_text.lower()
    if "github" in lower:
        return "github"
    if "git" in lower or "commit" in lower or "branch" in lower:
        return "git"
    if "codebase" in lower:
        return "codebase"
    return "repository"


def _infer_observation_scope(source_text: str, context: Mapping[str, Any]) -> str:
    if context.get("observation_scope"):
        return str(context["observation_scope"])
    lower = source_text.lower()
    if "logs" in lower:
        return "logs"
    if "status" in lower:
        return "status"
    if "anomaly" in lower:
        return "anomalies"
    return "system"


def _extract_time_window(source_text: str, unit: str) -> int | None:
    lower = source_text.lower()
    pattern = rf"(?:last|past|previous|for the last|for the past)\s+([a-z0-9\- ]+?)\s+{unit}"
    match = re.search(pattern, lower)
    if match:
        return _parse_number_phrase(match.group(1))

    alt = rf"([a-z0-9\- ]+?)\s+{unit}s?"
    for phrase in re.findall(alt, lower):
        value = _parse_number_phrase(phrase)
        if value is not None:
            return value
    return None


def _extract_requested_count(source_text: str) -> int | None:
    lower = source_text.lower()
    match = re.search(r"(\d+)\s+(?:pictures?|images?|photos?|files?)", lower)
    if match:
        return int(match.group(1))

    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b\s+(?:pictures?|images?|photos?|files?)", lower):
            return value
    return None


def _parse_number_phrase(phrase: str) -> int | None:
    cleaned = phrase.strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)

    total = 0
    matched = False
    for token in re.split(r"[\s-]+", cleaned):
        if token.isdigit():
            total += int(token)
            matched = True
        elif token in _NUMBER_WORDS:
            total += _NUMBER_WORDS[token]
            matched = True
    return total if matched else None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_phrase_matches(text, phrase) for phrase in phrases)


def _count_matches(text: str, phrases: tuple[str, ...]) -> int:
    return sum(1 for phrase in phrases if _phrase_matches(text, phrase))


def _phrase_matches(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase)
    pattern = rf"(?<!\w){escaped}(?!\w)"
    return re.search(pattern, text) is not None


def _collect_signals(text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if phrase in text)


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _normalize_intent(intent: str) -> str:
    return _normalize_text(intent).lower().replace(" ", "_").replace("-", "_")


__all__ = ["KiritoRoutePlan", "KiritoRoutingHooks", "route_kirito_command"]
