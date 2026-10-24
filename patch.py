"""patch a discord 349.0 mach-o so every strong import resolves on ios 15.

three edits, all in place inside the existing load-command padding:

  1. LC_BUILD_VERSION minos -> 15.0
  2. LC_LOAD_DYLIB -> LC_LOAD_WEAK_DYLIB for every framework that does not
     exist on ios 15 at all (AppIntents, libswift_StringProcessing, ...), so
     dyld does not refuse the image outright
  3. for every strong import that its own library does not export on ios 15,
     repoint the library ordinal -- in the DYLD_CHAINED_FIXUPS imports array
     and in the matching LC_SYMTAB n_desc -- at a library that does export it
     (the swift Foundation / CoreGraphics overlays), or at the shim

new LC_LOAD_DYLIBs are appended to the end of the load-command list so the
ordinals already in use keep their meaning; they fit in the padding between
the header and the first section, so nothing moves and no offset is rewritten.
the code signature is invalidated by design -- re-sign afterwards.
"""
import sys, os, struct, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix, plan

SHIM = '@rpath/libdiscordshim.dylib'
KNOWN_PATHS = {
    'libswiftFoundation.dylib': '/usr/lib/swift/libswiftFoundation.dylib',
    'libswiftCoreGraphics.dylib': '/usr/lib/swift/libswiftCoreGraphics.dylib',
    'libswiftCoreFoundation.dylib':
        '/usr/lib/swift/libswiftCoreFoundation.dylib',
    'libswiftCore.dylib': '/usr/lib/swift/libswiftCore.dylib',
    'libobjc.A.dylib': '/usr/lib/libobjc.A.dylib',
    'libSystem.B.dylib': '/usr/lib/libSystem.B.dylib',
    'Foundation': '/System/Library/Frameworks/Foundation.framework/Foundation',
    'CoreFoundation':
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation',
}


def patch(path, outpath, pkl, shim_syms, minos=(15, 0)):
    exports, absent, union = plan.load_index(pkl)
    m, f, st, rows = plan.classify(path, exports, absent, union)
    log = collections.Counter()

    # ---- 2. weaken load commands for frameworks ios 15 does not have
    for c in m.cmds:
        if c[0] != mo.LC_LOAD_DYLIB:
            continue
        noff = struct.unpack_from('<I', m.d, c[1] + 8)[0]
        nm = bytes(m.d[c[1] + noff:c[1] + c[2]]).split(b'\x00')[0].decode()
        if nm.startswith(('@rpath', '@executable_path', '@loader_path')):
            continue
        if os.path.basename(nm) in absent:
            struct.pack_into('<I', m.d, c[1], mo.LC_LOAD_WEAK_DYLIB)
            log['weakened ' + os.path.basename(nm)] += 1
    m.parse()

    # ---- 3a. work out which extra libraries we have to be able to name
    targets = set()
    for where, i, name, ordi, lib, v, t in rows:
        if v == 'retarget':
            targets.add(t)
        elif v == 'shim':
            targets.add('@shim')

    have = {}
    for idx, cmd, nm in m.dylibs():
        have.setdefault(os.path.basename(nm), idx)
        have.setdefault(nm, idx)

    ordinal = {}
    for t in sorted(targets):
        p = SHIM if t == '@shim' else KNOWN_PATHS.get(t, t)
        key = os.path.basename(p)
        if t == '@shim':
            key = SHIM
        if key in have:
            ordinal[t] = have[key]
            log['reused ordinal for ' + key] += 1
        else:
            m.append_cmd(mo.dylib_cmd(mo.LC_LOAD_DYLIB, p))
            ordinal[t] = len(m.dylibs())
            log['added LC_LOAD_DYLIB ' + p] += 1
        assert ordinal[t] < 0xf0, 'ordinal would collide with a special value'

    # ---- 3b. rewrite the ordinals
    f = cfix.Fixups(m)
    st = cfix.Symtab(m)
    for where, i, name, ordi, lib, v, t in rows:
        if v == 'retarget':
            new = ordinal[t]
        elif v == 'shim':
            new = ordinal['@shim']
        else:
            continue
        if where == 'fixup':
            f.set_lib(i, new)
            log['fixup repointed'] += 1
        else:
            st.set_ord(i, new)
            log['symtab repointed'] += 1
        if v == 'shim' and name not in shim_syms:
            raise SystemExit(f'shim does not export {name}')

    # ---- 1. minos
    for c in m.find(mo.LC_BUILD_VERSION):
        struct.pack_into('<I', m.d, c[1] + 12,
                         (minos[0] << 16) | (minos[1] << 8))
        log['minos set'] += 1
    for c in m.find(mo.LC_VERSION_MIN_IPHONEOS):
        struct.pack_into('<I', m.d, c[1] + 8,
                         (minos[0] << 16) | (minos[1] << 8))
        log['version_min set'] += 1

    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    open(outpath, 'wb').write(bytes(m.d))
    return log, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pkl', required=True)
    ap.add_argument('--shim-syms', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('binaries', nargs='+')
    a = ap.parse_args()
    shim = set(l.strip() for l in open(a.shim_syms) if l.strip())
    for b in a.binaries:
        name = os.path.basename(b)
        out = os.path.join(a.out, name)
        log, m = patch(b, out, a.pkl, shim)
        print(f'=== {name} -> {out}')
        print(f'    ncmds {m.ncmds}, sizeofcmds {m.sizeofcmds}, '
              f'header slack left {m.hdr_slack()}')
        for k, v in sorted(log.items()):
            print(f'    {k}: {v}')


if __name__ == '__main__':
    main()
