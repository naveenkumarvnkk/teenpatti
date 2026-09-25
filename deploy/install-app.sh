#!/usr/bin/env bash
# Install/restart the app as a launchd service (listens on 127.0.0.1:8765 only).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -q -r requirements.txt; }
# launchd can't read files in ~/Documents for sandbox-exec, so the profile lives next to hours.sh.
mkdir -p "$HOME/Library/Application Support/teenpatti"
cp deploy/sandbox.sb "$HOME/Library/Application Support/teenpatti/sandbox.sb"
PLIST=~/Library/LaunchAgents/com.naveen.teenpatti.plist
cp deploy/com.naveen.teenpatti.plist "$PLIST"
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "App running at http://127.0.0.1:8765"
