#!/bin/bash
# Install Perseus LaunchAgents for 24/7 operation on macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$SCRIPT_DIR/launchagents"
TARGET_DIR="$HOME/Library/LaunchAgents"

echo "Installing Perseus LaunchAgents..."

for plist in com.perseus.master.plist com.perseus.titan.plist com.perseus.clawdbot.plist com.perseus.browser-use.plist com.perseus.dashboard.plist com.perseus.frontend.plist; do
    cp "$PLIST_DIR/$plist" "$TARGET_DIR/$plist"
    echo "  Installed $plist"
done

echo ""
echo "Syncing Hermes soul + local skills..."
bash "$(dirname "$SCRIPT_DIR")/scripts/sync-hermes-agent.sh"

echo ""
echo ""
echo "Building frontend (required before first LaunchAgent start)..."
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")/hermes/web/frontend"
if [ -d "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    pnpm install --frozen-lockfile 2>/dev/null || pnpm install
    pnpm build
    echo "  ✓ Frontend built"
    cd "$SCRIPT_DIR"
else
    echo "  ⚠ Frontend not found — com.perseus.frontend will fail until it's built"
fi

echo ""
echo "To start now:"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.master.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.titan.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.clawdbot.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.browser-use.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.dashboard.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.frontend.plist"
echo "  hermes gateway install"
echo "  hermes gateway start"
echo ""
echo "To stop:"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.master.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.titan.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.clawdbot.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.browser-use.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.dashboard.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.frontend.plist"
echo "  hermes gateway stop"
echo ""
echo "Perseus workers + dashboard will auto-start on login and restart if they crash."
echo "Official Hermes is managed by its own launchd service."
echo "Dashboard: http://localhost:3000"
