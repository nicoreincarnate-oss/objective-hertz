import importlib
import sys
import types


def test_get_audit_logger_uses_constructor_bus_subscription(monkeypatch):
    fake_events = types.ModuleType("openjarvis.core.events")
    fake_bus = object()
    fake_events.get_event_bus = lambda: fake_bus

    fake_audit = types.ModuleType("openjarvis.security.audit")

    class FakeAuditLogger:
        def __init__(self, db_path, bus=None):
            self.db_path = db_path
            self.bus = bus

    fake_audit.AuditLogger = FakeAuditLogger

    monkeypatch.setitem(sys.modules, "openjarvis.core.events", fake_events)
    monkeypatch.setitem(sys.modules, "openjarvis.security.audit", fake_audit)
    monkeypatch.delitem(sys.modules, "shared.oj_bridge", raising=False)

    bridge = importlib.import_module("shared.oj_bridge")
    bridge._bus = None
    bridge._audit_logger = None

    logger = bridge.get_audit_logger()

    assert isinstance(logger, FakeAuditLogger)
    assert logger.bus is fake_bus
