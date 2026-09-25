#!/usr/bin/env bash
# One-time: create the outbound-only Cloudflare Tunnel for teenpatti and run it as a service.
# The Mac dials out to Cloudflare; nothing on the internet can open a connection to it.
set -euo pipefail
NAME=teenpatti
HOST=teenpatti-origin.meetnaveenkunisetty.com   # origin the portfolio router forwards /teenpatti/* to

command -v cloudflared >/dev/null || brew install cloudflared
[ -f ~/.cloudflared/cert.pem ] || cloudflared tunnel login          # opens browser, pick the domain
cloudflared tunnel info "$NAME" >/dev/null 2>&1 || cloudflared tunnel create "$NAME"
ID=$(cloudflared tunnel list -o json | python3 -c "import json,sys;print([t['id'] for t in json.load(sys.stdin) if t['name']=='$NAME'][0])")

cat > ~/.cloudflared/$NAME.yml <<YML
tunnel: $ID
credentials-file: $HOME/.cloudflared/$ID.json
ingress:
  - hostname: $HOST
    service: http://127.0.0.1:8765
  - service: http_status:404
YML
cloudflared tunnel route dns "$NAME" "$HOST" || true

PLIST=~/Library/LaunchAgents/com.naveen.teenpatti-tunnel.plist
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.naveen.teenpatti-tunnel</string>
  <key>ProgramArguments</key><array>
    <string>$(command -v cloudflared)</string><string>tunnel</string>
    <string>--config</string><string>$HOME/.cloudflared/$NAME.yml</string><string>run</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/tmp/teenpatti-tunnel.log</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Tunnel up: https://$HOST  (add \"/teenpatti\": \"https://$HOST\" to the portfolio router)"
