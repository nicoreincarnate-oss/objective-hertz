"""Static checks for non-sequence outbound dedupe recovery."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_non_seq_sends_dedupe_by_client_campaign_and_message_type():
    code = (ROOT / "titan/compliance.py").read_text()
    assert "elif message_type != \"email\":" in code
    assert "WHERE client_id = %s" in code
    assert "AND campaign_id = %s" in code
    assert "compliance_checks->>'message_type' = %s" in code
    assert "skipped resend to avoid a duplicate" in code
