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
echo "   hermes model"
echo "   # or use the current repo default:"
echo "   hermes config set model.provider openai-codex"
echo "   hermes config set model.default gpt-5.3-codex"
echo ""
echo "2. Connect Telegram:"
echo "   hermes gateway setup"
echo ""
echo "3. Load Perseus soul:"
echo "   ./scripts/sync-hermes-agent.sh"
echo ""
echo "4. Install Perseus skills:"
echo "   ./scripts/sync-hermes-agent.sh"
echo ""
echo "5. Start Hermes:"
echo "   hermes gateway start"
echo ""
