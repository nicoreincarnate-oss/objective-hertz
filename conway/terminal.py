"""
Conway Terminal — Textual-based TUI for the economic nervous system.

Displays agent wallet balances, transaction history, compute instances,
budget gauges, and live event streams. Provides operator command input.

Launch via: jarvis conway
"""

import asyncio
import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger("conway.terminal")

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import (
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        Log,
        Static,
    )
    from textual.timer import Timer

    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False


def launch_terminal():
    """Entry point for `jarvis conway` CLI command."""
    if not HAS_TEXTUAL:
        print("Conway Terminal requires the 'textual' package.")
        print("Install with: pip install textual")
        return

    app = ConwayTerminalApp()
    app.run()


if HAS_TEXTUAL:

    class WalletPanel(Static):
        """Displays agent wallet balances."""

        def compose(self) -> ComposeResult:
            yield Label("AGENT WALLETS", classes="panel-title")
            yield DataTable(id="wallet-table")

        def on_mount(self) -> None:
            table = self.query_one("#wallet-table", DataTable)
            table.add_columns("Agent", "Address", "USDC Balance", "Tier")

        async def refresh_data(self) -> None:
            try:
                from shared.db import fetch_all

                rows = await fetch_all(
                    "SELECT agent_name, public_address FROM conway_wallets ORDER BY agent_name"
                )
                table = self.query_one("#wallet-table", DataTable)
                table.clear()

                if rows:
                    from conway.wallet import WalletManager

                    wm = WalletManager()
                    for row in rows:
                        name = row["agent_name"]
                        addr = row["public_address"]
                        try:
                            wallet = await wm.get_or_create_wallet(name)
                            balance = await wallet.get_balance()
                        except Exception:
                            balance = Decimal("0")

                        # Get survival tier
                        from shared.db import fetch_one

                        tier_row = await fetch_one(
                            "SELECT value FROM system_config WHERE key = %s",
                            (f"conway_tier_{name}",),
                        )
                        tier = tier_row["value"] if tier_row else "normal"

                        table.add_row(
                            name,
                            f"{addr[:6]}...{addr[-4:]}",
                            f"${balance:.2f}",
                            tier,
                        )
            except Exception as e:
                logger.debug(f"Wallet panel refresh: {e}")

    class LedgerPanel(Static):
        """Displays recent transactions."""

        def compose(self) -> ComposeResult:
            yield Label("TRANSACTION LEDGER", classes="panel-title")
            yield DataTable(id="ledger-table")

        def on_mount(self) -> None:
            table = self.query_one("#ledger-table", DataTable)
            table.add_columns("Time", "Agent", "Type", "Amount", "To/From", "Description")

        async def refresh_data(self) -> None:
            try:
                from conway.ledger import EconomicLedger

                ledger = EconomicLedger()
                txs = await ledger.recent_transactions(limit=15)
                table = self.query_one("#ledger-table", DataTable)
                table.clear()
                for tx in txs:
                    created = str(tx.get("created_at", ""))[:19]
                    table.add_row(
                        created,
                        tx.get("agent", ""),
                        tx.get("tx_type", ""),
                        f"${tx.get('amount', 0):.4f}",
                        tx.get("counterparty", "")[:20],
                        tx.get("description", "")[:30],
                    )
            except Exception as e:
                logger.debug(f"Ledger panel refresh: {e}")

    class ComputePanel(Static):
        """Displays active compute instances."""

        def compose(self) -> ComposeResult:
            yield Label("COMPUTE INSTANCES", classes="panel-title")
            yield DataTable(id="compute-table")

        def on_mount(self) -> None:
            table = self.query_one("#compute-table", DataTable)
            table.add_columns("Agent", "Provider", "GPU", "$/hr", "Status", "Started")

        async def refresh_data(self) -> None:
            try:
                from shared.db import fetch_all

                rows = await fetch_all(
                    """SELECT agent, provider, gpu_type, price_per_hour, status, started_at
                       FROM conway_compute_instances
                       WHERE status = 'active'
                       ORDER BY started_at DESC LIMIT 10"""
                )
                table = self.query_one("#compute-table", DataTable)
                table.clear()
                for row in rows or []:
                    table.add_row(
                        row.get("agent", ""),
                        row.get("provider", ""),
                        row.get("gpu_type", ""),
                        f"${row.get('price_per_hour', 0):.2f}",
                        row.get("status", ""),
                        str(row.get("started_at", ""))[:19],
                    )
            except Exception as e:
                logger.debug(f"Compute panel refresh: {e}")

    class BudgetGauge(Static):
        """Displays budget status."""

        def compose(self) -> ComposeResult:
            yield Label("BUDGET", classes="panel-title")
            yield Static(id="budget-display")

        async def refresh_data(self) -> None:
            try:
                from tools.budget_guard import get_month_spending

                spending = await get_month_spending()
                total = spending["total_spent"]
                remaining = spending["remaining"]
                pct = spending["percent_used"]
                fiat = spending.get("fiat_spent", total)
                crypto = spending.get("crypto_spent", 0)

                # Build gauge bar
                bar_width = 30
                filled = int(bar_width * pct / 100)
                bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"

                status = "EXCEEDED" if spending["exceeded"] else "OK"
                color = "red" if pct > 90 else "yellow" if pct > 70 else "green"

                display = self.query_one("#budget-display", Static)
                display.update(
                    f"  {bar} {pct:.0f}%\n"
                    f"  Spent: ${total:.2f}  |  Remaining: ${remaining:.2f}  |  {status}\n"
                    f"  Fiat: ${fiat:.2f}  |  Crypto: ${crypto:.2f}"
                )
            except Exception as e:
                logger.debug(f"Budget gauge refresh: {e}")

    class EventStream(Static):
        """Live event stream."""

        def compose(self) -> ComposeResult:
            yield Label("LIVE EVENTS", classes="panel-title")
            yield Log(id="event-log", max_lines=50)

        async def refresh_data(self) -> None:
            try:
                from shared.db import fetch_all

                rows = await fetch_all(
                    """SELECT event_type, payload, created_at FROM events
                       ORDER BY created_at DESC LIMIT 5"""
                )
                log = self.query_one("#event-log", Log)
                for row in reversed(rows or []):
                    ts = str(row.get("created_at", ""))[:19]
                    evt = row.get("event_type", "")
                    payload = str(row.get("payload", ""))[:80]
                    log.write_line(f"[{ts}] {evt}: {payload}")
            except Exception as e:
                logger.debug(f"Event stream refresh: {e}")

    class ConwayTerminalApp(App):
        """Conway Terminal — Economic nervous system dashboard."""

        CSS = """
        Screen {
            layout: grid;
            grid-size: 2 3;
            grid-gutter: 1;
        }
        .panel-title {
            text-style: bold;
            color: cyan;
        }
        #wallet-panel { row-span: 1; }
        #budget-panel { row-span: 1; }
        #ledger-panel { column-span: 2; }
        #compute-panel { row-span: 1; }
        #event-panel { row-span: 1; }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("r", "refresh", "Refresh"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield WalletPanel(id="wallet-panel")
            yield BudgetGauge(id="budget-panel")
            yield LedgerPanel(id="ledger-panel")
            yield ComputePanel(id="compute-panel")
            yield EventStream(id="event-panel")
            yield Footer()

        def on_mount(self) -> None:
            self.title = "Conway Terminal"
            self.sub_title = "Economic Nervous System"
            # Auto-refresh every 10 seconds
            self.set_interval(10, self.action_refresh)
            # Initial refresh
            self.call_later(self.action_refresh)

        async def action_refresh(self) -> None:
            """Refresh all panels."""
            panels = [
                self.query_one("#wallet-panel", WalletPanel),
                self.query_one("#budget-panel", BudgetGauge),
                self.query_one("#ledger-panel", LedgerPanel),
                self.query_one("#compute-panel", ComputePanel),
                self.query_one("#event-panel", EventStream),
            ]
            for panel in panels:
                try:
                    await panel.refresh_data()
                except Exception:
                    pass
