"""
Perseus Web Dashboard — pipeline, revenue, leads, system health.
Simple FastAPI + Jinja2 templates.

Auth: Bearer token via DASHBOARD_SECRET env var.
Pass as ?token=<secret> in the URL or Authorization: Bearer <secret> header.
"""

import hmac
import json as _json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from hermes.web.insights import answer_strategic_question
from hermes.web.operator_chat import create_operator_dispatch
from hermes.web.presenter import build_dashboard_view_model
from shared import db
from shared.db import emit_event, fetch_all, fetch_val, get_config, insert_task

_PUBLIC_PATHS = {"/api/health"}  # health check stays unauthenticated for monitoring


def _get_dashboard_secret() -> str:
    """Read secret at call time so tests and env changes take effect without reimport."""
    return os.getenv("DASHBOARD_SECRET", "").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB pool on startup, close on shutdown."""
    await db.init_pool()
    yield
    await db.close_pool()


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Simple bearer-token auth. Token comes from env DASHBOARD_SECRET."""

    async def dispatch(self, request: Request, call_next) -> Response:
        secret = _get_dashboard_secret()
        if not secret:
            # No secret configured — dashboard is open (dev/local only)
            return await call_next(request)

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Allow static files through (CSS, etc.)
        if request.url.path.startswith("/static"):
            return await call_next(request)

        # Check token from query param or Authorization header
        token = request.query_params.get("token", "")
        if not token:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()

        if not token or not hmac.compare_digest(token, secret):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)


app = FastAPI(title="Perseus Dashboard", lifespan=lifespan)
app.add_middleware(TokenAuthMiddleware)

_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main operator dashboard page."""
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

    operator_status = "Message queued for dispatch." if request.query_params.get("operator_sent") == "1" else ""
    operator_error = request.query_params.get("operator_error", "")

    view_model = build_dashboard_view_model(
        pipeline_rows=pipeline,
        total_revenue=float(total_revenue),
        pending_revenue=float(pending_revenue),
        emails_today=int(emails_today),
        emails_week=int(emails_week),
        total_leads=int(total_leads),
        interested=int(interested),
        closed=int(closed),
        pending_review=int(pending_review),
        review_mode=bool(review_mode),
        sales_completed=int(sales_completed),
        events=events,
        operator_status=operator_status,
        operator_error=operator_error,
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            **view_model,
        },
    )


@app.post("/api/operator-chat")
async def operator_chat(
    target_agent: str = Form(...),
    priority: str = Form("priority"),
    message: str = Form(...),
):
    """Queue an operator message for a daemon and record an audit event."""
    try:
        dispatch = create_operator_dispatch(target_agent, message, priority)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/?operator_error={quote(str(exc))}#agent-link",
            status_code=303,
        )

    task_id = await insert_task(
        dispatch["task_type"],
        dispatch["payload"],
        priority=dispatch["priority_value"],
        dedupe=False,
    )
    await emit_event(
        "operator_message_sent",
        {
            "target_agent": dispatch["target_agent"],
            "message": dispatch["payload"]["message"],
            "priority": dispatch["priority"],
            "task_id": task_id,
            "source": "war_room",
        },
    )
    return RedirectResponse(url="/?operator_sent=1#agent-link", status_code=303)


@app.post("/api/insights")
async def api_insights(request: Request):
    """Answer a strategic operator question grounded in pipeline data."""
    payload = await request.json()
    question = str(payload.get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "Question cannot be empty."}, status_code=400)

    try:
        answer = await answer_strategic_question(question)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(answer)


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
    return JSONResponse(content=_json.loads(_json.dumps([dict(lead) for lead in leads], default=str)))


@app.get("/api/events")
async def api_events():
    """Recent events."""
    events = await fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT 50"
    )
    return JSONResponse(content=_json.loads(_json.dumps([dict(e) for e in events], default=str)))


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
    review_mode = await get_config("review_mode", True)
    emails_sent_today = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    emails_sent_week = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date > CURRENT_DATE - 7"
    ) or 0
    warm_leads = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'interested'"
    ) or 0
    sales_closed = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status IN ('closed','building','deployed','invoiced','paid')"
    ) or 0
    pending_approvals = await fetch_val(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
    ) or 0
    revenue_cleared = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
    ) or 0
    revenue_pending = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'pending'"
    ) or 0
    total_leads = await fetch_val("SELECT COUNT(*) FROM clients") or 0

    return JSONResponse({
        "status": status,
        "db_ok": db_ok,
        "db_error": db_error,
        "agents": agents,
        "mode": "review" if review_mode else "autonomous",
        "metrics": {
            "emails_sent_today": int(emails_sent_today),
            "emails_sent_week": int(emails_sent_week),
            "warm_leads": int(warm_leads),
            "sales_closed": int(sales_closed),
            "pending_approvals": int(pending_approvals),
            "revenue_cleared": float(revenue_cleared),
            "revenue_pending": float(revenue_pending),
            "total_leads": int(total_leads),
        },
    })
