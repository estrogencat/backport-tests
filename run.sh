#!/bin/sh
# build a patched, unsigned ios-15 bundle and prove every strong import
# resolves. nothing here touches the phone; install.sh does that.
#
#   ./run.sh
#   VER=351 IPA=/path/to/new.ipa PREV=350 ./run.sh
set -e
cd "$(dirname "$0")"
. ./env.sh
need DSC

mkdir -p "$D" syms/$VER

# 1. unpack the ipa (treated as untrusted: nothing is run from inside it)
if [ ! -d "$SRC" ]; then
    need IPA
    echo "== unpacking $IPA"
    rm -rf "$D/src"
    mkdir -p "$D/src"
    python -I -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$IPA" "$D/src"
fi

BINS="$SRC/Discord
$SRC/PlugIns/NotificationService.appex/NotificationService
$SRC/PlugIns/Share.appex/Share
$SRC/PlugIns/BroadcastUpload.appex/BroadcastUpload"
FWS="$SRC/Frameworks/RiveRuntime.framework/RiveRuntime
$SRC/Frameworks/YbridOgg.framework/YbridOgg
$SRC/Frameworks/YbridOpus.framework/YbridOpus
$SRC/Frameworks/hermesvm.framework/hermesvm"

# 2. export index from the real 15.7.7 cache, re-export chains followed
echo "== export index"
python -I collect15.py "$DSC" "$PKL" $BINS $FWS

# 3. per-import verdict (also confirms the bundled frameworks need no patch)
echo "== classify"
SHIMDIR=syms/$VER python -I plan.py "$PKL" $BINS $FWS

cat syms/$VER/shim_Discord.txt syms/$VER/shim_NotificationService.txt \
    syms/$VER/shim_Share.txt syms/$VER/shim_BroadcastUpload.txt \
    | sort -u > syms/$VER/shim_all.txt
echo "   shim surface: $(wc -l < syms/$VER/shim_all.txt) symbols"

# 4. the shim, plus two independent checks of it
echo "== shim"
python -I mkshim.py "$SHIM" syms/$VER/shim_all.txt
python -I checkshim.py "$SHIM" syms/$VER/shim_all.txt
python -E -P shimverify.py "$SHIM"
python -E -P clockverify.py "$SHIM"

# 5. assemble
echo "== bundle"
python -I build.py --src "$SRC" --out "$BUNDLE_DIR" --pkl "$PKL" \
    --shim-syms syms/$VER/shim_all.txt --shim "$SHIM" --copy

# 6. the thing that has to be true
echo "== verify"
python -I verify.py --pkl "$PKL" \
    --shim "$APP/Frameworks/libdiscordshim.dylib" \
    --bundle-dir "$APP/Frameworks" \
    "$APP/Discord" \
    "$APP/PlugIns/NotificationService.appex/NotificationService" \
    "$APP/PlugIns/Share.appex/Share" \
    "$APP/PlugIns/BroadcastUpload.appex/BroadcastUpload"

# 7. package
echo "== ipa"
python -I mkipa.py --bundle "$BUNDLE_DIR" --out "$OUT_IPA"

# 8. what moved since the last release
if [ -n "$PREV" ] && [ -f "syms/$PREV/shim_all.txt" ]; then
    echo "== shim surface vs $PREV"
    echo "   new:";     comm -13 syms/$PREV/shim_all.txt syms/$VER/shim_all.txt | sed 's/^/     /'
    echo "   dropped:"; comm -23 syms/$PREV/shim_all.txt syms/$VER/shim_all.txt | sed 's/^/     /'
fi
