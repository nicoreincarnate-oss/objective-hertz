#!/bin/bash
# Install OpenClaw + skills for Perseus
set -e

echo "═══════════════════════════════════════"
echo "  Installing OpenClaw for Perseus"
echo "═══════════════════════════════════════"

# Check if OpenClaw is already installed (macOS app)
if [ -d "/Applications/OpenClaw.app" ]; then
    echo "OpenClaw.app found in /Applications"
else
    echo "OpenClaw not found. Install from: https://openclaw.ai/"
    echo "Or use the DMG: ~/Downloads/OpenClaw-2026.2.21.dmg"
    echo ""
fi

# Clone curated skills
echo "Cloning curated skill collections..."

SKILLS_DIR="$HOME/.openclaw/skills"
mkdir -p "$SKILLS_DIR"

# VoltAgent's curated 5,400+ skills
if [ ! -d "$SKILLS_DIR/awesome-openclaw-skills" ]; then
    echo "  Cloning VoltAgent/awesome-openclaw-skills (5,400+ skills)..."
    git clone --depth 1 https://github.com/VoltAgent/awesome-openclaw-skills.git "$SKILLS_DIR/awesome-openclaw-skills" 2>/dev/null || echo "  Clone failed — check network"
else
    echo "  awesome-openclaw-skills already cloned"
fi

# LeoYeAI's curated 339+ best skills
if [ ! -d "$SKILLS_DIR/openclaw-master-skills" ]; then
    echo "  Cloning LeoYeAI/openclaw-master-skills (339+ best skills)..."
    git clone --depth 1 https://github.com/LeoYeAI/openclaw-master-skills.git "$SKILLS_DIR/openclaw-master-skills" 2>/dev/null || echo "  Clone failed — check network"
else
    echo "  openclaw-master-skills already cloned"
fi

echo ""
echo "═══════════════════════════════════════"
echo "  OpenClaw skills installed."
echo "═══════════════════════════════════════"
echo ""
echo "Skills directory: $SKILLS_DIR"
echo ""
echo "To connect OpenClaw to Perseus:"
echo "  1. Open OpenClaw app"
echo "  2. Configure LLM: Claude (Anthropic API key)"
echo "  3. Add Telegram channel"
echo "  4. Load Perseus skills from $SKILLS_DIR"
echo ""
