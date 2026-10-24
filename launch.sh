#!/bin/sh
# launch on the phone and report whether it stayed up
set -e
cd "$(dirname "$0")"
. ./env.sh
need PHONE
rsh_script "BUNDLE=$BUNDLE W=${W:-15}" <<'EOS'
pkill -f debugserver 2>/dev/null
touch /var/root/.dmark
uiopen --bundleid "$BUNDLE"
sleep "$W"
echo "--- process:"
ps aux | grep '[D]iscord.app/Discord' | head -1 || echo "NOT RUNNING"
echo "--- new crash reports:"
find /var/mobile/Library/Logs/CrashReporter -name 'Discord*' -newer /var/root/.dmark 2>/dev/null || echo none
EOS
