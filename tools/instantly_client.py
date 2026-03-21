"""
Instantly.ai API v2 client for Perseus/Titan.

IMPORTANT: Instantly is campaign-based, NOT a direct email sender.
Flow: Create campaign → Add leads with variables → Instantly handles sending,
      timing, warmup, account rotation, and delivery optimization.

API docs: https://developer.instantly.ai/
Base URL: https://api.instantly.ai/api/v2
Auth: Bearer token via INSTANTLY_API_KEY
"""

import logging
from typing import Optional

import httpx

from shared.config import config

logger = logging.getLogger("perseus.tools.instantly")

BASE_URL = "https://api.instantly.ai/api/v2"


class InstantlyClient:
    """Instantly.ai API v2 client for cold email automation."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or config.instantly.api_key
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def _get(self, path: str, params: dict = None) -> dict:
        resp = await self._http.get(f"{BASE_URL}{path}", params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict = None) -> dict:
        resp = await self._http.post(f"{BASE_URL}{path}", json=data or {})
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, path: str, data: dict = None) -> dict:
        resp = await self._http.patch(f"{BASE_URL}{path}", json=data or {})
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> dict:
        resp = await self._http.delete(f"{BASE_URL}{path}")
        resp.raise_for_status()
        return resp.json()

    # ── Campaigns ──────────────────────────────────────────────────

    async def create_campaign(self, name: str) -> dict:
        """Create a new campaign. Returns campaign object with id."""
        return await self._post("/campaigns", {"name": name})

    async def get_campaign(self, campaign_id: str) -> dict:
        """Get campaign details."""
        return await self._get(f"/campaigns/{campaign_id}")

    async def list_campaigns(self) -> list:
        """List all campaigns."""
        return await self._get("/campaigns")

    async def activate_campaign(self, campaign_id: str) -> dict:
        """Activate/resume a campaign to start sending."""
        return await self._post(f"/campaigns/{campaign_id}/activate")

    async def stop_campaign(self, campaign_id: str) -> dict:
        """Stop/pause a campaign."""
        return await self._post(f"/campaigns/{campaign_id}/stop")

    async def get_campaign_analytics(self, campaign_id: str = "") -> dict:
        """Get campaign analytics (sent, opens, clicks, replies)."""
        params = {}
        if campaign_id:
            params["id"] = campaign_id
        return await self._get("/campaigns/analytics", params)

    async def get_campaign_daily_analytics(self, campaign_id: str = "") -> dict:
        """Get daily campaign analytics."""
        params = {}
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

    async def list_leads(self, campaign_id: str = "", limit: int = 100) -> list:
        """List leads. Note: This is a POST endpoint due to complex filtering."""
        data = {"limit": limit}
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

    async def list_accounts(self) -> list:
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

    async def list_emails(self, campaign_id: str = "", is_unread: bool = None,
                          limit: int = 50) -> list:
        """
        List emails (replies, sent). Rate limited to 20 req/min.
        Use to check for new replies.
        """
        params = {"limit": limit}
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
        warmup = await client.get_warmup_analytics()
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
