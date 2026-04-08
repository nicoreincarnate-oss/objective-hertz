"""Escalation log — feeds Phase 43 classifier training set.

Every time the local tier fails verification and we escalate to cloud, we log
the original prompt + the failed local response + the cloud response. After 30
days of stable Phase 42.5 production, this dataset trains the auto-classifier
to predict which prompts the local tier will struggle with.

CRITICAL SECURITY: the log contains real customer PII (Titan leads), operator
conversations (Hermes Telegram), Conway ledger context, and possibly env vars
that include API keys. The redactor scrubs all of this BEFORE write.
"""

from shared.escalation_log.redactor import (
    EscalationLogger,
    Redactor,
    log_escalation,
    REDACTION_PATTERNS,
)

__all__ = [
    "EscalationLogger",
    "Redactor",
    "log_escalation",
    "REDACTION_PATTERNS",
]
