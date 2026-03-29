#!/usr/bin/env bash
# Generate Ed25519 keypair for skill signing
# Private key: ~/.objective-hertz/skill-signing-key.pem (operator keeps, never commit)
# Public key: shared/skill_verify_key.pem (checked into repo)
set -euo pipefail
PRIVATE_KEY_DIR="$HOME/.objective-hertz"
PRIVATE_KEY_PATH="$PRIVATE_KEY_DIR/skill-signing-key.pem"
PUBLIC_KEY_PATH="$(dirname "$0")/../shared/skill_verify_key.pem"
mkdir -p "$PRIVATE_KEY_DIR"
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
priv_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
pub_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
open('$PRIVATE_KEY_PATH', 'wb').write(priv_pem)
open('$PUBLIC_KEY_PATH', 'wb').write(pub_pem)
print(f'Private key: $PRIVATE_KEY_PATH')
print(f'Public key: $PUBLIC_KEY_PATH')
"
chmod 600 "$PRIVATE_KEY_PATH"
echo "Done. Private key secured at $PRIVATE_KEY_PATH"
