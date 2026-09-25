#!/usr/bin/env bash
# Open or close the table on the Mac: starts/stops the app + tunnel and frees the model's RAM.
#   deploy/hours.sh auto    decide from the clock (what launchd runs at 07:58, 20:05 and login)
#   deploy/hours.sh open | close
# Visitors are gated by the portfolio router's HOURS (8 AM–8 PM Central); this only saves the Mac's
# resources. Close runs at 20:05 so a game that started just before 8 PM can finish.
# launchd can't run scripts inside ~/Documents (macOS privacy protection), so deploy/install-hours.sh
# installs a copy to ~/Library/Application Support/teenpatti/ with MODEL filled in.
set -uo pipefail
OPEN_MIN=$((7 * 60 + 58)); CLOSE_MIN=$((20 * 60 + 5))   # Mac local time (Central)
DOMAIN=gui/$(id -u); LA=~/Library/LaunchAgents
MODEL="${MODEL:-__MODEL__}"   # set by install-hours.sh; re-run it after changing the model

start() { launchctl print "$DOMAIN/$1" >/dev/null 2>&1 || launchctl bootstrap "$DOMAIN" "$LA/$1.plist"; }
stop()  { launchctl bootout "$DOMAIN/$1" 2>/dev/null || true; }

mode=${1:-auto}
if [ "$mode" = auto ]; then
  now=$((10#$(date +%H) * 60 + 10#$(date +%M)))
  if [ $now -ge $OPEN_MIN ] && [ $now -lt $CLOSE_MIN ]; then mode=open; else mode=close; fi
fi
case $mode in
  open)  start com.naveen.teenpatti; start com.naveen.teenpatti-tunnel ;;
  close) stop com.naveen.teenpatti-tunnel; stop com.naveen.teenpatti
         [ -n "$MODEL" ] && ollama stop "$MODEL" 2>/dev/null ;;
  *) echo "usage: $0 [auto|open|close]"; exit 2 ;;
esac
echo "$(date '+%F %T') table $mode"
