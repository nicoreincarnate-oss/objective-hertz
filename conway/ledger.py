"""
Economic Ledger — tracks all agent financial activity.

Every USDC transaction (send, receive, compute rental, service payment)
is recorded here. Integrates with BudgetGuard for spend tracking.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from shared.db import fetch_all, fetch_one, fetch_val

logger = logging.getLogger("conway.ledger")

# Valid transaction types
TX_TYPES = ("spend", "earn", "transfer", "fund", "compute_rental", "inference", "service")


class EconomicLedger:
    """Track all agent economic activity in the conway_ledger table."""

    async def record(
        self,
        agent: str,
        tx_type: str,
        amount: Decimal,
        currency: str = "USDC",
        counterparty: str = "",
        description: str = "",
        tx_hash: str = "",
    ) -> int | None:
        """Record a transaction. Returns ledger entry ID."""
        if tx_type not in TX_TYPES:
            logger.warning(f"Unknown tx_type '{tx_type}', recording anyway")

        try:
            row = await fetch_one(
                """INSERT INTO conway_ledger
                   (agent, tx_type, amount, currency, counterparty, description, tx_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (agent, tx_type, str(Decimal(str(amount))), currency, counterparty, description, tx_hash),
            )
            entry_id = row["id"] if row else None
            logger.info(
                f"Ledger: {agent} {tx_type} {amount} {currency} "
                f"→ {counterparty or 'N/A'} ({description[:50]})"
            )
            return entry_id
        except Exception as e:
            logger.error(f"Failed to record ledger entry: {e}")
            return None

    async def agent_balance_sheet(self, agent: str) -> dict[str, Any]:
        """Get income, expenses, and net balance for an agent."""
        income = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM conway_ledger
               WHERE agent = %s AND tx_type IN ('earn', 'fund')""",
            (agent,),
        ) or 0

        expenses = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM conway_ledger
               WHERE agent = %s AND tx_type IN ('spend', 'compute_rental', 'inference', 'service')""",
            (agent,),
        ) or 0

        transfers_out = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM conway_ledger
               WHERE agent = %s AND tx_type = 'transfer'""",
            (agent,),
        ) or 0

        income_dec = Decimal(str(income))
        expenses_dec = Decimal(str(expenses))
        transfers_dec = Decimal(str(transfers_out))
        net = income_dec - expenses_dec - transfers_dec

        return {
            "agent": agent,
            "total_income": float(income_dec),
            "total_expenses": float(expenses_dec),
            "total_transfers": float(transfers_dec),
            "net_balance": float(net),
        }

    async def system_economics(self) -> dict[str, Any]:
        """Get aggregate economic metrics across all agents."""
        total_volume = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM conway_ledger"
        ) or 0

        total_earned = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM conway_ledger WHERE tx_type IN ('earn', 'fund')"
        ) or 0

        total_spent = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM conway_ledger "
            "WHERE tx_type IN ('spend', 'compute_rental', 'inference', 'service')"
        ) or 0

        tx_count = await fetch_val(
            "SELECT COUNT(*) FROM conway_ledger"
        ) or 0

        by_agent = await fetch_all(
            """SELECT agent,
                      SUM(CASE WHEN tx_type IN ('earn', 'fund') THEN amount ELSE 0 END) as income,
                      SUM(CASE WHEN tx_type NOT IN ('earn', 'fund', 'transfer') THEN amount ELSE 0 END) as expenses
               FROM conway_ledger GROUP BY agent ORDER BY expenses DESC"""
        )

        by_type = await fetch_all(
            """SELECT tx_type, COUNT(*) as count, SUM(amount) as total
               FROM conway_ledger GROUP BY tx_type ORDER BY total DESC"""
        )

        return {
            "total_volume": float(Decimal(str(total_volume))),
            "total_earned": float(Decimal(str(total_earned))),
            "total_spent": float(Decimal(str(total_spent))),
            "net": float(Decimal(str(total_earned)) - Decimal(str(total_spent))),
            "transaction_count": tx_count,
            "by_agent": [dict(r) for r in by_agent] if by_agent else [],
            "by_type": [dict(r) for r in by_type] if by_type else [],
        }

    async def recent_transactions(
        self, agent: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get recent transactions, optionally filtered by agent."""
        if agent:
            rows = await fetch_all(
                """SELECT * FROM conway_ledger WHERE agent = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (agent, limit),
            )
        else:
            rows = await fetch_all(
                "SELECT * FROM conway_ledger ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        return [dict(r) for r in rows] if rows else []

    async def month_spending(self, agent: str) -> Decimal:
        """Get total spending for an agent this month."""
        month = date.today().replace(day=1)
        total = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM conway_ledger
               WHERE agent = %s
               AND tx_type IN ('spend', 'compute_rental', 'inference', 'service')
               AND created_at >= %s""",
            (agent, month),
        ) or 0
        return Decimal(str(total))
