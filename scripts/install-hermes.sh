#!/bin/bash
# Install Nous Research Hermes Agent for Perseus
# This is the OFFICIAL installer — do not modify
set -e

echo "═══════════════════════════════════════"
echo "  Installing Hermes Agent (Nous Research)"
echo "═══════════════════════════════════════"

# Official one-line install
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

echo ""
echo "Verifying installation..."
hermes version 2>/dev/null || echo "WARNING: hermes command not found. Check your PATH."
hermes doctor 2>/dev/null || echo "WARNING: hermes doctor failed. Run manually."

echo ""
echo "═══════════════════════════════════════"
echo "  Hermes installed. Next steps:"
echo "═══════════════════════════════════════"
echo ""
echo "1. Configure Hermes for Perseus:"
echo "   hermes config set llm.provider anthropic"
echo "   hermes config set llm.model claude-sonnet-4-6"
echo "   hermes config set llm.api_key \$ANTHROPIC_API_KEY"
echo ""
echo "2. Connect Telegram:"
echo "   hermes channel add telegram --token \$TELEGRAM_BOT_TOKEN"
echo ""
echo "3. Load Perseus soul:"
echo "   cp soul/soul_hermes.md ~/.hermes/SOUL.md"
echo ""
echo "4. Install Perseus skills:"
echo "   hermes skill install ./hermes/skills/"
echo ""
echo "5. Start Hermes:"
echo "   hermes start"
echo ""
