#!/bin/sh
# pull the newest crash report and symbolize it
set -e
cd "$(dirname "$0")"
. ./env.sh
need PHONE
L=$(rsh "ls -t /var/mobile/Library/Logs/CrashReporter | grep '^Discord' | head -1" | tr -d '\r')
[ -z "$L" ] && { echo "no Discord crash reports"; exit 1; }
echo "# $L"
mkdir -p crashes
rcp "root@$PHONE:/var/mobile/Library/Logs/CrashReporter/$L" crashes/
python -I ips.py "crashes/$L"
