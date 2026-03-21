"""Static checks for send recovery and follow-up parse visibility."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_seq_backed_sends_dedupe_against_outbound_log():
    code = (ROOT / "titan/compliance.py").read_text()
    assert "WHERE email_sequence_id = %s" in code
    assert "assuming already sent to avoid duplicates" in code
    assert "Perseus skipped resend to avoid a duplicate and marked it sent" in code


def test_follow_up_parse_failures_are_logged_and_alerted():
    code = (ROOT / "titan/pipeline/follow_up.py").read_text()
    assert 'logger.warning(f"Could not parse follow-up JSON for lead {lead[\'id\']}")' in code
    assert '"follow_up.compose_parse"' in code
