"""Static checks for Instantly campaign setup flow."""

from pathlib import Path


ROOT = Path("/Users/majovega/Desktop/objective-hertz")


def test_email_send_uses_single_instantly_client_for_campaign_setup():
    content = (ROOT / "titan/pipeline/email_send.py").read_text()
    assert "client2 = InstantlyClient()" not in content
    assert "await client.activate_campaign(new_id)" in content
