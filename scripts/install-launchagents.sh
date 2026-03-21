#!/bin/bash
# Install Perseus LaunchAgents for 24/7 operation on macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DIR="$SCRIPT_DIR/launchagents"
TARGET_DIR="$HOME/Library/LaunchAgents"

echo "Installing Perseus LaunchAgents..."

for plist in com.perseus.master.plist com.perseus.titan.plist com.perseus.hermes.plist com.perseus.clawdbot.plist; do
    cp "$PLIST_DIR/$plist" "$TARGET_DIR/$plist"
    echo "  Installed $plist"
done

echo ""
echo "To start now:"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.master.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.titan.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.hermes.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.perseus.clawdbot.plist"
echo ""
echo "To stop:"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.master.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.titan.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.hermes.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.perseus.clawdbot.plist"
echo ""
echo "All 4 daemons will auto-start on login and restart if they crash."
