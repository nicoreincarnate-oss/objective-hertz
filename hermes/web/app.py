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

import asyncio
import base64
import hashlib
from decimal import Decimal
import hmac
import httpx
import json
import logging
import os
import psycopg
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from hermes.web.jarvis_ws import jarvis_screen_feed, is_enabled as jarvis_feed_enabled
from hermes.web.kirito_router import route_kirito_command
from hermes.web.kirito_runtime import build_status_graph, normalize_dispatch_plan, serialize_route_plan
from hermes.web.operator_chat import create_operator_dispatch
from hermes.web.presenter import build_dashboard_view_model
from shared import db
from shared.db import (
    emit_event,
    execute,
    fetch_all,
    fetch_one,
    fetch_val,
    get_config,
    insert_task,
    set_config,
)
from shared.observability import (
    configure_service_observability,
    get_metrics_summary,
    prometheus_content_type,
    render_prometheus_metrics,
)

logger = logging.getLogger("hermes.web")


# IGUS-FIX: Sanitized error response helper (CWE-209)
def _safe_error(e: Exception, status_code: int = 500, **extra) -> JSONResponse:
    """Return sanitized error response. Log full error internally."""
    logger.error("API error: %s", e, exc_info=True)
    body: dict = {"error": "Internal server error", "error_id": id(e) % 100000}
    body.update(extra)
    return JSONResponse(body, status_code=status_code)


_PUBLIC_PATHS = {"/api/liveness", "/metrics", "/login", "/unsub"}
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


# Phase 29: Memory Explorer API router
try:
    from hermes.web.memory_router import router as memory_router
    app.include_router(memory_router)
except (ImportError, AttributeError) as _mem_exc:
    logger.debug("Memory Explorer router not loaded: %s", _mem_exc)

try:
    app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")
except (OSError, RuntimeError):  # IGUS-FIX: Narrowed exception type (CWE-755)
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
        # IGUS-FIX: Default to Secure=True (CWE-614, AEGIS rule: dev is the exception)
        secure=os.getenv("ENVIRONMENT", "production") != "development",
        max_age=int(os.getenv("SESSION_TTL", "3600")),  # IGUS-FIX: Reduced from 24h to 1h (CWE-613)
    )
    return response


# IGUS-FIX: Added logout endpoint for session invalidation (CWE-613)
@app.post("/api/logout")
async def logout(request: Request):
    """Invalidate session by clearing cookie."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(_SESSION_COOKIE)
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
        # IGUS-FIX: Sanitized error response (CWE-209)
        logger.warning("Operator chat validation error: %s", exc)
        return RedirectResponse(
            url=f"/?operator_error={quote('Invalid dispatch parameters')}#agent-link",
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
    if len(question) > 2000:  # IGUS-FIX: Limit input length (CWE-20)
        return JSONResponse({"error": "Question too long (max 2000 chars)"}, status_code=400)

    try:
        answer = await answer_strategic_question(question)
    except ValueError as exc:
        # IGUS-FIX: Sanitized error response (CWE-209)
        logger.warning("Insights validation error: %s", exc)
        return JSONResponse({"error": "Invalid question parameters"}, status_code=400)

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
    try:
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
    except (psycopg.Error, OSError, ImportError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Ruflo endpoint error (tables may not exist): %s", exc)
        return JSONResponse(content={"enabled": False, "error": "ruflo unavailable"})


@app.get("/api/events")
async def api_events():
    """Recent events."""
    events = await fetch_all(
        "SELECT * FROM events ORDER BY created_at DESC LIMIT 50"
    )
    return JSONResponse(content=[dict(e) for e in events])


@app.get("/api/metrics")
async def api_metrics(request: Request):
    """LLM metrics summary — per-daemon calls, latency, errors, cost.

    Query params:
    - daemon: filter by daemon name (optional)
    - hours: lookback window in hours (default 24)
    """
    daemon = request.query_params.get("daemon")
    try:
        hours = int(request.query_params.get("hours", "24"))
    except (ValueError, TypeError):
        hours = 24
    summary = await get_metrics_summary(daemon=daemon, hours=hours)
    return JSONResponse(content=summary)


@app.get("/api/liveness")
async def api_liveness():
    """Public liveness probe — returns only up/down status, no business data."""
    db_ok = True
    try:
        await fetch_val("SELECT 1")
    except (psycopg.Error, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        db_ok = False
    return JSONResponse({"status": "ok" if db_ok else "degraded", "db_ok": db_ok})


@app.get("/unsub", response_class=HTMLResponse)
async def unsub_endpoint(request: Request):
    """CAN-SPAM unsubscribe endpoint — public, no auth required.

    Validates HMAC signature to prevent enumeration, then marks the
    client as unsubscribed in the database and syncs to Instantly blocklist.
    """
    client_id_raw = request.query_params.get("id")
    sig = request.query_params.get("sig", "")

    if not client_id_raw or not sig:
        return HTMLResponse(
            "<html><body><h1>Invalid Request</h1>"
            "<p>Missing required parameters.</p></body></html>",
            status_code=400,
        )

    try:
        client_id = int(client_id_raw)
    except (ValueError, TypeError):
        return HTMLResponse(
            "<html><body><h1>Invalid Request</h1>"
            "<p>Invalid client identifier.</p></body></html>",
            status_code=400,
        )

    # Validate HMAC signature using the same algorithm as titan/compliance.py
    unsub_secret = os.getenv("UNSUBSCRIBE_SECRET", "")
    if not unsub_secret:
        logger.error("UNSUBSCRIBE_SECRET not configured — cannot process unsubscribe")
        return HTMLResponse(
            "<html><body><h1>Server Error</h1>"
            "<p>Unsubscribe is temporarily unavailable. Please try again later.</p></body></html>",
            status_code=500,
        )

    expected = hmac.new(
        unsub_secret.encode(), str(client_id).encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(sig, expected):
        return HTMLResponse(
            "<html><body><h1>Invalid Link</h1>"
            "<p>This unsubscribe link is invalid or has expired.</p></body></html>",
            status_code=403,
        )

    # Mark client as unsubscribed
    await execute(
        "UPDATE clients SET status = 'unsubscribed', updated_at = NOW() WHERE id = %s",
        (client_id,),
    )

    # Log the unsubscribe event
    await emit_event("client_unsubscribed", {"client_id": client_id, "source": "unsub_link"})
    logger.info("Client %s unsubscribed via /unsub link", client_id)

    # Sync to Instantly suppression list (best-effort)
    try:
        client_row = await fetch_one("SELECT email FROM clients WHERE id = %s", (client_id,))
        if client_row and client_row.get("email"):
            from tools.instantly_client import InstantlyClient

            ic = InstantlyClient()
            await ic._post("/leads/delete", {"email": client_row["email"]})
            await ic.close()
    except (httpx.HTTPError, OSError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Failed to sync unsub to Instantly blocklist: %s", e)

    return HTMLResponse(
        "<html><head><title>Unsubscribed</title></head><body>"
        "<h1>You have been unsubscribed</h1>"
        "<p>You will no longer receive emails from us. "
        "This change takes effect immediately.</p>"
        "</body></html>",
        status_code=200,
    )


@app.get("/api/health")
async def api_health():
    """Authenticated system health — includes operator telemetry and business metrics."""
    db_ok = True
    db_error = ""
    try:
        await fetch_val("SELECT 1")
    except (psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        db_ok = False
        # IGUS-FIX: Sanitized error response (CWE-209) — log full error, expose generic message
        logger.error("DB health check failed: %s", exc, exc_info=True)
        db_error = "database connectivity error"

    try:
        from openjarvis.vassals.registry import check_agent_health
        agents = await check_agent_health()
    except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
async def prometheus_metrics():
    """Prometheus metrics for the Hermes dashboard process."""
    return PlainTextResponse(
        render_prometheus_metrics().decode("utf-8"),
        media_type=prometheus_content_type(),
    )


# ── Phase 1: WebSocket Real-Time Layer ──────────────────────────────────
# Provides instant updates instead of 30-second SWR polling.
# Auth: first message must be {"token": "<DASHBOARD_SECRET>"}.
# Then broadcasts a sync envelope every 5 seconds + pushes new events.

_ws_connections: set[WebSocket] = set()


async def _build_sync_payload() -> dict:
    """Build the full sync envelope from real DB state."""
    try:
        # Pipeline counts
        pipeline_rows = await fetch_all(
            "SELECT status, COUNT(*) as cnt FROM clients GROUP BY status ORDER BY status"
        )
        pipeline = {r["status"]: int(r["cnt"]) for r in pipeline_rows}

        # Recent leads
        leads_rows = await fetch_all(
            "SELECT id, business_name, email, industry, status, lead_score, created_at "
            "FROM clients ORDER BY created_at DESC LIMIT 50"
        )
        leads = []
        for r in leads_rows:
            leads.append({
                "id": r["id"],
                "business_name": r["business_name"],
                "email": r["email"],
                "industry": r.get("industry", ""),
                "status": r["status"],
                "lead_score": r.get("lead_score", 0),
                "created_at": str(r["created_at"]) if r.get("created_at") else None,
            })

        # Recent events
        events_rows = await fetch_all(
            "SELECT id, event_type, payload, created_at, acknowledged "
            "FROM events ORDER BY id DESC LIMIT 30"
        )
        events = []
        for r in events_rows:
            payload = r.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
            events.append({
                "id": r["id"],
                "event_type": r["event_type"],
                "payload": payload,
                "created_at": str(r["created_at"]) if r.get("created_at") else None,
                "acknowledged": r.get("acknowledged", False),
            })

        # Health metrics (reuse the same queries as /api/health)
        from shared.config import config
        review_mode = getattr(config, "review_mode", True)
        emails_today = await fetch_val(
            "SELECT COUNT(*) FROM email_sequences WHERE status IN ('queued','sent') "
            "AND created_at > NOW() - INTERVAL '1 day'"
        ) or 0
        warm_leads = await fetch_val(
            "SELECT COUNT(*) FROM clients WHERE status IN ('interested','demo_built','proposal_sent')"
        ) or 0
        sales_closed = await fetch_val(
            "SELECT COUNT(*) FROM clients WHERE status IN ('closed','invoiced','paid')"
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

        health = {
            "status": "running",
            "mode": "review" if review_mode else "autonomous",
            "metrics": {
                "emails_sent_today": int(emails_today),
                "warm_leads": int(warm_leads),
                "sales_closed": int(sales_closed),
                "pending_approvals": int(pending_approvals),
                "revenue_cleared": float(revenue_cleared),
                "revenue_pending": float(revenue_pending),
            },
        }

        # Task queue summary
        task_rows = await fetch_all(
            "SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status"
        )
        task_summary = {r["status"]: int(r["cnt"]) for r in task_rows}

        # Daemon heartbeats
        daemon_rows = await fetch_all(
            "SELECT agent_name, status, last_heartbeat FROM agent_registry"
        )
        daemons = {}
        for r in daemon_rows:
            name = r["agent_name"]
            paused = await get_config(f"{name}_paused", False)
            daemons[name] = {
                "status": r.get("status", "unknown"),
                "last_heartbeat": str(r["last_heartbeat"]) if r.get("last_heartbeat") else None,
                "paused": bool(paused),
            }

        # Budget summary
        try:
            from shared.config import config as _cfg
            from tools.budget_guard import get_month_spending
            cap = getattr(_cfg, "monthly_cap", 800)
            budget_data = await get_month_spending(Decimal(str(cap)))
            budget = {
                "percent_used": budget_data.get("percent_used", 0),
                "remaining": budget_data.get("remaining", cap),
                "total_spent": budget_data.get("total_spent", 0),
                "exceeded": budget_data.get("exceeded", False),
            }
        except (ImportError, psycopg.Error, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
            budget = {"percent_used": 0, "remaining": 800, "total_spent": 0, "exceeded": False}

        return {
            "type": "sync",
            "health": health,
            "pipeline": pipeline,
            "leads": leads,
            "events": events,
            "task_queue": task_summary,
            "daemons": daemons,
            "budget": budget,
        }
    except (OSError, ValueError, KeyError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("WebSocket sync build failed: %s", e)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return {"type": "sync", "error": "Internal sync error"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Real-time WebSocket for the War Room dashboard.

    Auth protocol:
    1. Client connects
    2. Client sends first message: {"token": "<DASHBOARD_SECRET>"}
    3. If valid, server enters broadcast loop
    4. If invalid, server closes with code 4001
    """
    await ws.accept()

    # Auth: first message must contain the dashboard secret
    secret = _get_dashboard_secret()
    if not secret:
        await ws.close(code=4001, reason="DASHBOARD_SECRET not configured")
        return

    try:
        auth_msg = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        auth_data = json.loads(auth_msg)
        token = auth_data.get("token", "")
        if not hmac.compare_digest(token, secret):
            await ws.close(code=4001, reason="Invalid token")
            return
    except (TimeoutError, json.JSONDecodeError):
        await ws.close(code=4001, reason="Auth timeout or invalid format")
        return

    # Authenticated — add to connection set
    _ws_connections.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_connections))

    try:
        while True:
            # Build and send full sync
            payload = await _build_sync_payload()
            await ws.send_json(payload)

            # Wait 5 seconds, but also listen for client messages (pings, etc.)
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                # Client can send {"type": "ping"} to keep alive
                if msg:
                    data = json.loads(msg)
                    if data.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
            except (TimeoutError, json.JSONDecodeError):
                pass  # Normal — 5-second sync interval elapsed, or malformed client ping

    except WebSocketDisconnect:
        pass
    except (OSError, ConnectionError, RuntimeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("WebSocket error: %s", e)
    finally:
        _ws_connections.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_ws_connections))


# ── Phase 1: Lead Actions ───────────────────────────────────────────────

@app.post("/api/leads/{lead_id}/action")
async def api_lead_action(lead_id: int, request: Request):
    """Inline lead actions: approve, reject, escalate."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action = body.get("action", "")
    if action not in ("approve", "reject", "escalate"):
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)

    lead = await fetch_val("SELECT id FROM clients WHERE id = %s", (lead_id,))
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    if action == "approve":
        await execute(
            "UPDATE review_queue SET status = 'approved' WHERE client_id = %s AND status = 'pending_review'",
            (lead_id,),
        )
        await emit_event("lead_approved", {"lead_id": lead_id, "source": "operator"})
    elif action == "reject":
        await execute(
            "UPDATE review_queue SET status = 'rejected' WHERE client_id = %s AND status = 'pending_review'",
            (lead_id,),
        )
        await emit_event("lead_rejected", {"lead_id": lead_id, "source": "operator"})
    elif action == "escalate":
        await insert_task("operator_escalation", {"lead_id": lead_id}, priority=1)
        await emit_event("lead_escalated", {"lead_id": lead_id, "source": "operator"})

    return JSONResponse({"success": True, "action": action, "lead_id": lead_id})


# ── War Room Control Plane ─────────────────────────────────────────────

_CONFIG_ALLOWLIST = {
    "review_mode", "sales_before_autonomy", "daily_email_cap",
    "monthly_budget_cap", "pipeline_pause", "warm_up_phase",
    "expansion_enabled", "auto_approve_threshold", "target_industries",
    "email_daily_target", "shadow_mode",
    # Model selection
    "model_primary", "model_fast", "model_genius",
}

# API keys that can be managed from the dashboard
_API_KEY_REGISTRY = {
    # AI & Models
    "anthropic": {"env": "ANTHROPIC_API_KEY", "label": "Claude / Anthropic", "required": True, "category": "ai"},
    "elevenlabs_api": {"env": "ELEVENLABS_API_KEY", "label": "ElevenLabs API Key", "required": False, "category": "ai"},
    "elevenlabs_agent": {"env": "ELEVENLABS_AGENT_ID", "label": "ElevenLabs Agent ID", "required": False, "category": "ai"},
    "kling_access": {"env": "KLING_ACCESS_KEY", "label": "Kling AI (Access Key)", "required": False, "category": "ai"},
    "kling_secret": {"env": "KLING_SECRET_KEY", "label": "Kling AI (Secret Key)", "required": False, "category": "ai"},
    "recraft": {"env": "RECRAFT_API_KEY", "label": "Recraft AI (Images)", "required": False, "category": "ai"},
    "vast_ai": {"env": "VAST_AI_API_KEY", "label": "Vast.ai (Cloud GPU)", "required": False, "category": "ai"},
    "v0": {"env": "V0_API_KEY", "label": "v0.dev (Vercel AI)", "required": False, "category": "ai"},
    # Scraping & Research
    "crawl4ai": {"env": "CRAWL4AI_API_URL", "label": "Crawl4AI (Web Scraping)", "required": False, "category": "scraping"},
    # Outreach
    "instantly": {"env": "INSTANTLY_API_KEY", "label": "Instantly (Email Campaigns)", "required": True, "category": "outreach"},
    # Payments
    "stripe": {"env": "STRIPE_API_KEY", "label": "Stripe (Payments)", "required": True, "category": "payments"},
    "wise": {"env": "WISE_API_TOKEN", "label": "Wise (Payouts)", "required": False, "category": "payments"},
    "wise_profile": {"env": "WISE_PROFILE_ID", "label": "Wise Profile ID", "required": False, "category": "payments"},
    # Hosting & DNS
    "coolify": {"env": "COOLIFY_API_TOKEN", "label": "Coolify (Site Hosting)", "required": True, "category": "hosting"},
    "cloudflare": {"env": "CLOUDFLARE_API_TOKEN", "label": "Cloudflare (DNS)", "required": False, "category": "hosting"},
    "vercel": {"env": "VERCEL_TOKEN", "label": "Vercel Token", "required": False, "category": "hosting"},
    # Communications
    "telegram_bot": {"env": "TELEGRAM_BOT_TOKEN", "label": "Telegram Bot Token", "required": True, "category": "comms"},
    "telegram_chat": {"env": "TELEGRAM_CHAT_ID", "label": "Telegram Chat ID", "required": True, "category": "comms"},
    "telegram_admin": {"env": "TELEGRAM_ADMIN_SECRET", "label": "Telegram Admin Secret", "required": False, "category": "comms"},
    "twilio_sid": {"env": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID", "required": False, "category": "comms"},
    "twilio_auth": {"env": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token", "required": False, "category": "comms"},
    "telnyx": {"env": "TELNYX_API_KEY", "label": "Telnyx (Voice)", "required": False, "category": "comms"},
    "telnyx_conn": {"env": "TELNYX_CONNECTION_ID", "label": "Telnyx Connection ID", "required": False, "category": "comms"},
    "whatsapp": {"env": "WHATSAPP_ACCESS_TOKEN", "label": "WhatsApp Access Token", "required": False, "category": "comms"},
    # Automation & Tools
    "n8n_user": {"env": "N8N_USER", "label": "N8N Username", "required": False, "category": "tools"},
    "n8n_password": {"env": "N8N_PASSWORD", "label": "N8N Password", "required": False, "category": "tools"},
    "composio": {"env": "COMPOSIO_API_KEY", "label": "Composio (Gmail/Google)", "required": False, "category": "tools"},
    # Observability
    "dashboard_secret": {"env": "DASHBOARD_SECRET", "label": "War Room Dashboard Secret", "required": True, "category": "system"},
    # Crypto / Conway
    "conway_api": {"env": "CONWAY_API_KEY", "label": "Conway API Key", "required": False, "category": "crypto"},
    "base_rpc": {"env": "BASE_RPC_URL", "label": "Base L2 RPC URL", "required": False, "category": "crypto"},
}


async def _get_managed_key_value(key_id: str) -> str:
    """Resolve a dashboard-managed key from DB override first, then env."""
    meta = _API_KEY_REGISTRY.get(key_id)
    if not meta:
        return ""

    db_val = await get_config(f"api_key_{key_id}", None)
    if db_val:
        return str(db_val).strip()

    return os.environ.get(meta["env"], "").strip()


def _format_payload_brief(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""

    message = str(payload.get("message") or "").strip()
    if message:
        return message[:140]

    summary_parts = []
    for key in ("target_agent", "intent", "goal", "topic", "subject", "source"):
        value = payload.get(key)
        if value:
            summary_parts.append(f"{key}={value}")
    return ", ".join(summary_parts)[:140]


async def _build_kirito_contextual_update() -> tuple[str, dict]:
    daemon_rows = await fetch_all(
        "SELECT agent_name, status, last_heartbeat FROM agent_registry ORDER BY agent_name"
    )
    tasks = await fetch_all(
        "SELECT task_type, payload, priority, status, created_at "
        "FROM task_queue ORDER BY created_at DESC LIMIT 6"
    )
    events = await fetch_all(
        "SELECT event_type, payload, created_at FROM events ORDER BY created_at DESC LIMIT 6"
    )

    try:
        from shared.governance import _enabled, get_pending_approvals

        pending_approvals = await get_pending_approvals() if _enabled() else []
    except (ImportError, psycopg.Error, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        pending_approvals = []

    daemon_snapshot = []
    for row in daemon_rows:
        name = row.get("agent_name", "unknown")
        paused = await get_config(f"{name}_paused", False)
        daemon_snapshot.append({
            "name": name,
            "status": row.get("status", "unknown"),
            "paused": bool(paused),
            "last_heartbeat": str(row["last_heartbeat"]) if row.get("last_heartbeat") else None,
        })

    task_snapshot = []
    for row in tasks:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        task_snapshot.append({
            "task_type": row.get("task_type", "task"),
            "status": row.get("status", "unknown"),
            "priority": row.get("priority", 5),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "brief": _format_payload_brief(payload),
        })

    event_snapshot = []
    for row in events:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        event_snapshot.append({
            "event_type": row.get("event_type", "event"),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
            "brief": _format_payload_brief(payload),
        })

    daemon_line = ", ".join(
        f"{daemon['name']}={daemon['status']}{' paused' if daemon['paused'] else ''}"
        for daemon in daemon_snapshot
    ) or "no daemon telemetry"
    task_line = "; ".join(
        f"{task['task_type']} [{task['status']}/p{task['priority']}]"
        + (f" — {task['brief']}" if task["brief"] else "")
        for task in task_snapshot[:4]
    ) or "no queued tasks"
    event_line = "; ".join(
        f"{event['event_type']}"
        + (f" — {event['brief']}" if event["brief"] else "")
        for event in event_snapshot[:4]
    ) or "no recent events"

    contextual_update = (
        "Operational context update for Hermes inside PERSEUS. "
        "You are speaking with Kirito's voice and persona, but you are acting as Hermes: "
        "the operator-facing research, coordination, and strategy assistant for Objective Hertz. "
        "Use your ElevenLabs knowledge bases for Kirito, the operator, and meat, then blend in this live system brief. "
        "Do not announce that you received a hidden system update unless the operator explicitly asks.\n\n"
        f"Daemon posture: {daemon_line}.\n"
        f"Pending approvals: {len(pending_approvals)}.\n"
        f"Recent tasks: {task_line}.\n"
        f"Recent events: {event_line}."
    )

    return contextual_update, {
        "daemons": daemon_snapshot,
        "tasks": task_snapshot,
        "events": event_snapshot,
        "pending_approvals": len(pending_approvals),
    }


@app.get("/api/config")
async def api_config_get():
    """Read all runtime config keys from system_config table."""
    result = {}
    for key in _CONFIG_ALLOWLIST:
        result[key] = await get_config(key, None)
    return JSONResponse(result)


@app.post("/api/config")
async def api_config_set(request: Request):
    """Update a runtime config key."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key = body.get("key", "")
    # IGUS-FIX: Basic input validation (CWE-20)
    if not isinstance(key, str) or not key:
        return JSONResponse({"error": "Config key must be a non-empty string"}, status_code=400)
    if key not in _CONFIG_ALLOWLIST:
        return JSONResponse({"error": f"Unknown config key: {key}"}, status_code=400)

    value = body.get("value")
    # IGUS-FIX: Basic input validation (CWE-20)
    if isinstance(value, str) and len(value) > 10000:
        return JSONResponse({"error": "Config value too long (max 10000 chars)"}, status_code=400)

    # Governance check: protected keys require approval (Phase 15)
    try:
        from shared.governance import check_approval_required, request_approval
        if await check_approval_required("config_change", config_key=key):
            # Check if review_mode transition True->False (AEGIS finding)
            if key == "review_mode":
                current = await get_config("review_mode", True)
                if current is True and value is False:
                    approval_id = await request_approval(
                        "autonomy_transition",
                        {"key": key, "from": current, "to": value},
                        requested_by="operator",
                    )
                    if approval_id:
                        return JSONResponse({
                            "status": "approval_required",
                            "approval_id": approval_id,
                            "message": "review_mode transition requires operator confirmation via Telegram",
                        })
            else:
                approval_id = await request_approval(
                    "config_change",
                    {"key": key, "value": value},
                    requested_by="operator",
                )
                if approval_id:
                    return JSONResponse({
                        "status": "approval_required",
                        "approval_id": approval_id,
                    })
    except ImportError:
        pass  # governance module not available

    await set_config(key, value)
    await emit_event("config_changed", {"key": key, "value": value, "source": "war_room"})
    return JSONResponse({"success": True, "key": key, "value": value})


@app.get("/api/budget")
async def api_budget():
    """Budget breakdown from budget_guard."""
    try:
        from shared.config import config
        from tools.budget_guard import get_month_spending
        cap = getattr(config, "monthly_cap", 800)
        budget = await get_month_spending(Decimal(str(cap)))
        return JSONResponse(budget)
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # Fallback if budget_guard not available
        return JSONResponse({
            "total_spent": 0, "remaining": 800, "percent_used": 0,
            # IGUS-FIX: Sanitized error response (CWE-209)
            "exceeded": False, "categories": [], "error": "Budget data unavailable",
        })


@app.get("/api/costs/breakdown")
async def api_costs_breakdown(request: Request):
    """Cost breakdown from cost_events table.

    Query params:
      - group_by: 'agent' | 'model' | 'task_type' | 'day' (default: 'agent')
      - days: lookback period in days (default: 30)
    """
    group_by = request.query_params.get("group_by", "agent")
    try:
        days = min(int(request.query_params.get("days", "30")), 365)
    except (ValueError, TypeError):
        days = 30

    # Allowlist group_by to prevent SQL injection via dynamic column
    ALLOWED_GROUPS = {
        "agent": "agent_id",
        "model": "model",
        "task_type": "task_type",
        "day": "DATE(created_at)",
    }

    column = ALLOWED_GROUPS.get(group_by)
    if column is None:
        return JSONResponse(
            {"error": f"Invalid group_by: {group_by}. Must be one of: {list(ALLOWED_GROUPS.keys())}"},
            status_code=400,
        )

    try:
        # Safe: column is from allowlist, not user input. days is cast to int above.
        rows = await fetch_all(
            f"""SELECT {column} AS group_key,
                       COUNT(*) AS call_count,
                       COALESCE(SUM(tokens_in), 0) AS total_tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS total_tokens_out,
                       ROUND(COALESCE(SUM(cost_usd), 0)::numeric, 4) AS total_cost_usd,
                       ROUND(COALESCE(AVG(cost_usd), 0)::numeric, 6) AS avg_cost_usd,
                       ROUND(COALESCE(AVG(latency_ms), 0)) AS avg_latency_ms
                FROM cost_events
                WHERE created_at >= NOW() - INTERVAL %s
                GROUP BY {column}
                ORDER BY total_cost_usd DESC""",
            (f"{days} days",),
        )
        return JSONResponse({
            "group_by": group_by,
            "days": days,
            "breakdown": [dict(r) for r in rows] if rows else [],
        })
    except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return _safe_error(e, breakdown=[])


@app.get("/api/cost-dashboard")
async def cost_dashboard(request: Request):
    """Weekly agent cost tracking dashboard data (Phase 19)."""
    try:
        days = min(int(request.query_params.get("days", "7")), 365)
    except (ValueError, TypeError):
        days = 7
    from shared.cost_events import get_agent_cost_summary
    return JSONResponse(await get_agent_cost_summary(days))


@app.get("/api/retrieval-telemetry")
async def retrieval_telemetry(request: Request):
    """Retrieval telemetry dashboard data from magma_retrieval_stats (Phase 19)."""
    try:
        days = min(int(request.query_params.get("days", "7")), 365)
    except (ValueError, TypeError):
        days = 7
    query_type = request.query_params.get("query_type", "")

    params: list[str] = [f"{days} days"]
    where_clause = "WHERE created_at >= NOW() - INTERVAL %s"
    if query_type:
        where_clause += " AND query_type = %s"
        params.append(query_type)

    try:
        rows = await fetch_all(
            f"""SELECT
                query_type,
                intent,
                COUNT(*) AS total_queries,
                ROUND(AVG(anchors_found)::numeric, 1) AS avg_anchors_found,
                ROUND(AVG(anchors_used)::numeric, 1) AS avg_anchors_used,
                ROUND(AVG(confidence_avg)::numeric, 3) AS avg_confidence,
                ROUND(AVG(latency_ms)::numeric, 0) AS avg_latency_ms,
                SUM(CASE WHEN abstained THEN 1 ELSE 0 END) AS abstention_count,
                ROUND(AVG(beam_ms)::numeric, 0) AS avg_beam_ms,
                ROUND(AVG(decompose_ms)::numeric, 0) AS avg_decompose_ms
            FROM magma_retrieval_stats
            {where_clause}
            GROUP BY query_type, intent
            ORDER BY total_queries DESC""",
            tuple(params),
        )
        return JSONResponse([dict(r) for r in rows] if rows else [])
    except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        return _safe_error(e)


@app.get("/api/tasks")
async def api_tasks(request: Request):
    """Task queue visibility — filter by status and agent."""
    status = request.query_params.get("status")
    agent = request.query_params.get("agent")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except (ValueError, TypeError):
        limit = 50

    conditions = []
    params: list = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if agent:
        conditions.append("task_type LIKE %s")
        params.append(f"{agent}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(min(limit, 200))

    rows = await fetch_all(
        f"SELECT id, task_type, payload, priority, status, created_at, updated_at "
        f"FROM task_queue {where} ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )
    tasks = []
    for r in rows:
        payload = r.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        tasks.append({
            "id": r["id"],
            "task_type": r["task_type"],
            "payload": payload,
            "priority": r.get("priority", 5),
            "status": r["status"],
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else None,
        })
    return JSONResponse(tasks)


@app.get("/api/daemons")
async def api_daemons():
    """Daemon status from agent_registry table."""
    rows = await fetch_all(
        "SELECT agent_name, status, last_heartbeat, metadata "
        "FROM agent_registry ORDER BY agent_name"
    )
    daemons = {}
    for r in rows:
        name = r["agent_name"]
        paused = await get_config(f"{name}_paused", False)
        daemons[name] = {
            "status": r.get("status", "unknown"),
            "last_heartbeat": str(r["last_heartbeat"]) if r.get("last_heartbeat") else None,
            "paused": bool(paused),
        }
    return JSONResponse(daemons)


@app.post("/api/daemons/{name}/action")
async def api_daemon_action(name: str, request: Request):
    """Pause/resume a daemon via config flag."""
    valid_daemons = {"perseus", "titan", "hermes", "clawdbot", "deerflow_research"}
    if name not in valid_daemons:
        return JSONResponse({"error": f"Unknown daemon: {name}"}, status_code=400)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action = body.get("action", "")
    if action == "pause":
        await set_config(f"{name}_paused", True)
        await emit_event("daemon_control", {"daemon": name, "action": "pause", "source": "war_room"})
    elif action == "resume":
        await set_config(f"{name}_paused", False)
        await emit_event("daemon_control", {"daemon": name, "action": "resume", "source": "war_room"})
    else:
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)

    return JSONResponse({"success": True, "daemon": name, "action": action})


@app.post("/api/review/bulk")
async def api_review_bulk(request: Request):
    """Bulk approve/reject review queue items."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action = body.get("action", "")
    ids = body.get("ids")  # Optional: specific IDs

    if action == "approve_all":
        from titan.review_mode import approve_review

        pending = await fetch_all(
            "SELECT id FROM review_queue WHERE status = 'pending_review' ORDER BY created_at ASC"
        )
        succeeded = 0
        failed = 0
        for row in pending:
            try:
                result = await approve_review(row["id"], notes="Bulk approved from War Room")
                if result:
                    succeeded += 1
                else:
                    failed += 1
            except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.error("Bulk approve failed for review %s: %s", row["id"], e)
                failed += 1
        await emit_event("bulk_approve", {
            "count": succeeded, "failed": failed, "source": "war_room",
        })
        return JSONResponse({
            "success": True,
            "action": "approve_all",
            "affected": succeeded,
            "failed": failed,
        })

    elif action == "reject_all":
        await execute(
            "UPDATE review_queue SET status = 'rejected' WHERE status = 'pending_review'"
        )
        count = await fetch_val(
            "SELECT COUNT(*) FROM review_queue WHERE status = 'rejected' "
            "AND updated_at > NOW() - INTERVAL '5 seconds'"
        ) or 0
        await emit_event("bulk_reject", {"count": int(count), "source": "war_room"})
        return JSONResponse({"success": True, "action": "reject_all", "affected": int(count)})

    elif action in ("approve", "reject") and ids:
        if action == "approve":
            from titan.review_mode import approve_review

            succeeded = 0
            failed = 0
            for rid in ids:
                try:
                    result = await approve_review(rid, notes="Approved from War Room")
                    if result:
                        succeeded += 1
                    else:
                        failed += 1
                except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.error("Approve failed for review %s: %s", rid, e)
                    failed += 1
            await emit_event("bulk_approve", {"ids": ids, "source": "war_room"})
            return JSONResponse({"success": True, "action": action, "affected": succeeded, "failed": failed})
        else:
            from titan.review_mode import reject_review

            for rid in ids:
                try:
                    await reject_review(rid, notes="Rejected from War Room")
                except (psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.error("Reject failed for review %s: %s", rid, e)
            await emit_event("bulk_reject", {"ids": ids, "source": "war_room"})
            return JSONResponse({"success": True, "action": action, "affected": len(ids)})

    return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)


@app.post("/api/pipeline/run")
async def api_pipeline_run():
    """Trigger a full pipeline run by inserting a task."""
    task_id = await insert_task(
        "titan_pipeline_run",
        {"source": "war_room", "full_run": True},
        priority=1,
        dedupe=False,
    )
    await emit_event("pipeline_triggered", {"task_id": task_id, "source": "war_room"})
    return JSONResponse({"success": True, "task_id": task_id, "status": "queued"})


@app.get("/api/campaigns")
async def api_campaigns():
    """List email campaigns from Instantly."""
    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()
        campaigns = await client.list_campaigns()
        return JSONResponse(campaigns if isinstance(campaigns, list) else [])
    except (httpx.HTTPError, OSError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return _safe_error(e, campaigns=[])


@app.post("/api/campaigns/{campaign_id}/action")
async def api_campaign_action(campaign_id: str, request: Request):
    """Activate or stop an email campaign."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action = body.get("action", "")
    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()
        if action == "activate":
            result = await client.activate_campaign(campaign_id)
        elif action == "stop":
            result = await client.stop_campaign(campaign_id)
        else:
            return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)

        await emit_event("campaign_control", {
            "campaign_id": campaign_id, "action": action, "source": "war_room",
        })
        return JSONResponse({"success": True, "action": action, "result": result})
    except (httpx.HTTPError, OSError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return _safe_error(e)


@app.get("/api/campaigns/{campaign_id}/analytics")
async def api_campaign_analytics(campaign_id: str):
    """Campaign analytics from Instantly."""
    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()
        analytics = await client.get_campaign_analytics(campaign_id)
        return JSONResponse(analytics)
    except (httpx.HTTPError, OSError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return _safe_error(e)


@app.get("/api/learnings")
async def api_learnings(request: Request):
    """AI learnings from titan_learnings table."""
    limit = int(request.query_params.get("limit", "50"))
    rows = await fetch_all(
        "SELECT id, category, insight, confidence, source_lead_id, created_at "
        "FROM titan_learnings ORDER BY created_at DESC LIMIT %s",
        (min(limit, 200),),
    )
    return JSONResponse([
        {
            "id": r["id"],
            "category": r.get("category", ""),
            "insight": r.get("insight", ""),
            "confidence": float(r.get("confidence", 0)),
            "source_lead_id": r.get("source_lead_id"),
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
        }
        for r in rows
    ])


@app.get("/api/decisions")
async def api_decisions(request: Request):
    """Decision audit trail from agent_decisions table."""
    limit = int(request.query_params.get("limit", "50"))
    rows = await fetch_all(
        "SELECT id, agent, decision_type, context, decision, reasoning, outcome, created_at "
        "FROM agent_decisions ORDER BY created_at DESC LIMIT %s",
        (min(limit, 200),),
    )
    results = []
    for r in rows:
        ctx = r.get("context", {})
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except (json.JSONDecodeError, TypeError):
                ctx = {}
        results.append({
            "id": r["id"],
            "agent": r.get("agent", ""),
            "decision_type": r.get("decision_type", ""),
            "context": ctx,
            "decision": r.get("decision", ""),
            "reasoning": r.get("reasoning", ""),
            "outcome": r.get("outcome"),
            "created_at": str(r["created_at"]) if r.get("created_at") else None,
        })
    return JSONResponse(results)


# ── Architecture Additive APIs (Phase 15) ──────────────────────────────


@app.get("/api/goals/tree")
async def get_goals_tree():
    """Return the full goal hierarchy for dashboard visualization."""
    try:
        from shared.goal_cascade import _enabled, get_goal_tree
        if not _enabled():
            return JSONResponse({"goals": [], "enabled": False})
        tree = await get_goal_tree()
        return JSONResponse({"goals": tree, "enabled": True})
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Goal tree fetch failed: %s", e)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return JSONResponse({"goals": [], "enabled": False, "error": "Goal data unavailable"})


@app.get("/api/approvals")
async def get_approvals():
    """Return pending approvals and recent history for operator review."""
    try:
        from shared.governance import _enabled, get_approval_history, get_pending_approvals
        if not _enabled():
            return JSONResponse({"pending": [], "history": [], "enabled": False})
        pending = await get_pending_approvals()
        history = await get_approval_history(limit=20)
        return JSONResponse({"pending": pending, "history": history, "enabled": True})
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Approvals fetch failed: %s", e)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return JSONResponse({"pending": [], "history": [], "enabled": False, "error": "Approval data unavailable"})


@app.post("/api/approvals/{approval_id}/resolve")
async def resolve_approval_endpoint(approval_id: str, request: Request):
    """Approve or reject a pending approval."""
    try:
        from shared.governance import resolve_approval
        data = await request.json()
        decision = data.get("decision")  # "approved" or "rejected"
        resolved_by = data.get("resolved_by", "operator")

        if decision not in ("approved", "rejected"):
            return JSONResponse({"error": "decision must be 'approved' or 'rejected'"}, status_code=400)

        success = await resolve_approval(approval_id, decision, resolved_by)
        if not success:
            return JSONResponse({"error": "Approval not found or already resolved"}, status_code=404)
        return JSONResponse({"status": "ok", "approval_id": approval_id, "decision": decision})
    except (OSError, ValueError, KeyError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Approval resolve failed: %s", e, exc_info=True)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return _safe_error(e)


@app.get("/api/commit-metrics")
async def get_commit_metrics(request: Request):
    """Return commit metrics summary for dashboard."""
    try:
        from tools.commit_metrics import get_metrics_summary
        days = int(request.query_params.get("days", "30"))
        summary = await get_metrics_summary(days=days)
        return JSONResponse(summary)
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Commit metrics fetch failed: %s", e)
        # IGUS-FIX: Sanitized error response (CWE-209)
        return JSONResponse({"enabled": False, "error": "Commit metrics unavailable"})


# ── API Key Management ─────────────────────────────────────────────────


def _mask_key(value: str) -> str:
    """Mask an API key, showing only last 4 chars."""
    if not value or len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


@app.get("/api/keys")
async def api_keys_list():
    """List all API keys with masked values. Never returns full keys."""
    result = {}
    for key_id, meta in _API_KEY_REGISTRY.items():
        # Check DB override first, then env var
        db_val = await get_config(f"api_key_{key_id}", None)
        env_val = os.environ.get(meta["env"], "")
        raw = str(db_val) if db_val else env_val
        result[key_id] = {
            "label": meta["label"],
            "env": meta["env"],
            "required": meta["required"],
            "configured": bool(raw),
            "masked": _mask_key(raw) if raw else "",
            "source": "dashboard" if db_val else ("env" if env_val else "none"),
        }
    return JSONResponse(result)


@app.get("/api/voice/elevenlabs/context")
async def kirito_voice_context():
    """Build a compact live Hermes brief for Kirito voice sessions."""
    contextual_update, snapshot = await _build_kirito_contextual_update()
    return JSONResponse({
        "contextual_update": contextual_update,
        "snapshot": snapshot,
    })


@app.get("/api/voice/elevenlabs/session")
async def kirito_voice_session():
    """Create a private ElevenLabs conversation token plus live Hermes context."""
    api_key = await _get_managed_key_value("elevenlabs_api")
    agent_id = await _get_managed_key_value("elevenlabs_agent")

    if not api_key or not agent_id:
        return JSONResponse(
            {
                "error": "Kirito voice is not configured. Add ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in War Room Settings."
            },
            status_code=503,
        )

    contextual_update, snapshot = await _build_kirito_contextual_update()

    url = f"https://api.elevenlabs.io/v1/convai/conversation/token?agent_id={quote(agent_id)}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers={"xi-api-key": api_key})

    if response.status_code >= 400:
        detail = response.text[:300]
        logger.error(
            "ElevenLabs conversation token request failed: status=%s detail=%s",
            response.status_code,
            detail,
        )
        return JSONResponse(
            {"error": "Failed to create ElevenLabs conversation token.", "detail": detail},
            status_code=502,
        )

    body = response.json()
    token = str(body.get("token") or "").strip()
    if not token:
        return JSONResponse(
            {"error": "ElevenLabs token response was empty."},
            status_code=502,
        )

    return JSONResponse(
        {
            "agent_id": agent_id,
            "conversation_token": token,
            "contextual_update": contextual_update,
            "dynamic_variables": {
                "assistant_identity": "Hermes speaking through Kirito",
                "operator_surface": "PERSEUS War Room desktop buddy",
            },
            "user_id": "perseus-operator",
            "snapshot": snapshot,
        }
    )


@app.post("/api/kirito/command")
async def api_kirito_command(request: Request):
    """Route a Kirito/Hermes operator command to the best execution lane."""
    # IGUS-FIX: Content size limit (AEGIS Tier 3)
    cl = request.headers.get("content-length", "")
    if cl and int(cl) > 100_000:  # 100KB max for commands
        return JSONResponse({"error": "Request body too large (max 100KB)"}, status_code=413)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = " ".join(str(body.get("text", "")).strip().split())
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    source = str(body.get("source", "kirito_ui") or "kirito_ui")
    mode = str(body.get("mode", "text") or "text")
    conversation_id = str(body.get("conversation_id", "") or "")
    context = body.get("context", {})
    if not isinstance(context, dict):
        context = {}

    request_id = str(body.get("request_id") or secrets.token_hex(8))
    autonomy_mode = str(body.get("autonomy_mode", "full") or "full")
    auth_context = body.get("auth_context", {})
    if not isinstance(auth_context, dict):
        auth_context = {}
    active_surface = str(body.get("active_surface", "") or "")

    plan = route_kirito_command(
        text,
        context={
            **context,
            "autonomy_mode": autonomy_mode,
            "auth_context": auth_context,
            "active_surface": active_surface,
        },
    )
    plan_payload = normalize_dispatch_plan(serialize_route_plan(plan))
    dispatch_mode = (
        "local_action"
        if plan_payload["local_action"]
        else "capability"
        if plan_payload["target_agent"] in {"hermes", "orchestrator", "system_executor", "deerflow_research"} or plan_payload["capability"] in {
            "ask",
            "operator_command",
            "briefing_custom",
            "desktop_control",
            "desktop_exec",
            "system_action",
            "screen_context",
            "auth_checkpoint",
            "evolution_research_cycle",
            "paper_scan",
            "repo_scan",
            "daily_evolution_brief",
        }
        else "task_queue"
    )

    await emit_event(
        "kirito_command_received",
        {
            "request_id": request_id,
            "step": "received",
            "spans": ["kirito", "intake"],
            "executor": "hermes",
            "risk": "low",
            "text": text,
            "source": source,
            "mode": mode,
            "conversation_id": conversation_id,
            "autonomy_mode": autonomy_mode,
            "result_summary": f"Received operator request: {text[:120]}",
            "sender": "hermes",
        },
    )
    await emit_event(
        "kirito_command_classified",
        {
            "request_id": request_id,
            "step": "classified",
            "spans": ["kirito", "routing", str(plan_payload["domain"])],
            "executor": plan_payload["executor"],
            "risk": plan_payload["risk_class"],
            "intent": plan_payload["intent"],
            "task_type": plan_payload["task_type"],
            "capability": plan_payload["capability"],
            "target_agent": plan_payload["target_agent"],
            "confidence": plan_payload["confidence"],
            "reasoning": plan_payload["rationale"],
            "domain": plan_payload["domain"],
            "selected_executor": plan_payload["executor"],
            "risk_class": plan_payload["risk_class"],
            "capability_family": plan_payload["capability_family"],
            "result_summary": str(plan_payload["rationale"])[:180],
            "sender": "hermes",
        },
    )
    await emit_event(
        "kirito_command_planned",
        {
            "request_id": request_id,
            "step": "planned",
            "spans": ["kirito", "plan", str(plan_payload["domain"])],
            "executor": plan_payload["executor"],
            "risk": plan_payload["risk_class"],
            "intent": plan_payload["intent"],
            "domain": plan_payload["domain"],
            "selected_executor": plan_payload["executor"],
            "executor_kind": plan_payload["executor_kind"],
            "target_agent": plan_payload["target_agent"],
            "task_type": plan_payload["task_type"],
            "capability": plan_payload["capability"],
            "risk_class": plan_payload["risk_class"],
            "visibility_mode": plan_payload["visibility_mode"],
            "supports_auth": plan_payload["supports_auth"],
            "supports_full_autonomy": plan_payload["supports_full_autonomy"],
            "fallback_executor": plan_payload["fallback_executor"],
            "result_summary": f"Planned {plan_payload['task_type']} via {plan_payload['executor']}.",
            "sender": "hermes",
        },
    )

    try:
        from shared.comms import call_agent_capability, record_decision, request_task

        await record_decision(
            agent="hermes",
            decision_type="kirito_command_route",
            context={
                "request_id": request_id,
                "source": source,
                "mode": mode,
                "conversation_id": conversation_id,
                "autonomy_mode": autonomy_mode,
                "context": context,
            },
            decision=plan_payload,
            reasoning=str(plan_payload["rationale"]),
        )

        dispatch: dict[str, object] = {
            "status": "accepted",
            "dispatch_mode": dispatch_mode,
            "target_agent": plan_payload["target_agent"],
            "task_type": plan_payload["task_type"],
            "capability": plan_payload["capability"],
            "selected_executor": plan_payload["executor"],
            "risk_class": plan_payload["risk_class"],
            "visibility_mode": plan_payload["visibility_mode"],
        }

        local_action = None
        if plan_payload["local_action"]:
            dashboard = str((plan_payload.get("payload") or {}).get("dashboard", "main") or "main")
            target = "/"
            if dashboard in {"sales", "pipeline"}:
                target = "/pipeline"
            elif dashboard in {"autopilot", "approvals"}:
                target = "/autopilot"
            local_action = {
                "action": "open_dashboard",
                "target": target,
            }
            await emit_event(
                "kirito_command_local_action",
                {
                    "request_id": request_id,
                    "step": "local_action",
                    "spans": ["kirito", "local_action", "desktop"],
                    "executor": "desktop.local_action",
                    "risk": "low",
                    "action": "open_dashboard",
                    "target": target,
                    "selected_executor": "desktop.local_action",
                    "result_summary": f"Prepared local dashboard action for {target}.",
                    "sender": "hermes",
                },
            )

        payload = {
            **(plan_payload.get("payload") or {}),
            "request_id": request_id,
            "conversation_id": conversation_id,
            "source": source,
            "mode": mode,
            "autonomy_mode": autonomy_mode,
            "auth_context": auth_context,
            "active_surface": active_surface,
            "context": context,
        }

        if dispatch_mode == "task_queue" and plan_payload["task_type"]:
            task_id = await request_task(str(plan_payload["task_type"]), payload, priority=3, dedupe=False)
            dispatch["task_id"] = task_id
            await emit_event(
                "kirito_command_dispatched",
                {
                    "request_id": request_id,
                    "step": "dispatched",
                    "spans": ["kirito", "dispatch", str(plan_payload["domain"])],
                    "executor": plan_payload["executor"],
                    "risk": plan_payload["risk_class"],
                    "task_id": task_id,
                    "target_agent": plan_payload["target_agent"],
                    "task_type": plan_payload["task_type"],
                    "dispatch_mode": "task_queue",
                    "selected_executor": plan_payload["executor"],
                    "risk_class": plan_payload["risk_class"],
                    "result_summary": f"Queued {plan_payload['task_type']} for {plan_payload['target_agent']}.",
                    "sender": "hermes",
                },
            )
        elif dispatch_mode == "capability" and plan_payload["capability"]:
            await emit_event(
                "executor_tool_started",
                {
                    "request_id": request_id,
                    "step": "running",
                    "spans": ["executor", str(plan_payload["executor"]), str(plan_payload["task_type"])],
                    "executor": plan_payload["executor"],
                    "risk": plan_payload["risk_class"],
                    "selected_executor": plan_payload["executor"],
                    "target_agent": plan_payload["target_agent"],
                    "task_type": plan_payload["task_type"],
                    "capability": plan_payload["capability"],
                    "result_summary": f"Started {plan_payload['capability']} on {plan_payload['target_agent']}.",
                    "sender": "hermes",
                },
            )
            result = await call_agent_capability(
                str(plan_payload["target_agent"]),
                str(plan_payload["capability"]),
                payload,
                timeout=90,
            )
            if not result or result.get("error"):
                detail = str((result or {}).get("error", "dispatch failed"))
                await emit_event(
                    "kirito_command_failed",
                    {
                        "request_id": request_id,
                        "step": "failed",
                        "spans": ["kirito", "dispatch", "failed"],
                        "executor": plan_payload["executor"],
                        "risk": plan_payload["risk_class"],
                        "error": detail,
                        "stage": "dispatch",
                        "selected_executor": plan_payload["executor"],
                        "result_summary": detail[:180],
                        "sender": "hermes",
                    },
                )
                return JSONResponse(
                    {
                        "accepted": False,
                        "request_id": request_id,
                        "classification": {
                            "intent": plan_payload["intent"],
                            "task_type": plan_payload["task_type"],
                            "capability": plan_payload["capability"],
                            "target_agent": plan_payload["target_agent"],
                            "route": dispatch_mode,
                            "confidence": plan_payload["confidence"],
                            "domain": plan_payload["domain"],
                            "selected_executor": plan_payload["executor"],
                            "risk_class": plan_payload["risk_class"],
                        },
                        "error": detail,
                    },
                    status_code=502,
                )

            task_id = str(result.get("task_id") or f"a2a_{request_id}:accepted")
            dispatch["task_id"] = task_id
            dispatch["result"] = result
            if isinstance(result, dict) and result.get("status") in {"auth_wait", "needs_auth"}:
                await emit_event(
                    "executor_auth_wait",
                    {
                        "request_id": request_id,
                        "step": "awaiting_followup",
                        "spans": ["executor", str(plan_payload["executor"]), "auth_wait"],
                        "executor": plan_payload["executor"],
                        "risk": plan_payload["risk_class"],
                        "selected_executor": plan_payload["executor"],
                        "target_agent": plan_payload["target_agent"],
                        "reason": result.get("message") or result.get("reason") or "Authentication required",
                        "channel": result.get("channel") or result.get("provider") or "auth",
                        "result_summary": str(result.get("message") or result.get("reason") or "Authentication required")[:180],
                        "sender": "hermes",
                    },
                )
            await emit_event(
                "kirito_command_dispatched",
                {
                    "request_id": request_id,
                    "step": "dispatched",
                    "spans": ["kirito", "dispatch", str(plan_payload["domain"])],
                    "executor": plan_payload["executor"],
                    "risk": plan_payload["risk_class"],
                    "task_id": task_id,
                    "target_agent": plan_payload["target_agent"],
                    "task_type": plan_payload["task_type"],
                    "capability": plan_payload["capability"],
                    "dispatch_mode": "a2a_capability",
                    "selected_executor": plan_payload["executor"],
                    "risk_class": plan_payload["risk_class"],
                    "result_summary": f"Dispatched {plan_payload['capability']} to {plan_payload['target_agent']}.",
                    "sender": "hermes",
                },
            )
            await emit_event(
                "executor_tool_finished",
                {
                    "request_id": request_id,
                    "step": "completed" if str(result.get("status") or "completed") not in {"auth_wait", "needs_auth"} else "awaiting_followup",
                    "spans": ["executor", str(plan_payload["executor"]), str(plan_payload["task_type"])],
                    "executor": plan_payload["executor"],
                    "risk": plan_payload["risk_class"],
                    "target_agent": plan_payload["target_agent"],
                    "task_id": task_id,
                    "selected_executor": plan_payload["executor"],
                    "status": str(result.get("status") or "completed"),
                    "result_summary": str(result.get("status") or result.get("answer") or "accepted")[:180],
                    "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), list) else [],
                    "replay_url": result.get("replay_url"),
                    "sender": "hermes",
                },
            )
            if not (isinstance(result, dict) and result.get("status") in {"auth_wait", "needs_auth"}):
                await emit_event(
                    "kirito_command_completed",
                    {
                        "request_id": request_id,
                        "step": "completed",
                        "spans": ["kirito", "completed", str(plan_payload["domain"])],
                        "executor": plan_payload["executor"],
                        "risk": plan_payload["risk_class"],
                        "target_agent": plan_payload["target_agent"],
                        "task_id": task_id,
                        "selected_executor": plan_payload["executor"],
                        "result_summary": str(result.get("status") or result.get("answer") or "accepted")[:180],
                        "sender": "hermes",
                    },
                )
        else:
            dispatch["status"] = "local_action_only"
            await emit_event(
                "kirito_command_completed",
                {
                    "request_id": request_id,
                    "step": "completed",
                    "spans": ["kirito", "completed", "desktop"],
                    "executor": "desktop.local_action",
                    "risk": "low",
                    "target_agent": "desktop",
                    "task_id": None,
                    "selected_executor": "desktop.local_action",
                    "result_summary": "Local action prepared for desktop execution.",
                    "sender": "hermes",
                },
            )

        return JSONResponse(
            {
                "accepted": True,
                "request_id": request_id,
                "status_url": f"/api/kirito/command/status?command_id={request_id}",
                "selected_executor": plan_payload["executor"],
                "risk_class": plan_payload["risk_class"],
                "plan": plan_payload,
                "classification": {
                    "intent": plan_payload["intent"],
                    "task_type": plan_payload["task_type"],
                    "capability": plan_payload["capability"],
                    "target_agent": plan_payload["target_agent"],
                    "route": dispatch_mode,
                    "confidence": plan_payload["confidence"],
                    "reasoning": plan_payload["rationale"],
                    "requires_followup": plan_payload["requires_followup"],
                    "followup_question": plan_payload["followup_question"],
                    "domain": plan_payload["domain"],
                    "selected_executor": plan_payload["executor"],
                    "risk_class": plan_payload["risk_class"],
                    "capability_family": plan_payload["capability_family"],
                },
                "dispatch": dispatch,
                "local_action": local_action,
                "preview": {
                    "summary": plan_payload["rationale"],
                },
            }
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.exception("Kirito command dispatch failed")
        await emit_event(
            "kirito_command_failed",
            {
                "request_id": request_id,
                "step": "failed",
                "spans": ["kirito", "dispatch", "failed"],
                "executor": plan_payload["executor"] if "plan_payload" in locals() else "hermes",
                "risk": plan_payload["risk_class"] if "plan_payload" in locals() else "medium",
                # IGUS-FIX: Sanitized error in event payload (CWE-209)
                "error": "dispatch_failed",
                "stage": "dispatch",
                "result_summary": "Kirito command dispatch failed",
                "sender": "hermes",
            },
        )
        return JSONResponse(
            # IGUS-FIX: Sanitized error response (CWE-209)
            {"accepted": False, "request_id": request_id, "error": "Command dispatch failed"},
            status_code=500,
        )


@app.get("/api/kirito/command/status")
async def api_kirito_command_status(command_id: str = ""):
    """Fetch recent Kirito command activity, optionally scoped to one request_id."""
    normalized_id = command_id.strip()

    if normalized_id:
        event_rows = await fetch_all(
            """SELECT id, event_type, payload, created_at
               FROM events
               WHERE (event_type LIKE 'kirito_command%%' OR event_type LIKE 'executor_%%')
               AND payload::jsonb->>'request_id' = %s
               ORDER BY created_at DESC LIMIT 12""",
            (normalized_id,),
        )
        task_rows = await fetch_all(
            """SELECT id, task_type, payload, priority, status, created_at, updated_at
               FROM task_queue
               WHERE payload::jsonb->>'request_id' = %s
               ORDER BY created_at DESC LIMIT 6""",
            (normalized_id,),
        )
    else:
        event_rows = await fetch_all(
            """SELECT id, event_type, payload, created_at
               FROM events
               WHERE event_type LIKE 'kirito_command%%' OR event_type LIKE 'executor_%%'
               ORDER BY created_at DESC LIMIT 12"""
        )
        task_rows = await fetch_all(
            """SELECT id, task_type, payload, priority, status, created_at, updated_at
               FROM task_queue
               WHERE payload::jsonb->>'source' LIKE 'kirito%%'
               ORDER BY created_at DESC LIMIT 6"""
        )

    events = []
    latest_request_id = normalized_id
    for row in event_rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        if not latest_request_id:
            latest_request_id = str(payload.get("request_id", "") or "")
        events.append(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
            }
        )

    tasks = []
    for row in task_rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        tasks.append(
            {
                "id": row["id"],
                "task_type": row["task_type"],
                "status": row["status"],
                "priority": row.get("priority", 5),
                "payload": payload,
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
                "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
            }
        )

    return JSONResponse(
        {
            "command_id": latest_request_id,
            "events": events,
            "tasks": tasks,
            "latest_event": events[0] if events else None,
            **build_status_graph(events=events, tasks=tasks),
        }
    )


@app.post("/api/keys")
async def api_keys_update(request: Request):
    """Update an API key. Stored in system_config (DB), also set in os.environ for current process."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key_id = body.get("key_id", "")
    value = body.get("value", "")

    # IGUS-FIX: Basic input validation (CWE-20)
    if not isinstance(key_id, str) or not key_id:
        return JSONResponse({"error": "key_id must be a non-empty string"}, status_code=400)
    if not isinstance(value, str):
        return JSONResponse({"error": "value must be a string"}, status_code=400)
    if len(value) > 500:
        return JSONResponse({"error": "API key value too long (max 500 chars)"}, status_code=400)

    if key_id not in _API_KEY_REGISTRY:
        return JSONResponse({"error": f"Unknown key: {key_id}"}, status_code=400)

    if not value:
        return JSONResponse({"error": "Value cannot be empty"}, status_code=400)

    meta = _API_KEY_REGISTRY[key_id]

    # Store in DB for persistence across restarts
    await set_config(f"api_key_{key_id}", value)

    # NOTE: os.environ mutation removed — only Hermes process env would be affected,
    # not other daemons. Services read keys at startup from DB via get_config().
    await emit_event("api_key_updated", {"key_id": key_id, "label": meta["label"], "source": "war_room"})

    return JSONResponse({
        "success": True,
        "key_id": key_id,
        "masked": _mask_key(value),
    })


@app.delete("/api/keys/{key_id}")
async def api_keys_delete(key_id: str):
    """Remove a dashboard-set API key override (reverts to .env value)."""
    if key_id not in _API_KEY_REGISTRY:
        return JSONResponse({"error": f"Unknown key: {key_id}"}, status_code=400)

    await set_config(f"api_key_{key_id}", None)
    return JSONResponse({"success": True, "key_id": key_id})


# ── Phase 1: Streaming Insights (SSE) ──────────────────────────────────

@app.post("/api/insights/stream")
async def api_insights_stream(request: Request):
    """Stream strategic insight responses token-by-token via SSE."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "Empty question"}, status_code=400)
    if len(question) > 2000:  # IGUS-FIX: Limit input length (CWE-20)
        return JSONResponse({"error": "Question too long (max 2000 chars)"}, status_code=400)

    async def generate():
        try:
            from shared.llm_client import llm

            # Build context from DB
            pipeline_rows = await fetch_all(
                "SELECT status, COUNT(*) as cnt FROM clients GROUP BY status"
            )
            pipeline_summary = ", ".join(f"{r['status']}: {r['cnt']}" for r in pipeline_rows)

            recent_events = await fetch_all(
                "SELECT event_type, payload FROM events ORDER BY id DESC LIMIT 10"
            )
            events_summary = "; ".join(
                f"{e['event_type']}" for e in recent_events
            )

            prompt = (
                f"You are the Perseus strategic analyst. Answer the operator's question "
                f"using this live data:\n\nPipeline: {pipeline_summary}\n"
                f"Recent events: {events_summary}\n\n"
                f"Question: {question}\n\n"
                f"Provide a concise answer with evidence and 1-3 recommended actions."
            )

            # Use streaming if available, otherwise chunk the full response
            response = await llm.generate(prompt, model="local", max_tokens=500, temperature=0.3, operation="hermes.generate", daemon_name="hermes")

            # Simulate streaming by yielding chunks
            words = response.split()
            buffer = ""
            for i, word in enumerate(words):
                buffer += word + " "
                if len(buffer) > 20 or i == len(words) - 1:
                    yield f"data: {json.dumps({'token': buffer})}\n\n"
                    buffer = ""
                    await asyncio.sleep(0.05)  # 50ms between chunks

            yield f"data: {json.dumps({'done': True})}\n\n"

        except (OSError, ValueError, RuntimeError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            # IGUS-FIX: Sanitized error response (CWE-209)
            logger.error("SSE stream error: %s", e, exc_info=True)
            yield f"data: {json.dumps({'error': 'Internal server error'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Jarvis Screen Feed (WebSocket + REST) ────────────────────────────────

@app.websocket("/ws/jarvis/screen")
async def ws_jarvis_screen(websocket: WebSocket):
    """Live screen feed for War Room — gated behind JARVIS_SCREEN_FEED flag."""
    if not jarvis_feed_enabled():
        await websocket.close(code=4003, reason="Jarvis screen feed disabled")
        return
    await jarvis_screen_feed(websocket)


@app.get("/api/jarvis/screen")
async def get_jarvis_screenshot():
    """On-demand screenshot for War Room."""
    if not jarvis_feed_enabled():
        return JSONResponse({"error": "Jarvis screen feed disabled"}, status_code=403)
    try:
        from hermes.jarvis.image_pipeline import capture_screenshot, resize_for_telegram, resize_for_vision
        from hermes.jarvis.vision_analyzer import describe_screen

        screenshot = await capture_screenshot(method="native")
        compressed = resize_for_telegram(screenshot)
        description = await describe_screen(resize_for_vision(screenshot))

        return {
            "image": base64.b64encode(compressed).decode(),
            "description": description,
        }
    except (OSError, RuntimeError, ImportError) as e:
        return _safe_error(e, status_code=500)


# ---------------------------------------------------------------------------
# Hermes V2 API Endpoints
# ---------------------------------------------------------------------------

_V2_VOICE_ENABLED = os.environ.get("HERMES_VOICE_ENABLED", "").lower() in ("true", "1")
_V2_FRIGATE_ENABLED = os.environ.get("FRIGATE_ENABLED", "").lower() in ("true", "1")
_V2_MONITORING_ENABLED = os.environ.get("MONITORING_ENABLED", "").lower() in ("true", "1")
_V2_INTELLIGENCE_ENABLED = os.environ.get("INTELLIGENCE_ENABLED", "").lower() in ("true", "1")
_V2_STARLINK_ENABLED = os.environ.get("STARLINK_ENABLED", "").lower() in ("true", "1")


@app.get("/api/briefing")
async def api_briefing():
    """Morning briefing text."""
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "morning_briefing", {})
        return JSONResponse({"briefing": result if isinstance(result, str) else (result or {}).get("briefing", "")})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/weather")
async def api_weather():
    """Current weather data."""
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "weather", {})
        return JSONResponse(result if isinstance(result, dict) else {"summary": str(result or "")})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/cameras/snapshots")
async def api_camera_snapshots():
    """Camera snapshots from Frigate."""
    if not _V2_FRIGATE_ENABLED:
        return JSONResponse({"error": "Frigate cameras not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "camera_snapshots", {})
        return JSONResponse(result if isinstance(result, dict) else {"snapshots": []})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/cameras/events")
async def api_camera_events():
    """Recent camera events from Frigate."""
    if not _V2_FRIGATE_ENABLED:
        return JSONResponse({"error": "Frigate cameras not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "camera_events", {})
        return JSONResponse(result if isinstance(result, dict) else {"events": []})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/system/metrics")
async def api_system_metrics():
    """CPU, RAM, disk metrics."""
    if not _V2_MONITORING_ENABLED:
        return JSONResponse({"error": "Monitoring not enabled"}, status_code=403)
    try:
        import shutil
        cpu_count = os.cpu_count() or 0
        load_avg = os.getloadavg()
        disk = shutil.disk_usage("/")
        return JSONResponse({
            "cpu_count": cpu_count,
            "load_avg_1m": round(load_avg[0], 2),
            "load_avg_5m": round(load_avg[1], 2),
            "load_avg_15m": round(load_avg[2], 2),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
        })
    except (OSError, RuntimeError) as e:
        return _safe_error(e)


@app.get("/api/system/docker")
async def api_system_docker():
    """Docker container status."""
    try:
        import subprocess
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=10, shell=False,
        )
        containers = []
        for line in (proc.stdout or "").strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                containers.append({"name": parts[0], "status": parts[1], "ports": parts[2] if len(parts) > 2 else ""})
        return JSONResponse({"containers": containers})
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        return _safe_error(e)


@app.get("/api/system/starlink")
async def api_system_starlink():
    """Starlink health status."""
    if not _V2_STARLINK_ENABLED:
        return JSONResponse({"error": "Starlink monitoring not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "starlink_status", {})
        return JSONResponse(result if isinstance(result, dict) else {"summary": str(result or "")})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/system/postgres")
async def api_system_postgres():
    """Postgres health and stats."""
    try:
        version = await fetch_val("SELECT version()")
        db_size = await fetch_val("SELECT pg_size_pretty(pg_database_size(current_database()))")
        active_conns = await fetch_val("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
        return JSONResponse({
            "status": "ok",
            "version": str(version or ""),
            "database_size": str(db_size or ""),
            "active_connections": int(active_conns or 0),
        })
    except (psycopg.Error, OSError) as e:
        return _safe_error(e)


@app.get("/api/network/devices")
async def api_network_devices():
    """Known devices on the network."""
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "network_devices", {})
        return JSONResponse(result if isinstance(result, dict) else {"devices": []})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/intelligence/findings")
async def api_intelligence_findings():
    """Business intelligence detector results."""
    if not _V2_INTELLIGENCE_ENABLED:
        return JSONResponse({"error": "Intelligence not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "bi_findings", {})
        return JSONResponse(result if isinstance(result, dict) else {"findings": []})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/intelligence/digest")
async def api_intelligence_digest():
    """Daily intelligence digest."""
    if not _V2_INTELLIGENCE_ENABLED:
        return JSONResponse({"error": "Intelligence not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "daily_digest", {})
        return JSONResponse(result if isinstance(result, dict) else {"digest": str(result or "")})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


@app.get("/api/revenue/dashboard")
async def api_revenue_dashboard():
    """Revenue metrics dashboard."""
    try:
        revenue_cleared = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
        ) or 0
        revenue_pending = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'pending'"
        ) or 0
        total_deals = await fetch_val("SELECT COUNT(*) FROM deals") or 0
        closed_deals = await fetch_val(
            "SELECT COUNT(*) FROM deals WHERE status IN ('paid', 'pending')"
        ) or 0
        mrr = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid' AND created_at > CURRENT_DATE - 30"
        ) or 0
        return JSONResponse({
            "revenue_cleared": float(revenue_cleared),
            "revenue_pending": float(revenue_pending),
            "total_deals": int(total_deals),
            "closed_deals": int(closed_deals),
            "mrr_30d": float(mrr),
        })
    except (psycopg.Error, OSError) as e:
        return _safe_error(e)


@app.get("/api/voice/status")
async def api_voice_status():
    """Voice session status."""
    if not _V2_VOICE_ENABLED:
        return JSONResponse({"error": "Voice not enabled"}, status_code=403)
    try:
        from shared.comms import call_agent_capability
        result = await call_agent_capability("hermes", "voice_status", {})
        return JSONResponse(result if isinstance(result, dict) else {"active": False})
    except (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        return _safe_error(e)


# ── Memory Explorer API ─────────────────────────────────────────────────

_DAEMON_COLORS = {
    "perseus": "#1097ff",
    "titan": "#ffb347",
    "hermes": "#62f1b5",
    "clawdbot": "#a78bfa",
    "conway": "#fbbf24",
    "deerflow": "#ff5a7a",
    "deerflow_research": "#ff5a7a",
    "system": "#6e96a5",
}

_MEMORY_TYPE_ICONS = {
    "episodic": "clock",
    "semantic": "book",
    "procedural": "cog",
    "feedback": "message-circle",
}


def _parse_memory_files() -> list[dict]:
    """Parse memory markdown files from the project memory directory."""
    import re
    from datetime import datetime

    memory_dir = Path(__file__).resolve().parent.parent.parent / ".claude" / "projects"
    # Find the project memory directory
    candidates = list(memory_dir.glob("*/memory"))
    if not candidates:
        # Fallback: try the direct memory path
        proj_slug = "-Users-majovega-Desktop-Projects-objective-hertz"
        alt = (
            Path(__file__).resolve().parent.parent.parent
            / ".claude" / "projects" / proj_slug / "memory"
        )
        if alt.exists():
            candidates = [alt]
    memories: list[dict] = []

    for mem_dir in candidates:
        for md_file in sorted(mem_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            # Parse YAML frontmatter
            meta: dict = {}
            body = content
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k in ("confidence", "source_reliability"):
                            try:
                                v = float(v)
                            except ValueError:
                                pass
                        elif k in ("pinned",):
                            v = v.lower() in ("true", "yes", "1")
                        elif k in ("version", "decay_half_life_days"):
                            try:
                                v = int(v)
                            except ValueError:
                                pass
                        meta[k] = v
                body = fm_match.group(2)

            # Determine daemon from name or domain
            name = meta.get("name", md_file.stem)
            domain = str(meta.get("domain", ""))
            daemon = "system"
            for d in ("perseus", "titan", "hermes", "clawdbot", "conway", "deerflow"):
                if d in name.lower() or d in domain.lower() or d in md_file.stem.lower():
                    daemon = d
                    break

            # Extract first paragraph as summary
            lines = [
                ln.strip() for ln in body.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            summary = lines[0][:200] if lines else ""

            # Determine date
            date_str = meta.get("last_validated", "")
            if not date_str:
                # Try to get from file mtime
                try:
                    mtime = md_file.stat().st_mtime
                    date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                except OSError:
                    date_str = "2026-01-01"

            memories.append({
                "id": md_file.stem,
                "name": str(meta.get("description", meta.get("name", md_file.stem))),
                "type": str(meta.get("type", "semantic")),
                "confidence": float(meta.get("confidence", 0.5)),
                "daemon": daemon,
                "domain": domain or daemon,
                "date": date_str,
                "pinned": bool(meta.get("pinned", False)),
                "version": int(meta.get("version", 1)),
                "summary": summary,
                "body": body[:2000],
                "file": md_file.name,
                "color": _DAEMON_COLORS.get(daemon, "#6e96a5"),
            })

    return memories


@app.get("/api/memory/search")
async def api_memory_search(request: Request):
    """Search memories by text query, daemon, type, confidence range."""
    try:
        params = request.query_params
        q = str(params.get("q", "")).strip().lower()
        daemon = str(params.get("daemon", "")).strip().lower()
        mem_type = str(params.get("type", "")).strip().lower()
        min_conf = float(params.get("min_confidence", "0"))
        max_conf = float(params.get("max_confidence", "1"))

        memories = _parse_memory_files()
        results = []
        for m in memories:
            if daemon and m["daemon"] != daemon:
                continue
            if mem_type and m["type"] != mem_type:
                continue
            if m["confidence"] < min_conf or m["confidence"] > max_conf:
                continue
            if q:
                name_l = m["name"].lower()
                summ_l = m["summary"].lower()
                body_l = m["body"].lower()
                if q not in name_l and q not in summ_l and q not in body_l:
                    continue
            results.append(m)

        results.sort(key=lambda x: (-x["confidence"], x["name"]))
        return JSONResponse(results)
    except (OSError, ValueError) as e:
        return _safe_error(e)


@app.get("/api/memory/graph")
async def api_memory_graph(request: Request):
    """Return nodes and edges for the memory graph visualization."""
    try:
        memories = _parse_memory_files()

        nodes = []
        edges = []
        daemon_groups: dict[str, list[str]] = {}

        for m in memories:
            nodes.append({
                "id": m["id"],
                "label": m["name"][:40],
                "title": (
                    f"{m['name']}\n\nType: {m['type']}\n"
                    f"Confidence: {m['confidence']:.0%}\n"
                    f"Daemon: {m['daemon']}"
                ),
                "color": m["color"],
                "size": max(12, int(m["confidence"] * 30)),
                "daemon": m["daemon"],
                "type": m["type"],
                "confidence": m["confidence"],
                "pinned": m["pinned"],
            })
            daemon_groups.setdefault(m["daemon"], []).append(m["id"])

        # Create edges between memories in the same daemon group
        for daemon, ids in daemon_groups.items():
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    edges.append({
                        "from": a,
                        "to": b,
                        "color": {"color": _DAEMON_COLORS.get(daemon, "#6e96a5"), "opacity": 0.2},
                        "width": 1,
                    })

        # Create cross-daemon edges for memories that reference each other
        name_index = {m["id"]: m for m in memories}
        for m in memories:
            body_lower = m["body"].lower()
            for other_id, other in name_index.items():
                if other_id == m["id"]:
                    continue
                # Check if this memory references another by stem name
                ref_space = other_id.replace("_", " ")
                ref_dash = other_id.replace("_", "-")
                if ref_space in body_lower or ref_dash in body_lower:
                    edges.append({
                        "from": m["id"],
                        "to": other_id,
                        "color": {"color": "#58e0ff", "opacity": 0.4},
                        "width": 2,
                        "dashes": True,
                    })

        return JSONResponse({
            "nodes": nodes,
            "edges": edges,
            "daemon_colors": _DAEMON_COLORS,
        })
    except (OSError, ValueError) as e:
        return _safe_error(e)


@app.get("/api/memory/timeline")
async def api_memory_timeline(request: Request):
    """Return timeline data grouped by daemon with date-sorted events."""
    try:
        memories = _parse_memory_files()

        # Group by daemon, sort by date
        lanes: dict[str, list[dict]] = {}
        for m in memories:
            lane = m["daemon"]
            lanes.setdefault(lane, [])
            lanes[lane].append({
                "id": m["id"],
                "name": m["name"][:50],
                "date": m["date"],
                "type": m["type"],
                "confidence": m["confidence"],
                "color": m["color"],
                "pinned": m["pinned"],
            })

        for lane in lanes.values():
            lane.sort(key=lambda x: x["date"])

        # Build ordered lane list
        lane_order = ["perseus", "titan", "hermes", "clawdbot", "conway", "deerflow", "system"]
        result = []
        for daemon in lane_order:
            if daemon in lanes:
                result.append({
                    "daemon": daemon,
                    "color": _DAEMON_COLORS.get(daemon, "#6e96a5"),
                    "events": lanes[daemon],
                })

        # Add any lanes not in the predefined order
        for daemon, events in lanes.items():
            if daemon not in lane_order:
                result.append({
                    "daemon": daemon,
                    "color": _DAEMON_COLORS.get(daemon, "#6e96a5"),
                    "events": events,
                })

        return JSONResponse(result)
    except (OSError, ValueError) as e:
        return _safe_error(e)
