"""
Centralized email compliance gate.

Every outbound email — cold outreach, approved draft, proposal — MUST go through
send_to_instantly().  This is the single choke point that enforces:
  1. Unsubscribe check (pre-send)
  2. Physical-address footer injection (as Instantly template variable)
  3. HMAC-signed unsubscribe link (not enumerable)
  4. Immutable outbound log (outbound_email_log table)

soul/soul_copy.md lines 34-42 define the rules; this module enforces them.
"""

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass

try:
    from psycopg.types.json import Jsonb
except ImportError:
    Jsonb = None

from shared.db import emit_event, execute, fetch_one, get_config

logger = logging.getLogger("perseus.titan.compliance")
UNSUB_SIGNATURE_HEX_LENGTH = 32
ADDRESS_PLACEHOLDER = "[SET YOUR PHYSICAL ADDRESS]"
UNSUB_BASE_URL_PLACEHOLDER = "https://your-domain.com"


@dataclass(frozen=True)
class ComplianceConfig:
    address: str
    base_url: str
    secret: str


def _normalize_config_str(value: object) -> str:
    """Normalize DB/env config values for placeholder detection."""
    if value is None:
        return ""

    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def generate_unsub_link(client_id: int, secret: str, base_url: str) -> str:
    """Build an HMAC-signed unsubscribe URL safe from enumeration."""
    sig = hmac.new(
        secret.encode(), str(client_id).encode(), hashlib.sha256
    ).hexdigest()[:UNSUB_SIGNATURE_HEX_LENGTH]
    return f"{base_url.rstrip('/')}/unsub?id={client_id}&sig={sig}"


async def get_compliance_issues() -> list[str]:
    """Return any missing compliance prerequisites that block outbound email."""
    _, issues = await _load_compliance_config()
    return issues


async def _load_compliance_config() -> tuple[ComplianceConfig, list[str]]:
    """Load compliance config once so send paths don't reread the same values."""
    address = _normalize_config_str(await get_config("company_address", ""))
    base_url = _normalize_config_str(await get_config("unsubscribe_base_url", ""))
    secret = _normalize_config_str(os.getenv("UNSUBSCRIBE_SECRET", ""))

    issues: list[str] = []
    if not address or address == ADDRESS_PLACEHOLDER:
        issues.append("company_address is not configured in system_config")
    if not base_url or base_url == UNSUB_BASE_URL_PLACEHOLDER:
        issues.append("unsubscribe_base_url is not configured in system_config")
    _PLACEHOLDER_SECRETS = {
        "CHANGE-ME-GENERATE-A-REAL-SECRET",
        "CHANGE_ME",
        "changeme",
        "change_me",
        "secret",
        "xxx",
        "test",
        "placeholder",
    }
    if (
        not secret
        or secret in _PLACEHOLDER_SECRETS
        or secret.lower() in _PLACEHOLDER_SECRETS
        or len(secret) < 16
    ):
        issues.append("UNSUBSCRIBE_SECRET is not configured in the environment (must be 16+ chars, not a placeholder)")

    return ComplianceConfig(address=address, base_url=base_url, secret=secret), issues


async def assert_compliance_ready() -> None:
    """Fail fast when outbound-email compliance prerequisites are missing."""
    issues = await get_compliance_issues()
    if not issues:
        return

    message = "Outbound email compliance is misconfigured: " + "; ".join(issues)
    logger.critical(message)
    try:
        await emit_event(
            "urgent_alert",
            {
                "sender": "titan_compliance",
                "message": message,
            },
        )
    except Exception as exc:
        logger.error(f"Failed to emit compliance startup alert: {exc}")

    raise RuntimeError(message)


async def _build_compliance_footer(client_id: int, cfg: ComplianceConfig, issues: list[str]) -> str | None:
    """Return the compliance footer text, or None if config is missing."""
    if issues:
        for issue in issues:
            logger.error("%s — refusing to send", issue)
        return None

    unsub_link = generate_unsub_link(client_id, cfg.secret, cfg.base_url)
    return f"\n---\n{cfg.address}\nUnsubscribe: {unsub_link}"


async def send_to_instantly(
    campaign_id: str,
    client_id: int,
    email: str,
    subject: str,
    body: str,
    seq_id: int | None = None,
    message_type: str = "email",
    **extra_vars,
) -> bool:
    """
    The ONE function that sends email via Instantly.

    All outbound paths (email_send, review_mode, close_deal) must call this.
    Direct InstantlyClient.add_lead() calls from pipeline code are forbidden.
    """
    # 1. Unsubscribe gate
    client_row = await fetch_one(
        "SELECT status FROM clients WHERE id = %s", (client_id,)
    )
    if not client_row:
        logger.warning(f"Client {client_id} not found — skipping send")
        return False
    if client_row["status"] == "unsubscribed":
        logger.warning(f"Client {client_id} is unsubscribed — refusing to send")
        return False

    # 2. Build compliance footer
    compliance_cfg, compliance_issues = await _load_compliance_config()
    footer = await _build_compliance_footer(client_id, compliance_cfg, compliance_issues)
    if footer is None:
        return False

    checks = {
        "unsub_checked": True,
        "has_footer": True,
        "has_unsub_link": True,
        "message_type": message_type,
    }
    full_body = f"{body}{footer}"

    # 2.5 Dedupe seq-backed sends against the immutable outbound log.
    if seq_id is not None:
        existing_log = await fetch_one(
            """SELECT id, send_status FROM outbound_email_log
               WHERE email_sequence_id = %s
               ORDER BY created_at DESC LIMIT 1""",
            (seq_id,),
        )
        if existing_log:
            if existing_log["send_status"] == "sent":
                logger.info("Sequence %s already has a sent outbound log row; skipping resend", seq_id)
                return True
            if existing_log["send_status"] == "pending":
                logger.warning(
                    "Sequence %s has an unresolved pending outbound log row; assuming already sent to avoid duplicates",
                    seq_id,
                )
                await emit_event(
                    "urgent_alert",
                    {
                        "sender": "titan_compliance",
                        "message": (
                            f"Email sequence {seq_id} had a pending outbound log on retry; "
                            "Perseus skipped resend to avoid a duplicate and marked it sent."
                        ),
                    },
                )
                await execute(
                    """UPDATE outbound_email_log
                       SET send_status = 'sent',
                           sent_at = COALESCE(sent_at, NOW()),
                           delivery_error = COALESCE(
                               delivery_error,
                               'Recovered after restart; resend skipped to avoid duplicate'
                           )
                       WHERE id = %s""",
                    (existing_log["id"],),
                )
                return True
    elif message_type != "email":
        existing_log = await fetch_one(
            """SELECT id, send_status FROM outbound_email_log
               WHERE client_id = %s
                 AND campaign_id = %s
                 AND compliance_checks->>'message_type' = %s
               ORDER BY created_at DESC LIMIT 1""",
            (client_id, campaign_id, message_type),
        )
        if existing_log:
            if existing_log["send_status"] == "sent":
                logger.info(
                    "Client %s already has a sent %s row in campaign %s; skipping resend",
                    client_id,
                    message_type,
                    campaign_id,
                )
                return True
            if existing_log["send_status"] == "pending":
                logger.warning(
                    "Client %s has an unresolved pending %s row in campaign %s; assuming already sent to avoid duplicates",
                    client_id,
                    message_type,
                    campaign_id,
                )
                await emit_event(
                    "urgent_alert",
                    {
                        "sender": "titan_compliance",
                        "message": (
                            f"{message_type.title()} send for client {client_id} in campaign {campaign_id} "
                            "had a pending outbound log on retry; Perseus skipped resend to avoid a duplicate "
                            "and marked it sent."
                        ),
                    },
                )
                await execute(
                    """UPDATE outbound_email_log
                       SET send_status = 'sent',
                           sent_at = COALESCE(sent_at, NOW()),
                           delivery_error = COALESCE(
                               delivery_error,
                               'Recovered after restart; resend skipped to avoid duplicate'
                           )
                       WHERE id = %s""",
                    (existing_log["id"],),
                )
                return True

    # 3. Create the audit log row before send so we never lose the attempt.
    try:
        log_row = await fetch_one(
            """INSERT INTO outbound_email_log
               (client_id, email_sequence_id, recipient_email, subject, body,
                campaign_id, send_status, compliance_checks)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
               RETURNING id""",
            (
                client_id,
                seq_id,
                email,
                subject,
                full_body,
                campaign_id,
                Jsonb(checks),
            ),
        )
    except Exception as e:
        logger.error(f"Failed to create outbound_email_log before send for client {client_id}: {e}")
        return False

    if not log_row or "id" not in log_row:
        logger.error(f"Failed to create outbound_email_log before send for client {client_id}")
        return False
    log_id = log_row["id"]

    # 4. Send via Instantly — footer goes as a template variable,
    #    NOT inside personalization (which is truncated to 500 chars).
    #    The Instantly email template must include {{compliance_footer}}.
    try:
        from tools.instantly_client import InstantlyClient

        client = InstantlyClient()
        await client.add_lead(
            campaign_id=campaign_id,
            email=email,
            personalization=body[:500],
            custom_subject=subject,
            compliance_footer=footer,
            **extra_vars,
        )
        await client.close()
    except ImportError:
        logger.warning("Instantly client not available")
        await execute(
            """UPDATE outbound_email_log
               SET send_status = 'failed', delivery_error = %s
               WHERE id = %s""",
            ("Instantly client not available", log_id),
        )
        return False
    except Exception as e:
        logger.error(f"Instantly add_lead failed for client {client_id}: {e}")
        await execute(
            """UPDATE outbound_email_log
               SET send_status = 'failed', delivery_error = %s
               WHERE id = %s""",
            (str(e), log_id),
        )
        return False

    try:
        await execute(
            """UPDATE outbound_email_log
               SET send_status = 'sent', sent_at = NOW(), delivery_error = NULL
               WHERE id = %s""",
            (
                log_id,
            ),
        )
    except Exception as e:
        logger.error(f"Failed to finalize outbound_email_log for client {client_id}: {e}")

    return True
