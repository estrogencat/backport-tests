"""end to end: turn the extracted discord 349.0 bundle into an ios-15 one.

  cp the bundle -> drop LockscreenWidget.appex -> patch the four mach-os ->
  drop the shim into Frameworks/ -> MinimumOSVersion 15.0 in every Info.plist

nothing is signed and nothing is zipped: re-signing is a separate, explicit
step.
"""
import sys, os, shutil, plistlib, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch, plan, neuter

HERE = os.path.dirname(os.path.abspath(__file__))
BINARIES = [
    'Discord',
    'PlugIns/NotificationService.appex/NotificationService',
    'PlugIns/Share.appex/Share',
    'PlugIns/BroadcastUpload.appex/BroadcastUpload',
]
PLISTS = [
    'Info.plist',
    'PlugIns/NotificationService.appex/Info.plist',
    'PlugIns/Share.appex/Info.plist',
    'PlugIns/BroadcastUpload.appex/Info.plist',
]
DROP = ['PlugIns/LockscreenWidget.appex']


def set_minos_plist(path, ver='15.0'):
    raw = open(path, 'rb').read()
    fmt = plistlib.FMT_BINARY if raw[:8] == b'bplist00' else plistlib.FMT_XML
    d = plistlib.loads(raw)
    old = d.get('MinimumOSVersion')
    d['MinimumOSVersion'] = ver
    plistlib.dump(d, open(path, 'wb'), fmt=fmt)
    return old


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='.../b349/Payload/Discord.app')
    ap.add_argument('--out', required=True, help='output dir (Payload/ goes here)')
    ap.add_argument('--pkl', required=True)
    ap.add_argument('--shim-syms', required=True)
    ap.add_argument('--shim', required=True)
    ap.add_argument('--copy', action='store_true',
                    help='copy the whole bundle (slow); otherwise assume --out '
                         'already holds a copy')
    a = ap.parse_args()

    app = os.path.join(a.out, 'Payload', 'Discord.app')
    if a.copy:
        if os.path.exists(os.path.join(a.out, 'Payload')):
            shutil.rmtree(os.path.join(a.out, 'Payload'))
        os.makedirs(os.path.dirname(app), exist_ok=True)
        print('copying bundle ...')
        shutil.copytree(a.src, app)

    for d in DROP:
        p = os.path.join(app, d)
        if os.path.isdir(p):
            shutil.rmtree(p)
            print(f'dropped {d}')

    shim_syms = set(l.strip() for l in open(a.shim_syms) if l.strip())
    for rel in BINARIES:
        src = os.path.join(a.src, rel)
        dst = os.path.join(app, rel)
        log, m = patch.patch(src, dst, a.pkl, shim_syms)
        n = sum(v for k, v in log.items() if k == 'fixup repointed')
        print(f'patched {rel}: {n} imports repointed, '
              f'slack left {m.hdr_slack()}')

    # ios-16 call sites in discord's own code that no stub can satisfy
    print('neutering the window-scene geometry request:')
    neuter.neuter(os.path.join(app, 'Discord'))

    fw = os.path.join(app, 'Frameworks', os.path.basename(a.shim))
    shutil.copyfile(a.shim, fw)
    shutil.copyfile(a.shim + '.map', fw + '.map')
    print(f'installed {os.path.relpath(fw, app)}')

    for rel in PLISTS:
        old = set_minos_plist(os.path.join(app, rel))
        print(f'{rel}: MinimumOSVersion {old} -> 15.0')

    print(f'\nbundle at {app}')


if __name__ == '__main__':
    main()
