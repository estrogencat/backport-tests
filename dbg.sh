#!/bin/sh
# start debugserver, launch the app, then run a probe script against it
#   ./dbg.sh probe_trace.py
set -e
cd "$(dirname "$0")"
. ./env.sh
need PHONE
rsh_script "BUNDLE=$BUNDLE PORT=$DBG_PORT" >/dev/null 2>&1 <<'EOS'
pkill -f debugserver 2>/dev/null
sleep 1
nohup /var/jb/usr/bin/debugserver 0.0.0.0:$PORT --waitfor=Discord > /var/root/ds.log 2>&1 &
sleep 1
uiopen --bundleid "$BUNDLE"
sleep 3
EOS
timeout ${T:-180} python -I "$@"
