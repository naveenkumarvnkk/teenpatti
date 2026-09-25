#!/usr/bin/env bash
# Install the daily open/close schedule (07:58 open, 20:05 close, and a check at login).
# Re-run after editing deploy/hours.sh or changing the model in seats.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="$HOME/Library/Application Support/teenpatti"
MODEL=$(sed -n 's/^  model: *\([^ #]*\).*/\1/p' seats.yaml | head -1)
mkdir -p "$DEST"
sed "s|__MODEL__|$MODEL|" deploy/hours.sh > "$DEST/hours.sh" && chmod +x "$DEST/hours.sh"
PLIST=~/Library/LaunchAgents/com.naveen.teenpatti-hours.plist
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.naveen.teenpatti-hours</string>
  <key>ProgramArguments</key><array><string>$DEST/hours.sh</string><string>auto</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartCalendarInterval</key><array>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>58</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>5</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$DEST/hours.log</string>
  <key>StandardErrorPath</key><string>$DEST/hours.log</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u)/com.naveen.teenpatti-hours 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Schedule installed (model: $MODEL). Log: $DEST/hours.log"
