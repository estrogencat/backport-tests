#!/bin/sh
# install the built ipa over trollstore. replaces whatever discord is there.
set -e
cd "$(dirname "$0")"
. ./env.sh
need PHONE
[ -f "$OUT_IPA" ] || { echo "no $OUT_IPA -- run ./run.sh first"; exit 1; }
echo "== uploading $OUT_IPA"
rcp "$OUT_IPA" root@$PHONE:/var/root/discord$VER.ipa
rsh_script "VER=$VER" <<'EOS'
TS=$(find /var/containers/Bundle/Application -maxdepth 3 -path '*TrollStore.app/trollstorehelper' | head -1)
[ -z "$TS" ] && { echo "trollstorehelper not found"; exit 1; }
"$TS" install /var/root/discord$VER.ipa > /var/root/d_install.log 2>&1
echo "trollstorehelper exit=$?"
grep -niE 'bailing|already installed|new app path|returning' /var/root/d_install.log | tail -3
EOS
