"""
Ed25519 skill signing utility.

Signs skill files (SKILL.md) so the skill loader can verify integrity
before injecting content into LLM system prompts.

Usage:
    python3 -m shared.skill_signer sign <skill_path> --key <private_key_path>
    python3 -m shared.skill_signer sign-all --key <private_key_path>
"""

import argparse
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger("perseus.skill_signer")


def _load_private_key(private_key_path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file."""
    pem_bytes = private_key_path.read_bytes()
    key = serialization.load_pem_private_key(pem_bytes, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        msg = f"Expected Ed25519 private key, got {type(key).__name__}"
        raise TypeError(msg)
    return key


def sign_skill(skill_path: Path, private_key_path: Path) -> Path:
    """
    Sign a skill file with an Ed25519 private key.

    Reads the skill content, computes a signature, and writes it to
    {skill_path}.sig as raw bytes (64 bytes for Ed25519).

    Returns the path to the .sig file.
    """
    private_key = _load_private_key(private_key_path)
    content_bytes = skill_path.read_bytes()
    signature = private_key.sign(content_bytes)
    sig_path = Path(str(skill_path) + ".sig")
    sig_path.write_bytes(signature)
    logger.info("Signed %s -> %s", skill_path, sig_path)
    return sig_path


def sign_all_skills(private_key_path: Path) -> list[Path]:
    """
    Sign all SKILL.md files found in the standard skill directories.

    Returns a list of .sig file paths created.
    """
    # Import here to avoid circular dependency at module level
    from shared.skill_loader import SKILL_DIRS

    signed: list[Path] = []
    for skill_dir in SKILL_DIRS:
        if not skill_dir.exists():
            continue
        for skill_file in skill_dir.rglob("SKILL.md"):
            sig_path = sign_skill(skill_file, private_key_path)
            signed.append(sig_path)
            logger.info("Signed: %s", skill_file)
    return signed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Sign skill files with Ed25519")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # sign <skill_path> --key <private_key_path>
    sign_parser = subparsers.add_parser("sign", help="Sign a single skill file")
    sign_parser.add_argument("skill_path", type=Path, help="Path to SKILL.md")
    sign_parser.add_argument("--key", type=Path, required=True, help="Path to private key PEM")

    # sign-all --key <private_key_path>
    sign_all_parser = subparsers.add_parser("sign-all", help="Sign all skills in standard directories")
    sign_all_parser.add_argument("--key", type=Path, required=True, help="Path to private key PEM")

    args = parser.parse_args()

    if args.command == "sign":
        result = sign_skill(args.skill_path, args.key)
        print(f"Signature written to: {result}")
    elif args.command == "sign-all":
        results = sign_all_skills(args.key)
        print(f"Signed {len(results)} skill(s)")
        for sig in results:
            print(f"  {sig}")
