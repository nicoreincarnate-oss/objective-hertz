"""Netlify file-digest deploy for multi-page static sites."""

import hashlib
import logging
import re
from typing import Any

import httpx

from shared.config import config

logger = logging.getLogger("perseus.clawdbot.netlify_deploy")

NETLIFY_API = "https://api.netlify.com/api/v1"


def _slugify(name: str) -> str:
    """Turn a business name into a Netlify-safe subdomain slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40] or "perseus-site"


def _sha1(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


async def deploy_static_site(
    pages: dict[str, str],
    site_name: str,
    *,
    client_id: int | None = None,
) -> str:
    """Deploy a dict of {filename: html} to Netlify. Returns the live URL.

    Uses Netlify's file-digest deploy API:
    1. Create (or reuse) a site
    2. POST a deploy with SHA1 digests of each file
    3. Upload each file body
    4. Return the published URL
    """
    token = config.hosting.netlify_token
    if not token:
        raise RuntimeError("NETLIFY_AUTH_TOKEN not configured")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    slug = _slugify(site_name)

    async with httpx.AsyncClient(timeout=60.0) as http:
        # Step 1: Find or create the Netlify site
        site_id = await _get_existing_site_id(client_id)

        if not site_id:
            resp = await http.post(
                f"{NETLIFY_API}/sites",
                json={"name": f"perseus-{slug}", "custom_domain": None},
                headers=headers,
            )
            if resp.status_code == 422:
                # Name taken — append client_id
                suffix = client_id or "x"
                resp = await http.post(
                    f"{NETLIFY_API}/sites",
                    json={"name": f"perseus-{slug}-{suffix}"},
                    headers=headers,
                )
            resp.raise_for_status()
            site_data = resp.json()
            site_id = site_data["id"]
            site_url = site_data.get("ssl_url") or site_data.get("url", "")
            # NOTE: Do NOT persist hosting state here. The site is created
            # but no deploy has succeeded yet. _store_site_id is called
            # after all uploads complete successfully (see Step 4 below).
        else:
            site_url = ""

        # Step 2: Create a deploy with file digests
        file_digests = {}
        for filename, html in pages.items():
            path = f"/{filename}" if not filename.startswith("/") else filename
            file_digests[path] = _sha1(html)

        deploy_resp = await http.post(
            f"{NETLIFY_API}/sites/{site_id}/deploys",
            json={"files": file_digests},
            headers=headers,
        )
        deploy_resp.raise_for_status()
        deploy_data = deploy_resp.json()
        deploy_id = deploy_data["id"]

        # Step 3: Upload each file that Netlify needs
        required = deploy_data.get("required", list(file_digests.keys()))
        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        }

        failed_uploads = []
        for path, digest in file_digests.items():
            if required and path not in required:
                continue
            filename = path.lstrip("/")
            body = pages.get(filename, pages.get(path, ""))
            upload_resp = await http.put(
                f"{NETLIFY_API}/deploys/{deploy_id}/files{path}",
                content=body.encode("utf-8"),
                headers=upload_headers,
            )
            if upload_resp.status_code >= 400:
                failed_uploads.append((path, upload_resp.status_code))
                logger.error(f"Netlify file upload failed: {path} → HTTP {upload_resp.status_code}")

        if failed_uploads:
            raise RuntimeError(
                f"Netlify deploy incomplete: {len(failed_uploads)}/{len(file_digests)} "
                f"uploads failed: {failed_uploads[:5]}"
            )

        # Step 4: Deploy succeeded — now persist hosting state
        if client_id:
            await _store_site_id(client_id, site_id, site_url)

        # Step 5: Return the live URL
        live_url = deploy_data.get("ssl_url") or deploy_data.get("url") or site_url
        if not live_url:
            # Poll deploy for URL
            check = await http.get(
                f"{NETLIFY_API}/deploys/{deploy_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if check.status_code == 200:
                live_url = check.json().get("ssl_url", "")

        logger.info("Deployed %d pages to Netlify: %s", len(pages), live_url)
        return live_url


async def _get_existing_site_id(client_id: int | None) -> str | None:
    """Check if this client already has a Netlify site."""
    if not client_id:
        return None
    try:
        from shared.db import fetch_val
        return await fetch_val(
            "SELECT netlify_site_id FROM hosting_subscriptions WHERE client_id = %s AND netlify_site_id IS NOT NULL LIMIT 1",
            (client_id,),
        )
    except Exception:
        return None


async def _store_site_id(client_id: int, site_id: str, site_url: str) -> None:
    """Store the Netlify site ID for future redeploys."""
    try:
        from shared.db import execute
        await execute(
            """INSERT INTO hosting_subscriptions (client_id, netlify_site_id, deploy_url, status)
               VALUES (%s, %s, %s, 'active')
               ON CONFLICT (client_id) DO UPDATE
               SET netlify_site_id = EXCLUDED.netlify_site_id,
                   deploy_url = EXCLUDED.deploy_url""",
            (client_id, site_id, site_url),
        )
    except Exception as e:
        logger.warning("Failed to store Netlify site ID for client %s: %s", client_id, e)


async def set_custom_domain(site_id: str, domain: str) -> dict:
    """Configure a custom domain on an existing Netlify site.

    Steps:
    1. Add the custom domain to the Netlify site
    2. Netlify auto-provisions a Let's Encrypt SSL certificate
    3. Return the domain status for the caller to set up DNS

    The caller (or tools/domain_manager.py) is responsible for creating
    the DNS CNAME record pointing `domain` → the Netlify subdomain.
    """
    token = config.hosting.netlify_token
    if not token:
        raise RuntimeError("NETLIFY_AUTH_TOKEN not configured")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as http:
        # Add custom domain to the Netlify site
        resp = await http.put(
            f"{NETLIFY_API}/sites/{site_id}",
            json={"custom_domain": domain},
            headers=headers,
        )
        if resp.status_code >= 400:
            error = resp.text[:300]
            logger.error("Netlify set_custom_domain failed for %s: %s", domain, error)
            return {"ok": False, "error": error, "domain": domain}

        site_data = resp.json()
        ssl_url = site_data.get("ssl_url", "")
        domain_aliases = site_data.get("domain_aliases", [])

        # Enable HTTPS (Netlify auto-provisions Let's Encrypt)
        ssl_resp = await http.post(
            f"{NETLIFY_API}/sites/{site_id}/ssl",
            headers=headers,
        )
        ssl_provisioned = ssl_resp.status_code < 400

        logger.info(
            "Custom domain %s set on site %s (ssl=%s, url=%s)",
            domain, site_id, ssl_provisioned, ssl_url,
        )

        # Persist domain to hosting_subscriptions
        try:
            from shared.db import execute
            await execute(
                """UPDATE hosting_subscriptions
                   SET domain = %s
                   WHERE netlify_site_id = %s""",
                (domain, site_id),
            )
        except Exception as e:
            logger.warning("Failed to persist domain for site %s: %s", site_id, e)

        return {
            "ok": True,
            "domain": domain,
            "ssl_url": ssl_url,
            "ssl_provisioned": ssl_provisioned,
            "dns_target": f"{site_data.get('name', '')}.netlify.app",
            "domain_aliases": domain_aliases,
        }
