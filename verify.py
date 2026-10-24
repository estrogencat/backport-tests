"""re-run the ios-15 resolvability check against patched binaries.

the export index is the real 15.7.7 dyld shared cache plus the shim's own
export trie, so a "strong missing" of 0 means every non-weak import of the
binary is exported by something that will actually be in the process.
"""
import sys, os, pickle, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix, dsc, plan

LC_DYLD_INFO_ONLY = 0x80000022


def shim_exports(path):
    d = open(path, 'rb').read()
    m = mo.MachO(d)
    t = m.find(mo.LC_DYLD_EXPORTS_TRIE)
    if t:
        off, size = struct.unpack_from('<II', m.d, t[0][1] + 8)
    else:
        info = m.find(LC_DYLD_INFO_ONLY)[0]
        v = struct.unpack_from('<10I', m.d, info[1] + 8)
        off, size = v[8], v[9]
    return set(dsc.trie_exports(d[off:off + size]))


import struct  # noqa: E402  (used by shim_exports)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pkl', required=True)
    ap.add_argument('--shim')
    ap.add_argument('--bundle-dir', help='Discord.app/Frameworks')
    ap.add_argument('binaries', nargs='+')
    a = ap.parse_args()

    d = pickle.load(open(a.pkl, 'rb'))
    exports, absent, union = d['exports'], set(d['absent']), d['union']
    if a.shim:
        ex = shim_exports(a.shim)
        exports['libdiscordshim.dylib'] = ex
        for n in ex:
            union.setdefault(n, []).append('libdiscordshim.dylib')
        print(f'shim provides {len(ex)} symbols')
    if a.bundle_dir:
        for fw in sorted(os.listdir(a.bundle_dir)):
            if not fw.endswith('.framework'):
                continue
            nm = fw.split('.')[0]
            b = os.path.join(a.bundle_dir, fw, nm)
            if not os.path.exists(b):
                continue
            ex = shim_exports(b)
            exports[nm] = ex
            absent.discard(nm)
            for n in ex:
                union.setdefault(n, []).append(nm)
            print(f'bundled {nm}: {len(ex)} symbols')

    rc = 0
    for b in a.binaries:
        m, f, st, rows = plan.classify(b, exports, absent, union)
        c = collections.Counter(r[5] for r in rows)
        strong_missing = c['retarget'] + c['shim'] + c['badord']
        print(f'=== {os.path.basename(b)}')
        bv = [struct.unpack_from('<4I', m.d, x[1] + 8)
              for x in m.find(mo.LC_BUILD_VERSION)]
        print(f'    minos {[f"{v[1]>>16}.{(v[1]>>8)&0xff}" for v in bv]}')
        for k in ('ok', 'special', 'bundled', 'weak-missing', 'retarget',
                  'shim', 'badord'):
            if c[k]:
                print(f'    {k}: {c[k]}')
        print(f'    STRONG MISSING: {strong_missing}')
        if strong_missing:
            rc = 1
            for r in rows:
                if r[5] in ('retarget', 'shim', 'badord'):
                    print('      ', r[2], r[4])
    return rc


if __name__ == '__main__':
    sys.exit(main())
