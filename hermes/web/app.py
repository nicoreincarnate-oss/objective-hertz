"""
Perseus Web Dashboard — pipeline, revenue, leads, system health.
Simple FastAPI + Jinja2 templates.
"""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared.db import fetch_all, fetch_one, fetch_val, get_config

app = FastAPI(title="Perseus Dashboard")

_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    # Pipeline summary
    pipeline = await fetch_all(
        """SELECT status, COUNT(*) as count FROM clients
           GROUP BY status ORDER BY count DESC"""
    )

    # Revenue
    total_revenue = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
    ) or 0
    pending_revenue = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'pending'"
    ) or 0

    # Email stats
    emails_today = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    emails_week = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date > CURRENT_DATE - 7"
    ) or 0

    # Totals
    total_leads = await fetch_val("SELECT COUNT(*) FROM clients") or 0
    interested = await fetch_val("SELECT COUNT(*) FROM clients WHERE status = 'interested'") or 0
    closed = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status IN ('closed','building','deployed','invoiced','paid')"
    ) or 0

    # Review queue
    pending_review = await fetch_val(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
    ) or 0

    # System status
    review_mode = await get_config("review_mode", True)
    sales_completed = await get_config("sales_completed", 0)

    # Recent events
    events = await fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT 20"
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "pipeline": {row["status"]: row["count"] for row in pipeline},
        "total_revenue": total_revenue,
        "pending_revenue": pending_revenue,
        "emails_today": emails_today,
        "emails_week": emails_week,
        "total_leads": total_leads,
        "interested": interested,
        "closed": closed,
        "pending_review": pending_review,
        "review_mode": review_mode,
        "sales_completed": sales_completed,
        "events": events,
    })


@app.get("/api/pipeline")
async def api_pipeline():
    """Pipeline data as JSON."""
    pipeline = await fetch_all(
        "SELECT status, COUNT(*) as count FROM clients GROUP BY status"
    )
    return JSONResponse({row["status"]: row["count"] for row in pipeline})


@app.get("/api/leads")
async def api_leads():
    """Recent leads."""
    leads = await fetch_all(
        """SELECT id, business_name, email, industry, status, lead_score, created_at
           FROM clients ORDER BY created_at DESC LIMIT 50"""
    )
    return JSONResponse([dict(l) for l in leads], default=str)


@app.get("/api/events")
async def api_events():
    """Recent events."""
    events = await fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT 50"
    )
    return JSONResponse([dict(e) for e in events], default=str)


@app.get("/api/health")
async def api_health():
    """System health."""
    db_ok = True
    db_error = ""
    try:
        await fetch_val("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    from perseus.agent_registry import check_agent_health
    agents = await check_agent_health()
    agent_values = list((agents or {}).values()) if isinstance(agents, dict) else []
    agents_ok = bool(agent_values) and all(status == "ok" for status in agent_values)
    status = "ok" if db_ok and agents_ok else "degraded"
    return JSONResponse({
        "status": status,
        "db_ok": db_ok,
        "db_error": db_error,
        "agents": agents,
    })
