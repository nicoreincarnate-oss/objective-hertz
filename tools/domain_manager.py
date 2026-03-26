"""Domain management via Cloudflare DNS API.

Creates and manages DNS records for custom domains pointing to Netlify sites.
Cloudflare proxied mode provides instant SSL and CDN without waiting for DNS propagation.
"""

import logging
from typing import Any

import httpx

from shared.config import config

logger = logging.getLogger("perseus.tools.domain_manager")

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


def _headers() -> dict[str, str]:
    """Cloudflare API auth headers."""
    token = getattr(config, "cloudflare", None) and getattr(config.cloudflare, "api_token", "")
    if not token:
        raise RuntimeError("CLOUDFLARE_API_TOKEN not configured")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def get_zone_id(domain: str) -> str | None:
    """Look up the Cloudflare zone ID for a domain's root (e.g., 'example.com')."""
    # Extract root domain (last two segments)
    parts = domain.rstrip(".").split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else domain

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.get(
            f"{CLOUDFLARE_API}/zones",
            params={"name": root, "status": "active"},
            headers=_headers(),
        )
        if resp.status_code != 200:
            logger.error("Cloudflare zone lookup failed: %s", resp.text[:200])
            return None
        zones = resp.json().get("result", [])
        return zones[0]["id"] if zones else None


async def create_cname_record(
    domain: str,
    target: str,
    *,
    proxied: bool = True,
) -> dict[str, Any]:
    """Create a CNAME record pointing domain → target (e.g., 'site.netlify.app').

    With proxied=True, Cloudflare handles SSL and CDN — no DNS propagation wait.
    """
    zone_id = await get_zone_id(domain)
    if not zone_id:
        return {"ok": False, "error": f"No Cloudflare zone found for {domain}"}

    async with httpx.AsyncClient(timeout=15.0) as http:
        # Check for existing record first
        existing = await http.get(
            f"{CLOUDFLARE_API}/zones/{zone_id}/dns_records",
            params={"name": domain, "type": "CNAME"},
            headers=_headers(),
        )
        if existing.status_code == 200:
            records = existing.json().get("result", [])
            if records:
                # Update existing record
                record_id = records[0]["id"]
                resp = await http.put(
                    f"{CLOUDFLARE_API}/zones/{zone_id}/dns_records/{record_id}",
                    json={"type": "CNAME", "name": domain, "content": target, "proxied": proxied},
                    headers=_headers(),
                )
                if resp.status_code == 200:
                    logger.info("Updated CNAME %s → %s (proxied=%s)", domain, target, proxied)
                    return {"ok": True, "action": "updated", "record_id": record_id}
                return {"ok": False, "error": resp.text[:200]}

        # Create new record
        resp = await http.post(
            f"{CLOUDFLARE_API}/zones/{zone_id}/dns_records",
            json={"type": "CNAME", "name": domain, "content": target, "proxied": proxied},
            headers=_headers(),
        )
        if resp.status_code in (200, 201):
            record = resp.json().get("result", {})
            logger.info("Created CNAME %s → %s (proxied=%s)", domain, target, proxied)
            return {"ok": True, "action": "created", "record_id": record.get("id", "")}

        error = resp.text[:200]
        logger.error("Failed to create CNAME for %s: %s", domain, error)
        return {"ok": False, "error": error}


async def provision_custom_domain(
    domain: str,
    netlify_site_id: str,
    netlify_subdomain: str,
) -> dict[str, Any]:
    """Full domain provisioning: Cloudflare CNAME + Netlify custom domain.

    Args:
        domain: The customer's domain (e.g., 'bobsplumbing.com')
        netlify_site_id: The Netlify site ID
        netlify_subdomain: The Netlify subdomain (e.g., 'perseus-bobs-plumbing.netlify.app')

    Returns:
        {"ok": True/False, "dns": {...}, "netlify": {...}}
    """
    from clawdbot.netlify_deploy import set_custom_domain

    # Step 1: Set custom domain on Netlify
    netlify_result = await set_custom_domain(netlify_site_id, domain)
    if not netlify_result.get("ok"):
        return {"ok": False, "netlify": netlify_result, "dns": None}

    # Step 2: Create Cloudflare CNAME pointing to Netlify
    dns_target = netlify_result.get("dns_target", netlify_subdomain)
    dns_result = await create_cname_record(domain, dns_target, proxied=True)

    ok = dns_result.get("ok", False) and netlify_result.get("ok", False)
    if ok:
        logger.info("Domain %s fully provisioned (Netlify + Cloudflare)", domain)
    else:
        logger.warning("Domain %s partially provisioned: dns=%s netlify=%s",
                       domain, dns_result.get("ok"), netlify_result.get("ok"))

    return {"ok": ok, "dns": dns_result, "netlify": netlify_result}
