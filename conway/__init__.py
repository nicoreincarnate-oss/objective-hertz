"""
Conway Terminal — Economic nervous system for Perseus agents.

Integrates with Conway Cloud (https://conway.tech) to give agents:
- Ethereum wallets on Base L2
- x402 payments (USDC) for compute, inference, and services
- ERC-8004 on-chain identity registration
- Survival tier management based on credit balance
"""

from conway.ledger import EconomicLedger
from conway.survival import SurvivalMonitor
from conway.wallet import AgentWallet, WalletManager

__all__ = [
    "AgentWallet",
    "WalletManager",
    "EconomicLedger",
    "SurvivalMonitor",
]
