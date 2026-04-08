#!/usr/bin/env python3
"""show_spend — CLI for inspecting LLM spend.

Examples:
    python -m scripts.show_spend --today
    python -m scripts.show_spend --month
    python -m scripts.show_spend --by-daemon
    python -m scripts.show_spend --by-tier
    python -m scripts.show_spend --top-models 10
    python -m scripts.show_spend --escalations
    python -m scripts.show_spend --local-share
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


@dataclass
class SpendRow:
    label: str
    cost: float
    requests: int
    extra: dict[str, Any] | None = None


async def query_today(db_pool) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT tier, SUM(cost_usd) as cost, COUNT(*) as reqs
            FROM tier_spend_log
            WHERE DATE(timestamp) = CURRENT_DATE
            GROUP BY tier
            ORDER BY cost DESC
            """
        )).fetchall()
    return [SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2])) for r in rows]


async def query_month(db_pool) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT tier, SUM(cost_usd), COUNT(*)
            FROM tier_spend_log
            WHERE DATE_TRUNC('month', timestamp) = DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY tier
            ORDER BY 2 DESC
            """
        )).fetchall()
    return [SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2])) for r in rows]


async def query_by_daemon(db_pool, days: int = 30) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT daemon, SUM(cost_usd), COUNT(*),
                   SUM(CASE WHEN tier IN ('local','local-heavy') THEN 1 ELSE 0 END)::FLOAT/COUNT(*) as local_share
            FROM tier_spend_log
            WHERE timestamp > NOW() - INTERVAL '%s days'
            GROUP BY daemon
            ORDER BY 2 DESC
            """ % days
        )).fetchall()
    return [
        SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2]),
                 extra={"local_share": float(r[3])})
        for r in rows
    ]


async def query_by_tier(db_pool, days: int = 7) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT tier, SUM(cost_usd), COUNT(*),
                   AVG(latency_ms), PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FROM tier_spend_log
            WHERE timestamp > NOW() - INTERVAL '%s days'
            GROUP BY tier
            ORDER BY 2 DESC
            """ % days
        )).fetchall()
    return [
        SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2]),
                 extra={"avg_ms": int(r[3] or 0), "p95_ms": int(r[4] or 0)})
        for r in rows
    ]


async def query_top_models(db_pool, limit: int = 10) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT model_used, SUM(cost_usd), COUNT(*)
            FROM tier_spend_log
            WHERE timestamp > NOW() - INTERVAL '30 days'
            GROUP BY model_used
            ORDER BY 2 DESC
            LIMIT %s
            """,
            (limit,),
        )).fetchall()
    return [SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2])) for r in rows]


async def query_escalations(db_pool, days: int = 7) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT daemon || ': ' || escalated_from_tier || ' → ' || tier AS label,
                   SUM(cost_usd), COUNT(*), STRING_AGG(DISTINCT verifier_layer_failed, ',')
            FROM tier_spend_log
            WHERE timestamp > NOW() - INTERVAL '%s days' AND escalated_from_tier IS NOT NULL
            GROUP BY daemon, escalated_from_tier, tier
            ORDER BY 3 DESC
            LIMIT 25
            """ % days
        )).fetchall()
    return [
        SpendRow(label=r[0], cost=float(r[1]), requests=int(r[2]),
                 extra={"layers_failed": r[3] or ""})
        for r in rows
    ]


async def query_local_share(db_pool, days: int = 7) -> list[SpendRow]:
    async with db_pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT daemon,
                   SUM(CASE WHEN tier IN ('local','local-heavy') THEN 1 ELSE 0 END) AS local_calls,
                   COUNT(*) AS total_calls,
                   SUM(CASE WHEN tier IN ('local','local-heavy') THEN 1 ELSE 0 END)::FLOAT
                       / GREATEST(COUNT(*), 1) AS local_pct
            FROM tier_spend_log
            WHERE timestamp > NOW() - INTERVAL '%s days'
            GROUP BY daemon
            ORDER BY local_pct DESC
            """ % days
        )).fetchall()
    return [
        SpendRow(label=r[0], cost=0.0, requests=int(r[2]),
                 extra={"local_calls": int(r[1]), "local_share": float(r[3])})
        for r in rows
    ]


def render(rows: list[SpendRow], title: str, fields: list[str] | None = None) -> None:
    if not HAS_RICH:
        # Plain text fallback
        print(f"\n=== {title} ===\n")
        for r in rows:
            extra_str = ""
            if r.extra:
                extra_str = "  " + "  ".join(f"{k}={v}" for k, v in r.extra.items())
            print(f"{r.label:<40} ${r.cost:>10.2f}  {r.requests:>6} req{extra_str}")
        return

    console = Console()
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("Label")
    table.add_column("Cost USD", justify="right")
    table.add_column("Requests", justify="right")
    if fields:
        for f in fields:
            table.add_column(f, justify="right")

    for r in rows:
        cells = [r.label, f"${r.cost:.2f}", f"{r.requests:,}"]
        if fields and r.extra:
            for f in fields:
                v = r.extra.get(f, "")
                if isinstance(v, float):
                    cells.append(f"{v:.2%}" if "share" in f or "pct" in f else f"{v:.2f}")
                else:
                    cells.append(str(v))
        table.add_row(*cells)
    console.print(table)


async def main_async(args: argparse.Namespace) -> int:
    try:
        from shared.db import get_pool
    except Exception as exc:
        print(f"Cannot import shared.db: {exc}", file=sys.stderr)
        return 2

    pool = await get_pool()

    if args.today:
        render(await query_today(pool), "Today's Spend by Tier")
    elif args.month:
        render(await query_month(pool), "Month-to-Date Spend by Tier")
    elif args.by_daemon:
        render(await query_by_daemon(pool, args.days), f"Last {args.days}d by Daemon",
               fields=["local_share"])
    elif args.by_tier:
        render(await query_by_tier(pool, args.days), f"Last {args.days}d by Tier",
               fields=["avg_ms", "p95_ms"])
    elif args.top_models:
        render(await query_top_models(pool, args.top_models), f"Top {args.top_models} Models")
    elif args.escalations:
        render(await query_escalations(pool, args.days), f"Escalations Last {args.days}d",
               fields=["layers_failed"])
    elif args.local_share:
        render(await query_local_share(pool, args.days), f"Local Tier Share Last {args.days}d",
               fields=["local_calls", "local_share"])
    else:
        # Default: show today + month + local share
        render(await query_today(pool), "Today's Spend by Tier")
        render(await query_month(pool), "Month-to-Date Spend by Tier")
        render(await query_local_share(pool, 7), "Last 7d Local Share by Daemon",
               fields=["local_calls", "local_share"])

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Perseus LLM spend")
    parser.add_argument("--today", action="store_true", help="Today's spend by tier")
    parser.add_argument("--month", action="store_true", help="Month-to-date spend by tier")
    parser.add_argument("--by-daemon", action="store_true", help="Spend grouped by daemon")
    parser.add_argument("--by-tier", action="store_true", help="Spend grouped by tier with latency")
    parser.add_argument("--top-models", type=int, metavar="N", help="Top N models by spend")
    parser.add_argument("--escalations", action="store_true", help="Tier escalation patterns")
    parser.add_argument("--local-share", action="store_true", help="Local tier share by daemon")
    parser.add_argument("--days", type=int, default=7, help="Window in days (default 7)")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
