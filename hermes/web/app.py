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
import hashlib
from decimal import Decimal
import hmac
import json
import logging
import os
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
    except Exception as exc:
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
    except Exception:
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
    ).hexdigest()[:32]

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
    except Exception as e:
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
        except Exception:
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
    except Exception as e:
        logger.error("WebSocket sync build failed: %s", e)
        return {"type": "sync", "error": str(e)}


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
    except Exception as e:
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
    except Exception:
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
    "kling_access": {"env": "KLING_ACCESS_KEY", "label": "Kling AI (Access Key)", "required": False, "category": "ai"},
    "kling_secret": {"env": "KLING_SECRET_KEY", "label": "Kling AI (Secret Key)", "required": False, "category": "ai"},
    "recraft": {"env": "RECRAFT_API_KEY", "label": "Recraft AI (Images)", "required": False, "category": "ai"},
    "vast_ai": {"env": "VAST_AI_API_KEY", "label": "Vast.ai (Cloud GPU)", "required": False, "category": "ai"},
    "v0": {"env": "V0_API_KEY", "label": "v0.dev (Vercel AI)", "required": False, "category": "ai"},
    # Scraping & Research
    "firecrawl": {"env": "FIRECRAWL_API_KEY", "label": "Firecrawl (Web Scraping)", "required": True, "category": "scraping"},
    "firecrawl_self_host": {"env": "FIRECRAWL_SELF_HOST_URL", "label": "Firecrawl Self-Host URL", "required": False, "category": "scraping"},
    # Outreach
    "instantly": {"env": "INSTANTLY_API_KEY", "label": "Instantly (Email Campaigns)", "required": True, "category": "outreach"},
    # Payments
    "stripe": {"env": "STRIPE_API_KEY", "label": "Stripe (Payments)", "required": True, "category": "payments"},
    "wise": {"env": "WISE_API_TOKEN", "label": "Wise (Payouts)", "required": False, "category": "payments"},
    "wise_profile": {"env": "WISE_PROFILE_ID", "label": "Wise Profile ID", "required": False, "category": "payments"},
    # Hosting & DNS
    "netlify": {"env": "NETLIFY_AUTH_TOKEN", "label": "Netlify (Site Hosting)", "required": True, "category": "hosting"},
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
    "sentry": {"env": "SENTRY_DSN", "label": "Sentry DSN", "required": False, "category": "observability"},
    "dashboard_secret": {"env": "DASHBOARD_SECRET", "label": "War Room Dashboard Secret", "required": True, "category": "system"},
    # Crypto / Conway
    "conway_api": {"env": "CONWAY_API_KEY", "label": "Conway API Key", "required": False, "category": "crypto"},
    "base_rpc": {"env": "BASE_RPC_URL", "label": "Base L2 RPC URL", "required": False, "category": "crypto"},
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
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key = body.get("key", "")
    if key not in _CONFIG_ALLOWLIST:
        return JSONResponse({"error": f"Unknown config key: {key}"}, status_code=400)

    value = body.get("value")
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
    except Exception as e:
        # Fallback if budget_guard not available
        return JSONResponse({
            "total_spent": 0, "remaining": 800, "percent_used": 0,
            "exceeded": False, "categories": [], "error": str(e),
        })


@app.get("/api/tasks")
async def api_tasks(request: Request):
    """Task queue visibility — filter by status and agent."""
    status = request.query_params.get("status")
    agent = request.query_params.get("agent")
    limit = int(request.query_params.get("limit", "50"))

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
    valid_daemons = {"perseus", "titan", "hermes", "clawdbot"}
    if name not in valid_daemons:
        return JSONResponse({"error": f"Unknown daemon: {name}"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
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
    except Exception:
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
            except Exception as e:
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
                except Exception as e:
                    logger.error("Approve failed for review %s: %s", rid, e)
                    failed += 1
            await emit_event("bulk_approve", {"ids": ids, "source": "war_room"})
            return JSONResponse({"success": True, "action": action, "affected": succeeded, "failed": failed})
        else:
            from titan.review_mode import reject_review

            for rid in ids:
                try:
                    await reject_review(rid, notes="Rejected from War Room")
                except Exception as e:
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
    except Exception as e:
        return JSONResponse({"error": str(e), "campaigns": []})


@app.post("/api/campaigns/{campaign_id}/action")
async def api_campaign_action(campaign_id: str, request: Request):
    """Activate or stop an email campaign."""
    try:
        body = await request.json()
    except Exception:
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
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/campaigns/{campaign_id}/analytics")
async def api_campaign_analytics(campaign_id: str):
    """Campaign analytics from Instantly."""
    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()
        analytics = await client.get_campaign_analytics(campaign_id)
        return JSONResponse(analytics)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


@app.post("/api/keys")
async def api_keys_update(request: Request):
    """Update an API key. Stored in system_config (DB), also set in os.environ for current process."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key_id = body.get("key_id", "")
    value = body.get("value", "")

    if key_id not in _API_KEY_REGISTRY:
        return JSONResponse({"error": f"Unknown key: {key_id}"}, status_code=400)

    if not value:
        return JSONResponse({"error": "Value cannot be empty"}, status_code=400)

    meta = _API_KEY_REGISTRY[key_id]

    # Store in DB for persistence across restarts
    await set_config(f"api_key_{key_id}", value)

    # Also set in current process env so services pick it up immediately
    os.environ[str(meta["env"])] = value

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
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "Empty question"}, status_code=400)

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
            response = await llm.generate(prompt, model="local", max_tokens=500, temperature=0.3)

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

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
