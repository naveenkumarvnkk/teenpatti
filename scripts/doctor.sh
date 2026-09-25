#!/usr/bin/env bash
# Check Teen Patti, repair what can be repaired safely, and print a status table.
#   scripts/doctor.sh            check + fix (open the table if it should be open, reinstall schedule)
#   scripts/doctor.sh --no-fix   report only
#   scripts/doctor.sh --restart  restart the app first (after a code or seats.yaml change), then check + fix
# Same interface and output as trading-agent's doctor, so portfolio/doctor-all.sh can run both.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

FIX=1; RESTART=0
for a in "$@"; do
  case $a in
    --no-fix) FIX=0 ;;
    --restart) RESTART=1 ;;
    -h|--help) sed -n 2,5p "$0"; exit 0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done

DOMAIN=gui/$(id -u)
APP=com.naveen.teenpatti; TUNNEL=com.naveen.teenpatti-tunnel; HOURS=com.naveen.teenpatti-hours
SUPPORT="$HOME/Library/Application Support/teenpatti"
PUBLIC=https://www.meetnaveenkunisetty.com/teenpatti/
ORIGIN=https://teenpatti-origin.meetnaveenkunisetty.com/
LOCAL=http://127.0.0.1:8765/
MODEL=$(sed -n 's/^  model: *\([^ #]*\).*/\1/p' seats.yaml | head -1)
OPEN_MIN=$((7 * 60 + 58)); CLOSE_MIN=$((20 * 60 + 5))   # keep in sync with deploy/hours.sh

WORST=0   # 0 OK, 1 WARN, 2 FAIL
row() {   # row STATE name detail
  case $1 in
    OK)   printf ' ✔ OK   %-11s %s\n' "$2" "$3" ;;
    WARN) printf ' ! WARN %-11s %s\n' "$2" "$3"; [ $WORST -lt 1 ] && WORST=1 ;;
    FAIL) printf ' ✘ FAIL %-11s %s\n' "$2" "$3"; WORST=2 ;;
  esac
}
pid_of() { launchctl list | awk -v l="$1" '$3==l{print $1}'; }
running() { local p; p=$(pid_of "$1"); [ -n "$p" ] && [ "$p" != "-" ]; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-10}" "$1"; }

now=$((10#$(date +%H) * 60 + 10#$(date +%M)))
if [ $now -ge $OPEN_MIN ] && [ $now -lt $CLOSE_MIN ]; then SHOULD=open; else SHOULD=closed; fi

echo "teen patti doctor -- $(date '+%F %H:%M %Z') ($([ $FIX = 1 ] && echo 'check + fix' || echo 'report only'))"
echo

if [ $RESTART = 1 ] && running $APP; then
  launchctl kickstart -k "$DOMAIN/$APP" && sleep 4
fi

# schedule: the launchd job that opens/closes the table
if launchctl print "$DOMAIN/$HOURS" >/dev/null 2>&1 && [ -x "$SUPPORT/hours.sh" ]; then
  row OK schedule "installed; table should be $SHOULD now (open 07:58-20:05 local)"
elif [ $FIX = 1 ] && ./deploy/install-hours.sh >/dev/null 2>&1; then
  row WARN schedule "was missing; reinstalled"
else
  row FAIL schedule "not installed: run deploy/install-hours.sh"
fi

# open the table if it should be open but isn't (never closes it: a game may be running)
if [ $SHOULD = open ] && [ $FIX = 1 ] && { ! running $APP || ! running $TUNNEL; }; then
  "$SUPPORT/hours.sh" open >/dev/null 2>&1; sleep 5; FIXED_OPEN=1
fi

# app: launchd service, sandboxed, answering on 127.0.0.1 only
if running $APP; then
  pid=$(pid_of $APP)
  sbx=$(/usr/bin/python3 -c "import ctypes;print(bool(ctypes.CDLL(None).sandbox_check($pid,None,0)))" 2>/dev/null)
  bind=$(lsof -nP -a -p "$pid" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | head -1)
  c=$(code $LOCAL)
  if [ "$c" != 200 ]; then row FAIL app "pid $pid but $LOCAL returned $c"
  elif [ "$sbx" != True ]; then row WARN app "pid $pid answering, but NOT sandboxed: run deploy/install-app.sh"
  elif [ "${bind%%:*}" != 127.0.0.1 ]; then row FAIL app "listening on $bind (must be 127.0.0.1 only)"
  else row OK app "pid $pid, sandboxed, listening on $bind${FIXED_OPEN:+ (started by doctor)}"; fi
elif [ $SHOULD = open ]; then row FAIL app "not running during open hours: deploy/hours.sh open"
else row OK app "stopped (outside hours)"; fi

# tunnel: outbound connections to Cloudflare
if running $TUNNEL; then
  conns=$(tail -200 /tmp/teenpatti-tunnel.log 2>/dev/null | grep -c "Registered tunnel connection")
  row OK tunnel "pid $(pid_of $TUNNEL), ${conns} connection registrations in recent log"
elif [ $SHOULD = open ]; then row FAIL tunnel "not running during open hours"
else row OK tunnel "stopped (outside hours)"; fi

# ollama + model
if tags=$(curl -s --max-time 5 http://127.0.0.1:11434/api/tags); then
  if grep -q "\"$MODEL\"" <<<"$tags"; then
    obind=$(lsof -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | head -1)
    if [ "${obind%%:*}" = 127.0.0.1 ]; then row OK ollama "$MODEL ready, listening on $obind"
    else row FAIL ollama "listening on $obind (must be 127.0.0.1 only)"; fi
  else row FAIL ollama "running, but model $MODEL missing: ollama pull $MODEL"; fi
elif [ $SHOULD = open ]; then row FAIL ollama "not answering on 127.0.0.1:11434: open the Ollama app"
else row WARN ollama "not answering (fine while closed, needed by 07:58)"; fi

# public: what a visitor gets through the router
pc=$(code "$PUBLIC?doctor=$RANDOM" 15)
if [ $SHOULD = open ]; then
  [ "$pc" = 200 ] && row OK public "$PUBLIC -> 200" || row FAIL public "$PUBLIC -> $pc (visitors see the downtime page)"
else
  [ "$pc" = 503 ] && row OK public "$PUBLIC -> 503 closed page" || row WARN public "$PUBLIC -> $pc (expected the closed page)"
fi

# origin lock: direct hits without the router's key must be refused
if running $TUNNEL; then
  oc=$(code "$ORIGIN" 15)
  [ "$oc" = 404 ] && row OK origin "direct access refused (404)" || row FAIL origin "direct access returned $oc (origin key check broken?)"
fi

# errors: model/agent warnings and crashes since the app last started
if [ -f teenpatti.log ]; then
  since=$(awk '/Started server process/{n=NR} END{print n+0}' teenpatti.log)
  recent=$(tail -n +"$((since > 0 ? since : 1))" teenpatti.log | grep -E "WARNING|ERROR|Traceback|Error:")
  n=$(grep -c . <<<"$recent")
  [ "$n" = 0 ] && row OK errors "none since the app started" \
    || row WARN errors "$n since the app started; latest: $(tail -1 <<<"$recent" | cut -c1-100)"
  mb=$(du -m teenpatti.log | cut -f1)
  [ "$mb" -lt 20 ] && row OK logs "teenpatti.log ${mb} MB" || row WARN logs "teenpatti.log ${mb} MB: consider truncating"
fi

echo
LEVELS=(OK WARN FAIL)
echo "overall: ${LEVELS[$WORST]}"
exit $WORST
