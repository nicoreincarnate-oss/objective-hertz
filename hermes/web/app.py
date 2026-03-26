"""
Perseus Web Dashboard — pipeline, revenue, leads, system health.
Simple FastAPI + Jinja2 templates.

Auth: DASHBOARD_SECRET env var is REQUIRED. Without it, the dashboard
refuses all requests (fail-closed).

Login: POST /login with the secret to get an HttpOnly session cookie.
API clients can use the Authorization: Bearer <secret> header instead.
Tokens are NEVER accepted from query parameters (they leak into logs,
browser history, Referer headers, and analytics).
"""

import hashlib
import hmac
import json as _json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from hermes.web.operator_chat import create_operator_dispatch
from hermes.web.presenter import build_dashboard_view_model
from shared import db
from shared.db import emit_event, fetch_all, fetch_val, get_config, insert_task
from shared.observability import (
    configure_service_observability,
    prometheus_content_type,
    render_prometheus_metrics,
)

logger = logging.getLogger("hermes.web")
_PUBLIC_PATHS = {"/api/liveness", "/metrics", "/login"}
_SESSION_COOKIE = "perseus_session"
# HMAC key for signing session cookies — random per process, so a restart
# invalidates all sessions (acceptable for a single-operator dashboard).
_COOKIE_SIGNING_KEY = secrets.token_bytes(32)


def _get_dashboard_secret() -> str:
    """Read secret at call time so tests and env changes take effect without reimport."""
    return os.getenv("DASHBOARD_SECRET", "").strip()


def _sign_session(secret: str) -> str:
    """Create an HMAC-signed session token from the dashboard secret."""
    return hmac.new(_COOKIE_SIGNING_KEY, secret.encode(), hashlib.sha256).hexdigest()


def _verify_session(cookie_value: str, secret: str) -> bool:
    """Verify a session cookie was signed by us for the current secret."""
    expected = _sign_session(secret)
    return hmac.compare_digest(cookie_value, expected)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB pool on startup, close on shutdown."""
    configure_service_observability("hermes-web", start_metrics_server_for_service=False)
    await db.init_pool()
    yield
    await db.close_pool()


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Auth middleware. Requires DASHBOARD_SECRET to be set (fail-closed).

    Accepts auth via:
    1. Session cookie (set by POST /login) — for browser access
    2. Authorization: Bearer <secret> header — for API clients

    NEVER accepts tokens from query parameters.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        secret = _get_dashboard_secret()

        # Fail-closed: no secret configured → block everything except health/metrics
        if not secret:
            if request.url.path in _PUBLIC_PATHS:
                return await call_next(request)
            logger.error("DASHBOARD_SECRET not set — blocking dashboard access")
            return JSONResponse(
                {"error": "Dashboard disabled: DASHBOARD_SECRET not configured"},
                status_code=503,
            )

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Allow static files through (CSS, JS, etc.)
        if request.url.path.startswith("/static"):
            return await call_next(request)

        # 1. Check session cookie
        session_cookie = request.cookies.get(_SESSION_COOKIE, "")
        if session_cookie and _verify_session(session_cookie, secret):
            return await call_next(request)

        # 2. Check Authorization header (for API clients)
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if token and hmac.compare_digest(token, secret):
                return await call_next(request)

        # No valid auth — redirect browsers to login, return 401 for API
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)


app = FastAPI(title="Perseus Dashboard", lifespan=lifespan)
app.add_middleware(TokenAuthMiddleware)

_DIR = Path(__file__).parent

# Lazy template initialization — avoids hard crash when jinja2 is not
# installed (e.g. in test environments that only exercise API routes).
_templates = None


def _get_templates():
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=str(_DIR / "templates"))
    return _templates


try:
    app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")
except Exception:
    pass  # Static dir may not exist in test environments


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Minimal login form."""
    return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Perseus — Login</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#111;color:#eee}
form{background:#1a1a1a;padding:2rem;border-radius:8px;min-width:300px}
input{width:100%;padding:8px;margin:8px 0;box-sizing:border-box;border:1px solid #333;border-radius:4px;background:#222;color:#eee}
button{width:100%;padding:10px;background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:1rem}
button:hover{background:#1d4ed8}.err{color:#ef4444;font-size:0.85rem}</style></head>
<body><form method="POST" action="/login"><h2>Perseus Dashboard</h2>
<input type="password" name="secret" placeholder="Dashboard secret" required autocomplete="current-password">
<button type="submit">Login</button></form></body></html>""")


@app.post("/login")
async def login_submit(secret: str = Form(...)):
    """Validate secret and set a signed session cookie."""
    dashboard_secret = _get_dashboard_secret()
    if not dashboard_secret:
        return JSONResponse({"error": "Dashboard disabled"}, status_code=503)

    if not hmac.compare_digest(secret, dashboard_secret):
        return HTMLResponse(
            '<html><body style="font-family:system-ui;display:flex;justify-content:center;'
            'align-items:center;height:100vh;margin:0;background:#111;color:#eee">'
            '<div style="text-align:center"><p class="err" style="color:#ef4444">Invalid secret</p>'
            '<a href="/login" style="color:#60a5fa">Try again</a></div></body></html>',
            status_code=401,
        )

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=_sign_session(dashboard_secret),
        httponly=True,
        samesite="lax",
        secure=os.getenv("ENVIRONMENT", "development") != "development",
        max_age=86400,  # 24 hours
    )
    return response


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

    return _get_templates().TemplateResponse(
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
    priority: str = Form("routine"),
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
    # Import lazily so the dashboard can start even if LLM-only deps
    # are unavailable or this endpoint is not exercised.
    from hermes.web.insights import answer_strategic_question

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
    return JSONResponse(content=[dict(lead) for lead in leads])


@app.get("/api/ruflo")
async def api_ruflo():
    """Ruflo engineering agent status and recent activity."""
    from shared.config import config as _cfg
    if not _cfg.ruflo.enabled:
        return JSONResponse(content={"enabled": False})

    recent_tasks = await fetch_all(
        "SELECT id, task_type, source, status, validation_status, confidence, "
        "claude_cost, duration_seconds, created_at, completed_at "
        "FROM ruflo_tasks ORDER BY created_at DESC LIMIT 20"
    )
    stats = {
        "total": await fetch_val("SELECT COUNT(*) FROM ruflo_tasks") or 0,
        "validated": await fetch_val(
            "SELECT COUNT(*) FROM ruflo_tasks WHERE validation_status = 'passed'"
        ) or 0,
        "rejected": await fetch_val(
            "SELECT COUNT(*) FROM ruflo_tasks WHERE validation_status = 'failed'"
        ) or 0,
        "pending": await fetch_val(
            "SELECT COUNT(*) FROM ruflo_tasks WHERE status IN ('pending', 'dispatched', 'running')"
        ) or 0,
        "month_claude_spend": float(await fetch_val(
            "SELECT COALESCE(SUM(claude_cost), 0) FROM ruflo_tasks "
            "WHERE created_at > DATE_TRUNC('month', NOW())"
        ) or 0),
        "claude_cap": _cfg.ruflo.claude_monthly_cap,
        "patterns_learned": await fetch_val("SELECT COUNT(*) FROM ruflo_patterns") or 0,
    }
    if stats["total"] > 0:
        stats["success_rate"] = round(stats["validated"] / stats["total"] * 100, 1)
    else:
        stats["success_rate"] = 0.0

    return JSONResponse(content={
        "enabled": True,
        "stats": stats,
        "recent_tasks": [dict(t) for t in recent_tasks],
    })


@app.get("/api/events")
async def api_events():
    """Recent events."""
    events = await fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT 50"
    )
    return JSONResponse(content=[dict(e) for e in events])


@app.get("/api/liveness")
async def api_liveness():
    """Public liveness probe — returns only up/down status, no business data."""
    db_ok = True
    try:
        await fetch_val("SELECT 1")
    except Exception:
        db_ok = False
    return JSONResponse({"status": "ok" if db_ok else "degraded", "db_ok": db_ok})


@app.get("/api/health")
async def api_health():
    """Authenticated system health — includes operator telemetry and business metrics."""
    db_ok = True
    db_error = ""
    try:
        await fetch_val("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    try:
        from openjarvis.vassals.registry import check_agent_health
        agents = await check_agent_health()
    except Exception as exc:
        logger.warning(f"Agent health check failed: {exc}")
        agents = {}
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


@app.get("/metrics")
async def api_metrics():
    """Prometheus metrics for the Hermes dashboard process."""
    return PlainTextResponse(
        render_prometheus_metrics().decode("utf-8"),
        media_type=prometheus_content_type(),
    )
