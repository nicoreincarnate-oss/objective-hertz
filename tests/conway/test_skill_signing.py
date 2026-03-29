"""
Tests for Ed25519 skill signing and verification (FIX-04).

Validates that:
- Signing creates valid .sig files
- Verification accepts valid signatures
- Tampered content is rejected
- Missing signatures are rejected
- load_skill blocks unsigned skills
- load_skill allows signed skills
- SKILL_LOADER_ALLOW_UNSIGNED bypass works
"""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def skill_keys(tmp_path):
    """Generate a fresh Ed25519 keypair for test isolation."""
    key = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "test-private.pem"
    pub_path = tmp_path / "test-public.pem"
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return {"private": priv_path, "public": pub_path, "key": key}


def _write_skill(tmp_path, content="# Test Skill\nDo the thing"):
    """Helper to write a skill file to tmp_path."""
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(content)
    return skill_path


class TestSignSkill:
    """Tests for the sign_skill function."""

    def test_sign_skill_creates_sig_file(self, tmp_path, skill_keys):
        """Signing a skill creates a .sig file with 64 bytes (Ed25519)."""
        from shared.skill_signer import sign_skill

        skill_path = _write_skill(tmp_path)
        sig_path = sign_skill(skill_path, skill_keys["private"])

        assert sig_path.exists()
        assert sig_path == Path(str(skill_path) + ".sig")
        # Ed25519 signatures are always 64 bytes
        assert len(sig_path.read_bytes()) == 64


class TestVerifySkill:
    """Tests for the verify_skill function."""

    def test_verify_skill_accepts_valid_signature(self, tmp_path, skill_keys, monkeypatch):
        """A properly signed skill passes verification."""
        import shared.skill_loader
        from shared.skill_signer import sign_skill

        skill_path = _write_skill(tmp_path)
        sign_skill(skill_path, skill_keys["private"])

        monkeypatch.setattr(shared.skill_loader, "VERIFY_KEY_PATH", skill_keys["public"])
        assert shared.skill_loader.verify_skill(skill_path) is True

    def test_verify_skill_rejects_tampered_content(self, tmp_path, skill_keys, monkeypatch):
        """Modifying skill content after signing causes verification to fail."""
        import shared.skill_loader
        from shared.skill_signer import sign_skill

        skill_path = _write_skill(tmp_path)
        sign_skill(skill_path, skill_keys["private"])

        # Tamper with the content after signing
        skill_path.write_text(skill_path.read_text() + "TAMPERED")

        monkeypatch.setattr(shared.skill_loader, "VERIFY_KEY_PATH", skill_keys["public"])
        assert shared.skill_loader.verify_skill(skill_path) is False

    def test_verify_skill_rejects_missing_sig(self, tmp_path, skill_keys, monkeypatch):
        """A skill with no .sig file fails verification."""
        import shared.skill_loader

        skill_path = _write_skill(tmp_path)
        # Do NOT sign it

        monkeypatch.setattr(shared.skill_loader, "VERIFY_KEY_PATH", skill_keys["public"])
        assert shared.skill_loader.verify_skill(skill_path) is False


class TestLoadSkill:
    """Tests for the load_skill function with signature verification."""

    def test_load_skill_rejects_unsigned(self, tmp_path, skill_keys, monkeypatch):
        """load_skill returns empty string for unsigned skills."""
        import shared.skill_loader

        skill_path = _write_skill(tmp_path, "# Secret Skill")
        # Do NOT sign it
        # Ensure SKILL_LOADER_ALLOW_UNSIGNED is NOT set
        monkeypatch.delenv("SKILL_LOADER_ALLOW_UNSIGNED", raising=False)
        monkeypatch.setattr(shared.skill_loader, "VERIFY_KEY_PATH", skill_keys["public"])

        result = shared.skill_loader.load_skill(skill_path)
        assert result == ""

    def test_load_skill_accepts_signed(self, tmp_path, skill_keys, monkeypatch):
        """load_skill returns full content for properly signed skills."""
        import shared.skill_loader
        from shared.skill_signer import sign_skill

        content = "# Test Skill\nDo the thing"
        skill_path = _write_skill(tmp_path, content)
        sign_skill(skill_path, skill_keys["private"])

        monkeypatch.delenv("SKILL_LOADER_ALLOW_UNSIGNED", raising=False)
        monkeypatch.setattr(shared.skill_loader, "VERIFY_KEY_PATH", skill_keys["public"])

        result = shared.skill_loader.load_skill(skill_path)
        assert result == content

    def test_load_skill_unsigned_bypass(self, tmp_path, monkeypatch):
        """SKILL_LOADER_ALLOW_UNSIGNED=true bypasses verification."""
        import shared.skill_loader

        content = "# Unsigned"
        skill_path = _write_skill(tmp_path, content)

        monkeypatch.setenv("SKILL_LOADER_ALLOW_UNSIGNED", "true")

        result = shared.skill_loader.load_skill(skill_path)
        assert result == content

    def test_load_skill_unsigned_bypass_case_insensitive(self, tmp_path, monkeypatch):
        """SKILL_LOADER_ALLOW_UNSIGNED=True (capitalized) also bypasses."""
        import shared.skill_loader

        content = "# Unsigned Capital"
        skill_path = _write_skill(tmp_path, content)

        monkeypatch.setenv("SKILL_LOADER_ALLOW_UNSIGNED", "True")

        result = shared.skill_loader.load_skill(skill_path)
        assert result == content
