from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_critical_tier_enables_revenue_mode_and_seeds_tasks(monkeypatch, patch_db):
    import conway.survival as survival
    import shared.comms
    import shared.db

    monkeypatch.setattr(shared.comms, "send_alert", AsyncMock())
    monkeypatch.setattr(survival, "set_config", shared.db.set_config)
    monkeypatch.setattr(survival, "insert_task", shared.db.insert_task)

    monitor = survival.SurvivalMonitor(survival.WalletManager(), survival.EconomicLedger())

    await monitor._apply_tier_change("titan", "low_compute", "critical")

    assert patch_db.tables["system_config"]["titan_paused"] is False
    assert patch_db.tables["system_config"]["conway_revenue_mode_titan"] is True

    task_types = [task["task_type"] for task in patch_db.tables["task_queue"]]
    assert "close_interested" in task_types
    assert "follow_up_check" in task_types
    assert "process_invoices" in task_types
    assert "lead_discovery" in task_types


@pytest.mark.asyncio
async def test_dead_tier_disables_revenue_mode_and_pauses_agent(monkeypatch, patch_db):
    import conway.survival as survival
    import shared.db

    monkeypatch.setattr(survival, "set_config", shared.db.set_config)
    monkeypatch.setattr(survival, "insert_task", shared.db.insert_task)

    monitor = survival.SurvivalMonitor(survival.WalletManager(), survival.EconomicLedger())

    await monitor._apply_tier_change("titan", "critical", "dead")

    assert patch_db.tables["system_config"]["titan_paused"] is True
    assert patch_db.tables["system_config"]["conway_revenue_mode_titan"] is False
