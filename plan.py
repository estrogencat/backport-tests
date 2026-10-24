"""classify a mach-o's strong imports against the ios15 cache export index.

for each strong import that does not resolve in the library its ordinal names,
pick a retarget: an ios15 dylib that does export it, else the shim.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pickle, collections, struct
import mo, cfix

BUNDLED_PREFIX = ('@rpath', '@executable_path', '@loader_path')
# order of preference when a symbol lives somewhere else on ios15
PREF = ['libswiftFoundation.dylib', 'libswiftCoreGraphics.dylib',
        'libswiftCoreFoundation.dylib', 'libswiftCore.dylib',
        'Foundation', 'CoreFoundation', 'libobjc.A.dylib', 'libSystem.B.dylib']

SPECIAL_ORD = {0xfc, 0xfd, 0xfe, 0xff, 0}


def load_index(pkl):
    d = pickle.load(open(pkl, 'rb'))
    return d['exports'], set(d['absent']), d['union']


def classify(path, exports, absent, union):
    m = mo.MachO(open(path, 'rb').read())
    dl = m.dylibs()
    libname = {i: n for i, c, n in dl}
    f = cfix.Fixups(m)
    st = cfix.Symtab(m)

    rows = []           # (where, idx, symbol, cur_ord, cur_lib, verdict, target)
    for i in range(f.imports_count):
        lib, weak, noff, name = f.get(i)
        rows.append(('fixup', i, name, lib, weak))
    sym_rows = [('symtab', i, name, ordi, weak)
                for i, name, ordi, weak in st.undef()]

    def verdict(name, ordi, weak):
        if ordi in SPECIAL_ORD:
            return 'special', None
        lp = libname.get(ordi)
        if lp is None:
            return 'badord', None
        base = os.path.basename(lp)
        if lp.startswith(BUNDLED_PREFIX) and base not in exports:
            return 'bundled', None
        ex = exports.get(base)
        if base not in absent and ex is not None and name in ex:
            return 'ok', None
        if weak:
            return 'weak-missing', None
        cands = union.get(name)
        if cands:
            for p in PREF:
                if p in cands:
                    return 'retarget', p
            return 'retarget', sorted(cands)[0]
        return 'shim', None

    out = []
    for where, i, name, ordi, weak in rows + sym_rows:
        v, t = verdict(name, ordi, weak)
        out.append((where, i, name, ordi, libname.get(ordi), v, t))
    return m, f, st, out


def main():
    pkl, binaries = sys.argv[1], sys.argv[2:]
    exports, absent, union = load_index(pkl)
    for b in binaries:
        m, f, st, rows = classify(b, exports, absent, union)
        c = collections.Counter(r[5] for r in rows)
        print(f'=== {os.path.basename(b)}  (imports {f.imports_count}, undef syms {st.nsyms})')
        for k, n in c.most_common():
            print(f'    {k}: {n}')
        rt = collections.Counter((os.path.basename(r[4] or "?"), r[6])
                                 for r in rows if r[5] == 'retarget' and r[0] == 'fixup')
        for (a, t), n in rt.most_common():
            print(f'      retarget {a} -> {t}: {n}')
        shim = sorted(set(r[2] for r in rows if r[5] == 'shim'))
        print(f'      shim-needed unique symbols: {len(shim)}')
        tag = os.path.basename(b)
        outdir = os.environ.get('SHIMDIR', '.')
        os.makedirs(outdir, exist_ok=True)
        open(os.path.join(outdir, f'shim_{tag}.txt'), 'w').write(
            '\n'.join(shim) + '\n')


if __name__ == '__main__':
    main()
