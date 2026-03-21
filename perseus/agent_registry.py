"""
Agent registry — all agents register here so Perseus knows what's running.
"""

import logging
from typing import Optional

from shared.db import fetch_all, fetch_one, execute

logger = logging.getLogger("perseus.registry")


async def get_active_agents() -> list[dict]:
    """Get all active agents."""
    return await fetch_all(
        "SELECT * FROM agent_registry WHERE status = 'active' ORDER BY name"
    )


async def get_agent(name: str) -> Optional[dict]:
    """Get a specific agent's status."""
    return await fetch_one(
        "SELECT * FROM agent_registry WHERE name = %s", (name,)
    )


async def heartbeat(name: str):
    """Update an agent's heartbeat timestamp."""
    await execute(
        "UPDATE agent_registry SET last_heartbeat = NOW() WHERE name = %s",
        (name,),
    )


async def check_agent_health() -> dict:
    """Check which agents are healthy (heartbeat within last 5 minutes)."""
    agents = await fetch_all(
        """SELECT name, status, last_heartbeat,
                  EXTRACT(EPOCH FROM NOW() - last_heartbeat) as seconds_since_heartbeat
           FROM agent_registry"""
    )
    health = {}
    for agent in agents:
        stale = (agent.get("seconds_since_heartbeat") or 999) > 300
        health[agent["name"]] = {
            "status": "stale" if stale else agent["status"],
            "last_heartbeat": str(agent.get("last_heartbeat", "")),
        }
    return health
