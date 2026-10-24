# everything machine-specific comes from the environment. run.sh only needs
# IPA; the phone scripts also need PHONE.

# the phone, reachable over ssh as root (e.g. a dopamine/palera1n jailbreak)
PHONE=${PHONE:-}
SSH_PORT=${SSH_PORT:-22}
KEY_ROOT=${KEY_ROOT:-~/.ssh/id_ed25519}
BUNDLE=${BUNDLE:-com.hammerandchisel.discord}
DBG_PORT=${DBG_PORT:-1234}

# what ios 15 exports: the shipped dump (made from 15.7.7 by dumpdb.py), or
# a directory holding an extracted ios 15 dyld shared cache
DSC=${DSC:-ios15-exports.txt.xz}

# which discord release we are targeting, and the decrypted ipa it comes from
VER=${VER:-350}
IPA=${IPA:-}

REMOTE_PATH=/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin

rsh() { ssh -i $KEY_ROOT -p $SSH_PORT root@$PHONE "PATH=$REMOTE_PATH $*"; }
rsh_script() { ssh -i $KEY_ROOT -p $SSH_PORT root@$PHONE "PATH=$REMOTE_PATH $* sh -s"; }
rcp() { scp -q -i $KEY_ROOT -P $SSH_PORT "$@"; }

need() {
    for v in "$@"; do
        eval "val=\${$v}"
        [ -n "$val" ] || { echo "set $v (see README)"; exit 1; }
    done
}

# where generated things go. out/ is entirely regenerable.
D=out/$VER
SRC=$D/src/Payload/Discord.app
PKL=$D/ios15_exports.pkl
SHIM=$D/libdiscordshim.dylib
BUNDLE_DIR=$D/bundle
APP=$BUNDLE_DIR/Payload/Discord.app
OUT_IPA=$D/Discord$VER-ios15.ipa
