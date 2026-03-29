"""
Instantly.ai API v2 client for Perseus/Titan.

IMPORTANT: Instantly is campaign-based, NOT a direct email sender.
Flow: Create campaign → Add leads with variables → Instantly handles sending,
      timing, warmup, account rotation, and delivery optimization.

API docs: https://developer.instantly.ai/
Base URL: https://api.instantly.ai/api/v2
Auth: Bearer token via INSTANTLY_API_KEY
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from shared.config import config

logger = logging.getLogger("perseus.tools.instantly")

BASE_URL = "https://api.instantly.ai/api/v2"
DEFAULT_MIN_INTERVAL_SECONDS = 0.2
DEFAULT_MAX_RETRIES = 3


class InstantlyClient:
    """Instantly.ai API v2 client for cold email automation."""

    _rate_limit_lock: asyncio.Lock | None = None
    _global_last_request_at: float = 0.0

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or config.instantly.api_key
        self._min_interval_seconds = DEFAULT_MIN_INTERVAL_SECONDS
        self._max_retries = DEFAULT_MAX_RETRIES
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def _wait_for_rate_limit_slot(self):
        """Throttle back-to-back requests across all Instantly clients in this process."""
        if InstantlyClient._rate_limit_lock is None:
            InstantlyClient._rate_limit_lock = asyncio.Lock()

        async with InstantlyClient._rate_limit_lock:
            now = time.monotonic()
            elapsed = now - InstantlyClient._global_last_request_at
            remaining = self._min_interval_seconds - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)
                now = time.monotonic()
            InstantlyClient._global_last_request_at = now

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Honor Retry-After when present, otherwise back off progressively."""
        retry_after = resp.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
        return float(attempt)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
    ) -> Any:
        """Central request path with light throttling and 429 retry handling."""
        request_fn = getattr(self._http, method)
        url = f"{BASE_URL}{path}"

        for attempt in range(1, self._max_retries + 1):
            await self._wait_for_rate_limit_slot()
            resp = await request_fn(url, params=params or None, json=data or None)

            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()

            if attempt == self._max_retries:
                resp.raise_for_status()

            delay = self._retry_delay(resp, attempt)
            logger.warning(
                "Instantly rate limited %s %s, retrying in %.2fs (attempt %d/%d)",
                method.upper(),
                path,
                delay,
                attempt,
                self._max_retries,
            )
            await asyncio.sleep(delay)

        raise RuntimeError(f"Instantly request failed after retries: {method.upper()} {path}")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("get", path, params=params)

    async def _post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return await self._request("post", path, data=data)

    async def _patch(self, path: str, data: dict[str, Any] | None = None) -> Any:
        return await self._request("patch", path, data=data)

    async def _delete(self, path: str) -> Any:
        return await self._request("delete", path)

    # ── Campaigns ──────────────────────────────────────────────────

    async def create_campaign(self, name: str) -> dict:
        """Create a new campaign with built-in sequences DISABLED.

        Perseus owns all sequencing logic (follow_up.py).  Instantly is a
        dumb sending pipe — it must not auto-send its own follow-ups or
        the prospect gets double emails.
        """
        campaign = await self._post("/campaigns", {"name": name})
        campaign_id = campaign.get("id", "")
        if campaign_id:
            try:
                await self.disable_sequences(campaign_id)
            except Exception as e:
                logger.warning("Could not disable sequences for campaign %s: %s", campaign_id, e)
        return campaign

    async def get_campaign(self, campaign_id: str) -> dict:
        """Get campaign details."""
        return await self._get(f"/campaigns/{campaign_id}")

    async def list_campaigns(self) -> Any:
        """List all campaigns."""
        return await self._get("/campaigns")

    async def disable_sequences(self, campaign_id: str) -> dict:
        """Disable Instantly's built-in auto-sequences on a campaign.

        Perseus owns follow-up timing and content via follow_up.py.
        Instantly must only send what Perseus explicitly adds as leads.
        """
        return await self._patch(f"/campaigns/{campaign_id}", {
            "sequences": [],
            "auto_follow_up": False,
        })

    async def activate_campaign(self, campaign_id: str) -> dict:
        """Activate/resume a campaign to start sending."""
        return await self._post(f"/campaigns/{campaign_id}/activate")

    async def stop_campaign(self, campaign_id: str) -> dict:
        """Stop/pause a campaign."""
        return await self._post(f"/campaigns/{campaign_id}/stop")

    async def get_campaign_analytics(self, campaign_id: str = "") -> dict:
        """Get campaign analytics (sent, opens, clicks, replies)."""
        params: dict[str, Any] = {}
        if campaign_id:
            params["id"] = campaign_id
        return await self._get("/campaigns/analytics", params)

    async def get_campaign_daily_analytics(self, campaign_id: str = "") -> dict:
        """Get daily campaign analytics."""
        params: dict[str, Any] = {}
        if campaign_id:
            params["id"] = campaign_id
        return await self._get("/campaigns/daily-analytics", params)

    async def get_sending_status(self, campaign_id: str) -> dict:
        """Check why a campaign may not be sending."""
        return await self._get(f"/campaigns/{campaign_id}/sending-status")

    async def update_campaign(self, campaign_id: str, **fields) -> dict:
        """Update campaign fields (name, sequences, schedule, etc.)."""
        return await self._patch(f"/campaigns/{campaign_id}", fields)

    # ── Leads ──────────────────────────────────────────────────────

    async def add_lead(self, campaign_id: str, email: str, **variables) -> dict:
        """Add a single lead to a campaign with custom variables."""
        data = {
            "campaign_id": campaign_id,
            "email": email,
        }
        # Instantly uses custom variables for personalization in sequences
        # e.g. {{first_name}}, {{company_name}}, {{personalization}}
        if variables:
            data.update(variables)
        return await self._post("/leads", data)

    async def add_leads_bulk(self, campaign_id: str, leads: list[dict]) -> dict:
        """
        Add up to 1000 leads to a campaign.
        Each lead dict should have 'email' and any custom variables.
        """
        for lead in leads:
            lead["campaign_id"] = campaign_id
        return await self._post("/leads/bulk", {"leads": leads})

    async def list_leads(self, campaign_id: str = "", limit: int = 100) -> Any:
        """List leads. Note: This is a POST endpoint due to complex filtering."""
        data: dict[str, Any] = {"limit": limit}
        if campaign_id:
            data["campaign_id"] = campaign_id
        return await self._post("/leads/list", data)

    async def get_lead(self, lead_id: str) -> dict:
        """Get a single lead."""
        return await self._get(f"/leads/{lead_id}")

    async def update_lead_interest(self, lead_id: str, status: str) -> dict:
        """Update lead interest status (e.g. 'interested', 'not_interested')."""
        return await self._patch(f"/leads/{lead_id}/interest-status", {"status": status})

    # ── Email Accounts ─────────────────────────────────────────────

    async def list_accounts(self) -> Any:
        """List connected email sending accounts."""
        return await self._get("/accounts")

    async def get_account(self, account_id: str) -> dict:
        """Get a single email account."""
        return await self._get(f"/accounts/{account_id}")

    async def pause_account(self, account_id: str) -> dict:
        """Pause an email account."""
        return await self._post(f"/accounts/{account_id}/pause")

    async def resume_account(self, account_id: str) -> dict:
        """Resume a paused email account."""
        return await self._post(f"/accounts/{account_id}/resume")

    async def test_account_vitals(self, account_id: str) -> dict:
        """Test account deliverability and health."""
        return await self._get(f"/accounts/{account_id}/test-vitals")

    # ── Warmup ─────────────────────────────────────────────────────

    async def enable_warmup(self) -> dict:
        """Enable warmup for accounts. Returns background job."""
        return await self._post("/accounts/warmup/enable")

    async def disable_warmup(self) -> dict:
        """Disable warmup. Returns background job."""
        return await self._post("/accounts/warmup/disable")

    async def get_warmup_analytics(self) -> dict:
        """Get warmup analytics across accounts."""
        return await self._get("/accounts/warmup/analytics")

    async def get_daily_account_analytics(self) -> dict:
        """Get daily sending analytics per account."""
        return await self._get("/accounts/daily-analytics")

    # ── Emails / Inbox ─────────────────────────────────────────────

    async def list_emails(self, campaign_id: str = "", is_unread: bool | None = None,
                          limit: int = 50) -> Any:
        """
        List emails (replies, sent). Rate limited to 20 req/min.
        Use to check for new replies.
        """
        params: dict[str, Any] = {"limit": limit}
        if campaign_id:
            params["campaign_id"] = campaign_id
        if is_unread is not None:
            params["is_unread"] = is_unread
        return await self._get("/emails", params)

    async def get_email(self, email_id: str) -> dict:
        """Get a specific email by ID."""
        return await self._get(f"/emails/{email_id}")

    async def reply_to_email(self, reply_to_uuid: str, body: str) -> dict:
        """Reply to an existing email thread."""
        return await self._post("/emails/reply", {
            "reply_to_uuid": reply_to_uuid,
            "body": body,
        })

    async def mark_thread_read(self, email_id: str) -> dict:
        """Mark an email thread as read."""
        return await self._post(f"/emails/{email_id}/mark-read")

    async def send_test_email(self, account_id: str, to_email: str,
                               subject: str, body: str) -> dict:
        """Send a test email (rate limited to 10/min). NOT for outreach."""
        return await self._post("/emails/test", {
            "account_id": account_id,
            "to": to_email,
            "subject": subject,
            "body": body,
        })

    # ── Background Jobs ────────────────────────────────────────────

    async def get_job_status(self, job_id: str) -> dict:
        """Check status of a background job (warmup, lead moves, etc.)."""
        return await self._get(f"/background-jobs/{job_id}")

    # ── Email Verification ─────────────────────────────────────────

    async def verify_email(self, email: str) -> dict:
        """Verify an email address. May return 'pending' if >10s."""
        return await self._post("/email-verification", {"email": email})

    async def get_verification_status(self, email: str) -> dict:
        """Check verification status for a previously submitted email."""
        return await self._get(f"/email-verification/{email}")

    # ── DFY (Done-For-You) Email Accounts ──────────────────────────

    async def check_domain_availability(self, domains: list[str]) -> dict:
        """Check if domains are available for DFY email account setup."""
        return await self._post("/dfy-orders/check-domains", {"domains": domains})

    async def order_dfy_accounts(self, domain: str, count: int = 3) -> dict:
        """Order pre-warmed DFY email accounts (up to 5 per domain)."""
        return await self._post("/dfy-orders", {
            "domain": domain,
            "count": min(count, 5),
        })

    async def close(self):
        await self._http.aclose()


async def generate_domain_report(domains: list[str]) -> str:
    """Generate a domain health report using Instantly.ai warmup analytics."""
    if not domains:
        return "DOMAIN HEALTH\n└── No sending domains configured"

    client = InstantlyClient()
    lines = ["DOMAIN HEALTH"]

    try:
        # Get warmup analytics for health overview
        await client.get_warmup_analytics()
        accounts = await client.list_accounts()

        for account in (accounts if isinstance(accounts, list) else []):
            email = account.get("email", "")
            domain = email.split("@")[-1] if "@" in email else ""
            if domain not in domains:
                continue

            account_id = account.get("id", "")
            status = account.get("status", "unknown")

            # Test vitals if account is active
            vitals = {}
            if account_id and status != "paused":
                try:
                    vitals = await client.test_account_vitals(account_id)
                except Exception:
                    pass

            health = "OK" if status == "active" else ("PAUSED" if status == "paused" else "WARNING")
            lines.append(
                f"├── {domain} ({email}): {health} "
                f"status={status} "
                f"vitals={vitals.get('result', 'untested')}"
            )

    except Exception as e:
        lines.append(f"├── Error fetching domain health: {e}")
    finally:
        await client.close()

    if len(lines) == 1:
        lines.append("└── No matching accounts found for configured domains")

    return "\n".join(lines)
