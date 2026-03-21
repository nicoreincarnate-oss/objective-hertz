#!/bin/bash
# Install all skill collections that Perseus agents use.
# Skills are FREE markdown instruction files — the LLM executes them.
# Cost is only the LLM (Claude Max $200/mo) + any external APIs the skills call.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SKILLS_BASE="$ROOT_DIR/.agent/skills"

echo "═══════════════════════════════════════"
echo "  PERSEUS — Installing Skill Collections"
echo "═══════════════════════════════════════"
echo ""

mkdir -p "$SKILLS_BASE"

# ── 1. Antigravity Awesome Skills (1,273+ Claude Code / Cursor skills) ──
echo "[1/4] Antigravity Awesome Skills (1,273+ skills)..."
if [ ! -d "$SKILLS_BASE/antigravity" ]; then
    git clone --depth 1 https://github.com/sickn33/antigravity-awesome-skills.git "$SKILLS_BASE/antigravity" 2>/dev/null \
        && echo "  ✓ Cloned" || echo "  ✗ Failed (check network)"
else
    echo "  Already installed. Updating..."
    cd "$SKILLS_BASE/antigravity" && git pull --ff-only 2>/dev/null && cd "$ROOT_DIR"
fi

# ── 2. VoltAgent Awesome Agent Skills (500+ multi-platform skills) ──
echo "[2/4] VoltAgent Agent Skills (500+ skills)..."
if [ ! -d "$SKILLS_BASE/voltagent" ]; then
    git clone --depth 1 https://github.com/VoltAgent/awesome-agent-skills.git "$SKILLS_BASE/voltagent" 2>/dev/null \
        && echo "  ✓ Cloned" || echo "  ✗ Failed"
else
    echo "  Already installed. Updating..."
    cd "$SKILLS_BASE/voltagent" && git pull --ff-only 2>/dev/null && cd "$ROOT_DIR"
fi

# ── 3. OpenClaw Curated Skills (5,400+ filtered from ClawHub) ──
echo "[3/4] OpenClaw Skills (5,400+ skills)..."
OPENCLAW_DIR="$HOME/.openclaw/skills"
mkdir -p "$OPENCLAW_DIR"
if [ ! -d "$OPENCLAW_DIR/awesome-openclaw-skills" ]; then
    git clone --depth 1 https://github.com/VoltAgent/awesome-openclaw-skills.git "$OPENCLAW_DIR/awesome-openclaw-skills" 2>/dev/null \
        && echo "  ✓ Cloned" || echo "  ✗ Failed"
else
    echo "  Already installed. Updating..."
    cd "$OPENCLAW_DIR/awesome-openclaw-skills" && git pull --ff-only 2>/dev/null && cd "$ROOT_DIR"
fi

# ── 4. OpenClaw Master Skills (339+ curated best skills) ──
echo "[4/4] OpenClaw Master Skills (339+ curated)..."
if [ ! -d "$OPENCLAW_DIR/openclaw-master-skills" ]; then
    git clone --depth 1 https://github.com/LeoYeAI/openclaw-master-skills.git "$OPENCLAW_DIR/openclaw-master-skills" 2>/dev/null \
        && echo "  ✓ Cloned" || echo "  ✗ Failed"
else
    echo "  Already installed. Updating..."
    cd "$OPENCLAW_DIR/openclaw-master-skills" && git pull --ff-only 2>/dev/null && cd "$ROOT_DIR"
fi

echo ""
echo "═══════════════════════════════════════"
echo "  Skills Installation Complete"
echo "═══════════════════════════════════════"
echo ""

# Count installed skills
TOTAL=0
for dir in "$SKILLS_BASE"/*/; do
    if [ -d "$dir" ]; then
        COUNT=$(find "$dir" -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
        TOTAL=$((TOTAL + COUNT))
        echo "  $(basename $dir): $COUNT skills"
    fi
done
for dir in "$OPENCLAW_DIR"/*/; do
    if [ -d "$dir" ]; then
        COUNT=$(find "$dir" -name "SKILL.md" -o -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
        TOTAL=$((TOTAL + COUNT))
        echo "  $(basename $dir): $COUNT skills"
    fi
done

echo ""
echo "  Total: ~$TOTAL skill files available"
echo ""
echo "  Perseus custom skills: $ROOT_DIR/hermes/skills/"
echo "  Agent skills:          $SKILLS_BASE/"
echo "  OpenClaw skills:       $OPENCLAW_DIR/"
echo ""
echo "  Titan pipeline auto-detects installed skills."
echo "  No configuration needed — just install and they work."
echo "═══════════════════════════════════════"
