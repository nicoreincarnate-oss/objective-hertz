"""Tests for custom domain provisioning — Phase 6.

Verifies:
1. set_custom_domain exists and calls Netlify API correctly
2. provision_custom_domain chains Netlify + Cloudflare
3. A2A capabilities are registered for domain operations
4. Task routing includes domain operations
5. Domain is persisted to hosting_subscriptions
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import respx


# ── 1. A2A capability registration ──

def test_domain_capabilities_in_handlers():
    from clawdbot.a2a_server import CAPABILITY_HANDLERS
    assert "set_custom_domain" in CAPABILITY_HANDLERS
    assert "provision_domain" in CAPABILITY_HANDLERS


def test_domain_capabilities_in_card():
    from clawdbot.a2a_server import CLAWDBOT_CARD
    assert "set_custom_domain" in CLAWDBOT_CARD.capabilities
    assert "provision_domain" in CLAWDBOT_CARD.capabilities


# ── 2. Task routing ──

def test_task_routing_includes_domain_ops():
    from shared.task_routing import TASK_ROUTING
    assert TASK_ROUTING.get("set_custom_domain") == "clawdbot"
    assert TASK_ROUTING.get("provision_domain") == "clawdbot"


# ── 3. set_custom_domain calls Netlify API ──

def test_set_custom_domain_calls_netlify():
    """set_custom_domain must PUT to Netlify API with custom_domain."""
    from clawdbot.netlify_deploy import set_custom_domain

    with respx.mock:
        # Mock the PUT to update site with custom domain
        respx.put("https://api.netlify.com/api/v1/sites/site123").respond(200, json={
            "id": "site123",
            "name": "perseus-bobs-plumbing",
            "ssl_url": "https://bobsplumbing.com",
            "domain_aliases": ["bobsplumbing.com"],
        })
        # Mock SSL provisioning
        respx.post("https://api.netlify.com/api/v1/sites/site123/ssl").respond(200, json={})

        with patch("clawdbot.netlify_deploy.config") as mock_config:
            mock_config.hosting.netlify_token = "test-token"
            with patch("shared.db.execute", new_callable=AsyncMock):
                result = asyncio.run(set_custom_domain("site123", "bobsplumbing.com"))

    assert result["ok"] is True
    assert result["domain"] == "bobsplumbing.com"
    assert result["dns_target"] == "perseus-bobs-plumbing.netlify.app"


def test_set_custom_domain_persists_to_db():
    """set_custom_domain must UPDATE hosting_subscriptions with the domain."""
    from clawdbot.netlify_deploy import set_custom_domain

    execute_calls = []

    async def track_execute(query, params=None):
        execute_calls.append((query, params))

    with respx.mock:
        respx.put("https://api.netlify.com/api/v1/sites/site123").respond(200, json={
            "id": "site123", "name": "test", "ssl_url": "https://test.com",
        })
        respx.post("https://api.netlify.com/api/v1/sites/site123/ssl").respond(200, json={})

        with patch("clawdbot.netlify_deploy.config") as mock_config:
            mock_config.hosting.netlify_token = "test-token"
            with patch("shared.db.execute", track_execute):
                asyncio.run(set_custom_domain("site123", "test.com"))

    domain_updates = [q for q, p in execute_calls if "hosting_subscriptions" in q and "domain" in q]
    assert len(domain_updates) >= 1, f"Expected UPDATE hosting_subscriptions SET domain, got: {execute_calls}"


# ── 4. set_custom_domain handles Netlify failure ──

def test_set_custom_domain_handles_failure():
    from clawdbot.netlify_deploy import set_custom_domain

    with respx.mock:
        respx.put("https://api.netlify.com/api/v1/sites/bad-id").respond(404, text="Not found")

        with patch("clawdbot.netlify_deploy.config") as mock_config:
            mock_config.hosting.netlify_token = "test-token"
            result = asyncio.run(set_custom_domain("bad-id", "test.com"))

    assert result["ok"] is False
    assert "error" in result


# ── 5. domain_manager creates CNAME ──

def test_create_cname_record():
    """create_cname_record must POST to Cloudflare DNS API."""
    from tools.domain_manager import create_cname_record

    with respx.mock:
        # Zone lookup
        respx.get("https://api.cloudflare.com/client/v4/zones").respond(200, json={
            "result": [{"id": "zone123"}],
        })
        # Check existing records
        respx.get("https://api.cloudflare.com/client/v4/zones/zone123/dns_records").respond(200, json={
            "result": [],
        })
        # Create new record
        respx.post("https://api.cloudflare.com/client/v4/zones/zone123/dns_records").respond(200, json={
            "result": {"id": "rec123"},
        })

        with patch("tools.domain_manager.config") as mock_config:
            mock_config.cloudflare = MagicMock()
            mock_config.cloudflare.api_token = "test-cf-token"
            result = asyncio.run(create_cname_record("bobsplumbing.com", "perseus-bobs.netlify.app"))

    assert result["ok"] is True
    assert result["action"] == "created"


# ── 6. provision_custom_domain chains both ──

def test_provision_chains_netlify_and_cloudflare():
    """provision_custom_domain must call both Netlify and Cloudflare."""
    from tools.domain_manager import provision_custom_domain

    mock_set_domain = AsyncMock(return_value={
        "ok": True, "domain": "test.com", "ssl_url": "https://test.com",
        "ssl_provisioned": True, "dns_target": "perseus-test.netlify.app",
        "domain_aliases": [],
    })
    mock_create_cname = AsyncMock(return_value={"ok": True, "action": "created", "record_id": "rec1"})

    with patch("tools.domain_manager.provision_custom_domain.__module__", "tools.domain_manager"):
        with patch("clawdbot.netlify_deploy.set_custom_domain", mock_set_domain), \
             patch("tools.domain_manager.create_cname_record", mock_create_cname):
            result = asyncio.run(provision_custom_domain("test.com", "site123", "perseus-test.netlify.app"))

    assert result["ok"] is True
    assert result["dns"]["ok"] is True
    assert result["netlify"]["ok"] is True
