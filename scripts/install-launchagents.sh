#!/bin/bash
# Install Perseus LaunchAgents for 24/7 operation on macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$SCRIPT_DIR/launchagents"
TARGET_DIR="$HOME/Library/LaunchAgents"

echo "Installing Perseus LaunchAgents..."

# Core daemons (always installed)
CORE_PLISTS=(
    com.perseus.master.plist
    com.perseus.titan.plist
    com.perseus.clawdbot.plist
    com.perseus.hermes.plist
    com.perseus.conway.plist
    com.perseus.deerflow.plist
    com.perseus.ruflo.plist
    com.perseus.openjarvis.plist
    com.perseus.dashboard.plist
    com.perseus.frontend.plist
    com.perseus.browser-use.plist
    com.perseus.backup.plist
    com.perseus.clawdbot-health.plist
)

# Local-tier plists (installed but kept unloaded until Phase 42.5 cutover)
LOCAL_TIER_PLISTS=(
    com.perseus.parakeet.plist
    com.perseus.kokoro.plist
    com.perseus.mem0.plist
    com.perseus.n8n.plist
)

mkdir -p "$TARGET_DIR"

for plist in "${CORE_PLISTS[@]}"; do
    if [ -f "$PLIST_DIR/$plist" ]; then
        cp "$PLIST_DIR/$plist" "$TARGET_DIR/$plist"
        echo "  Installed $plist"
    else
        echo "  ⚠ Skipping $plist (not found in $PLIST_DIR)"
    fi
done

for plist in "${LOCAL_TIER_PLISTS[@]}"; do
    if [ -f "$PLIST_DIR/$plist" ]; then
        cp "$PLIST_DIR/$plist" "$TARGET_DIR/$plist"
        echo "  Installed $plist (local-tier — load manually after Phase 42.5 cutover)"
    fi
done

echo ""
echo "Building frontend (required before first LaunchAgent start)..."
FRONTEND_DIR="/opt/perseus/repo/hermes/web/frontend"
if [ -d "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    pnpm install --frozen-lockfile 2>/dev/null || pnpm install
    pnpm build
    echo "  ✓ Frontend built"
    cd "$SCRIPT_DIR"
else
    echo "  ⚠ Frontend not found at $FRONTEND_DIR — com.perseus.frontend will fail until it's built"
fi

echo ""
echo "To start core daemons now:"
for plist in "${CORE_PLISTS[@]}"; do
    echo "  launchctl load ~/Library/LaunchAgents/$plist"
done
echo ""
echo "To start local-tier daemons (Phase 42.5 cutover only):"
for plist in "${LOCAL_TIER_PLISTS[@]}"; do
    echo "  launchctl load ~/Library/LaunchAgents/$plist"
done
echo ""
echo "To stop all:"
for plist in "${CORE_PLISTS[@]}" "${LOCAL_TIER_PLISTS[@]}"; do
    echo "  launchctl unload ~/Library/LaunchAgents/$plist"
done
echo ""
echo "Perseus workers + dashboard will auto-start on login and restart if they crash."
echo "Dashboard: http://localhost:8500"
