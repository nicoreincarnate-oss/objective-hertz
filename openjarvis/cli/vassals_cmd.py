"""CLI commands for vassal management — discover, status, control, query.

jarvis vassals              — list all vassals + health
jarvis vassals discover     — re-scan for vassals
jarvis vassals health       — detailed health check
jarvis vassals restart <n>  — restart a vassal
jarvis vassals pipeline     — pipeline status via Titan
jarvis vassals budget       — budget status via Titan
jarvis vassals decisions    — recent autonomous decisions
jarvis vassals briefing     — generate morning briefing via Hermes
jarvis vassals ask <vassal> <question>  — send natural language to a vassal
"""

from __future__ import annotations

import json

import click


def _get_vassals_config() -> dict:
    """Load vassal config from OpenJarvis config."""
    try:
        from openjarvis.core.config import load_config
        cfg = load_config()
        return getattr(cfg, "vassals", {})
    except Exception:
        # Fallback defaults
        return {
            "titan": {"url": "http://localhost:9001"},
            "hermes": {"url": "http://localhost:9002"},
            "clawdbot": {"url": "http://localhost:9003"},
        }


def _get_discovery():
    """Create a VassalDiscovery instance."""
    from openjarvis.core.events import get_event_bus
    from openjarvis.vassals.discovery import VassalDiscovery

    bus = get_event_bus()
    config = _get_vassals_config()
    discovery = VassalDiscovery(bus=bus, config=config)
    discovery.discover_all()
    return discovery


@click.group("vassals", help="Manage Perseus vassals (Titan, Hermes, ClawdBot)")
def vassals():
    pass


@vassals.command("list")
def vassals_list():
    """List all configured vassals and their status."""
    discovery = _get_discovery()
    summary = discovery.summary()

    if not summary:
        click.echo("No vassals configured.")
        return

    click.echo()
    click.echo("  VASSAL        STATUS    CAPABILITIES  URL")
    click.echo("  " + "─" * 65)
    for name, info in summary.items():
        status = click.style("● healthy", fg="green") if info["healthy"] else click.style("● down", fg="red")
        caps = str(info["capabilities"])
        url = info["url"]
        desc = info.get("description", "")[:40]
        click.echo(f"  {name:<12}  {status}  {caps:>5} caps     {url}")
        if desc:
            click.echo(f"                {desc}")
    click.echo()


@vassals.command("discover")
def vassals_discover():
    """Re-scan for vassals."""
    discovery = _get_discovery()
    click.echo("Scanning for vassals...")
    for name, vassal in discovery.vassals.items():
        if vassal.healthy:
            click.echo(click.style(f"  ✓ {name}", fg="green") + f" — {len(vassal.capabilities)} capabilities")
        else:
            click.echo(click.style(f"  ✗ {name}", fg="red") + f" — {vassal.last_error[:60]}")


@vassals.command("health")
def vassals_health():
    """Detailed health check of all vassals."""
    discovery = _get_discovery()
    results = discovery.health_check_all()

    for name, status in results.items():
        if status.get("healthy"):
            click.echo(click.style(f"  ● {name}: healthy", fg="green"))
            click.echo(f"    {status.get('capabilities', '?')} capabilities")
            resp = status.get("response", "")[:100]
            if resp:
                click.echo(f"    Response: {resp}")
        else:
            click.echo(click.style(f"  ● {name}: down", fg="red"))
            click.echo(f"    Error: {status.get('error', 'unknown')}")


@vassals.command("restart")
@click.argument("name")
def vassals_restart(name: str):
    """Restart a vassal process."""
    click.echo(f"Restarting {name}... (requires VassalSupervisor running)")
    # This would call the supervisor — for now just document it
    click.echo("Note: Use `jarvis start` with vassals configured to enable process management.")


@vassals.command("pipeline")
def vassals_pipeline():
    """Show pipeline status via Titan."""
    discovery = _get_discovery()
    raw = discovery.call("titan", "pipeline_status")

    try:
        state = json.loads(raw)
    except Exception:
        click.echo(click.style("Failed to get pipeline status from Titan", fg="red"))
        return

    click.echo()
    click.echo("  PIPELINE STATUS")
    click.echo("  " + "─" * 50)

    stages = state.get("stage_counts", {})
    if stages:
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 30)
            click.echo(f"  {stage:<20} {count:>4}  {bar}")
    else:
        click.echo("  No leads in pipeline.")

    click.echo()
    click.echo(f"  Hot leads:        {state.get('hot_leads', 0)}")
    click.echo(f"  Ready to close:   {state.get('ready_to_close', 0)}")
    click.echo(f"  Ready to deliver: {state.get('ready_to_deliver', 0)}")
    click.echo(f"  Ready to invoice: {state.get('ready_to_invoice', 0)}")
    click.echo(f"  Top of funnel:    {state.get('top_of_funnel', 0)}")
    click.echo(f"  Active outreach:  {state.get('outreach_active', 0)}")
    click.echo(f"  Recent errors:    {state.get('recent_errors', 0)}")
    click.echo(f"  Pending reviews:  {state.get('pending_reviews', 0)}")
    click.echo()


@vassals.command("budget")
def vassals_budget():
    """Show budget status via Titan."""
    discovery = _get_discovery()
    raw = discovery.call("titan", "budget_status")

    try:
        budget = json.loads(raw)
    except Exception:
        click.echo(click.style("Failed to get budget status from Titan", fg="red"))
        return

    click.echo()
    click.echo("  BUDGET STATUS")
    click.echo("  " + "─" * 40)
    percent = budget.get("percent_used", 0)
    spent = budget.get("total_spent", 0)
    cap = budget.get("monthly_cap", 800)
    color = "green" if percent < 80 else ("yellow" if percent < 100 else "red")
    click.echo(f"  Spent:   ${spent:.2f} / ${cap:.2f}")
    click.echo("  Used:    " + click.style(f"{percent:.0f}%", fg=color))
    if budget.get("exceeded"):
        click.echo(click.style("  ⚠ BUDGET EXCEEDED — pipeline may be paused", fg="red"))
    click.echo()


@vassals.command("decisions")
@click.option("--limit", "-n", default=10, help="Number of decisions to show")
@click.option("--agent", "-a", default="", help="Filter by agent name")
def vassals_decisions(limit: int, agent: str):
    """Show recent autonomous decisions."""
    discovery = _get_discovery()
    raw = discovery.call("titan", "decisions_query", agent=agent, limit=limit)

    try:
        decisions = json.loads(raw)
    except Exception:
        click.echo(click.style("Failed to get decisions from Titan", fg="red"))
        return

    if not decisions:
        click.echo("No recent decisions.")
        return

    click.echo()
    click.echo("  RECENT DECISIONS")
    click.echo("  " + "─" * 60)
    for d in decisions:
        agent_name = d.get("agent", "?")
        dtype = d.get("decision_type", "?")
        reasoning = d.get("reasoning", "")[:80]
        created = str(d.get("created_at", ""))[:19]
        click.echo(f"  [{created}] {agent_name}/{dtype}")
        if reasoning:
            click.echo(f"    → {reasoning}")
    click.echo()


@vassals.command("briefing")
def vassals_briefing():
    """Generate a briefing via Hermes."""
    discovery = _get_discovery()
    raw = discovery.call("hermes", "briefing_generate")

    try:
        briefing = json.loads(raw)
    except Exception:
        click.echo(click.style("Failed to generate briefing from Hermes", fg="red"))
        return

    click.echo()
    click.echo("  MORNING BRIEFING")
    click.echo("  " + "═" * 50)

    pipeline = briefing.get("pipeline", {})
    if pipeline:
        click.echo()
        click.echo("  Pipeline:")
        click.echo(f"    Hot leads: {pipeline.get('hot_leads', 0)}")
        click.echo(f"    Ready to close: {pipeline.get('ready_to_close', 0)}")
        click.echo(f"    Ready to deliver: {pipeline.get('ready_to_deliver', 0)}")
        click.echo(f"    Errors (1h): {pipeline.get('recent_errors', 0)}")

    budget = briefing.get("budget", {})
    if budget:
        click.echo()
        click.echo(f"  Budget: ${budget.get('total_spent', 0):.2f} / ${budget.get('monthly_cap', 800):.2f} ({budget.get('percent_used', 0):.0f}%)")

    decisions = briefing.get("recent_decisions", [])
    if decisions:
        click.echo()
        click.echo("  Recent decisions:")
        for d in decisions[:5]:
            click.echo(f"    • {d.get('agent', '?')}: {d.get('reasoning', '?')[:60]}")

    click.echo()
    click.echo("  " + "═" * 50)
    click.echo()


@vassals.command("ask")
@click.argument("vassal_name")
@click.argument("question", nargs=-1, required=True)
def vassals_ask(vassal_name: str, question: tuple):
    """Send a natural language question to a vassal."""
    discovery = _get_discovery()
    text = " ".join(question)

    vassal = discovery.get(vassal_name)
    if not vassal:
        click.echo(click.style(f"Unknown vassal: {vassal_name}", fg="red"))
        click.echo(f"Available: {', '.join(discovery.vassals.keys())}")
        return

    if not vassal.healthy:
        click.echo(click.style(f"Vassal {vassal_name} is unhealthy: {vassal.last_error}", fg="red"))
        return

    click.echo(f"Asking {vassal_name}: {text}")
    click.echo()

    try:
        task = vassal.client.send_task(text)
        output = task.output_text
        # Try to pretty-print JSON
        try:
            parsed = json.loads(output)
            click.echo(json.dumps(parsed, indent=2, default=str))
        except Exception:
            click.echo(output)
    except Exception as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"))


@vassals.command("tools")
def vassals_tools():
    """List all tools registered from vassals."""
    discovery = _get_discovery()
    tools = discovery.list_all_tools()

    if not tools:
        click.echo("No vassal tools registered.")
        return

    click.echo()
    click.echo(f"  {len(tools)} vassal tools available:")
    click.echo("  " + "─" * 40)
    for tool in sorted(tools):
        click.echo(f"  • {tool}")
    click.echo()
