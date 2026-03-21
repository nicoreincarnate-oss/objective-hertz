"""Regression checks for Mem0 write failure visibility."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_memory_store_failures_emit_visible_alerts():
    code = (ROOT / "titan/memory.py").read_text()
    assert 'logger.warning(f"Mem0 store failed:' in code
    assert 'await emit_event("memory_write_failed"' in code
    assert 'await emit_event("urgent_alert"' in code
    assert "MEM0_ALERT_COOLDOWN_SECONDS" in code


def test_hermes_formats_memory_write_failures():
    code = (ROOT / "hermes/alerts.py").read_text()
    assert '"memory_write_failed": lambda p:' in code
