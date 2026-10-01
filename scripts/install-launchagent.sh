#!/bin/sh
# Install and start the server as a LaunchAgent, so it runs at login and restarts if it dies.
set -eu
label=com.music-mcp-server
dir=$(cd "$(dirname "$0")/.." && pwd)
uv=$(command -v uv)
plist="$HOME/Library/LaunchAgents/$label.plist"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array><string>$uv</string><string>run</string><string>python</string><string>server.py</string></array>
    <key>WorkingDirectory</key><string>$dir</string>
    <!-- uv is found on this PATH, not launchd's default one -->
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>$PATH</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/music-mcp-server.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/music-mcp-server.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "started $label; logs in ~/Library/Logs/music-mcp-server.log"
