"""build ios15 export sets: per-image-basename, plus a global union index.

the source is either an extracted dyld shared cache directory or the
`ios15-exports.txt.xz` dump made from one by dumpdb.py; both give the same
index.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sys, os, pickle, collections, struct, lzma
import mo
from dsc import Cache, image_exports, lcs, NUL

DSC = sys.argv[1]
OUT = sys.argv[2]
BINS = sys.argv[3:]

EXTRA = [
    'libswiftFoundation.dylib', 'libswiftCoreFoundation.dylib',
    'libswiftCoreGraphics.dylib', 'libswiftDarwin.dylib',
    'libswiftDispatch.dylib', 'libswiftObjectiveC.dylib',
    'libswiftUIKit.dylib', 'libswiftCoreImage.dylib',
    'libswiftCoreLocation.dylib', 'libswiftMetal.dylib',
    'libswiftos.dylib', 'libswiftQuartzCore.dylib',
    'libswiftCoreMedia.dylib', 'libswiftAVFoundation.dylib',
    'libswiftCoreAudio.dylib', 'libswiftsimd.dylib',
    'libswiftNetwork.dylib', 'libswiftPhotos.dylib',
    'libswiftWebKit.dylib', 'libswiftSwiftOnoneSupport.dylib',
    'libswift_Concurrency.dylib', 'libswift_Differentiation.dylib',
    'libswiftCore.dylib', 'libobjc.A.dylib', 'libSystem.B.dylib',
]


class CacheSource:
    def __init__(s, d):
        s.c = Cache(d)
        s.imgs = s.c.images()
        s.addr = {p: a for a, p in s.imgs}
        s.count = len(s.imgs)
        s.paths = [p for _a, p in s.imgs]

    def exports(s, p):
        try:
            return set(image_exports(s.c, s.addr[p]))
        except Exception:
            return set()

    def reexports(s, p):
        out = []
        for cmd, b in lcs(s.c, s.addr[p]):
            if cmd == 0x8000001f:
                noff = struct.unpack_from('<I', b, 8)[0]
                out.append(b[noff:].split(NUL)[0].decode())
        return out


class DumpSource:
    def __init__(s, path):
        s.ex, s.re, s.paths = {}, {}, []
        s.count = 0
        cur = None
        with lzma.open(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.startswith('#images '):
                    s.count = int(line.split()[1])
                elif line.startswith('@'):
                    cur = line[1:]
                    s.paths.append(cur)
                    s.ex[cur], s.re[cur] = set(), []
                elif line.startswith('>'):
                    s.re[cur].append(line[1:])
                elif line:
                    s.ex[cur].add(line)
        s.addr = {p: p for p in s.paths}

    def exports(s, p):
        return set(s.ex[p])

    def reexports(s, p):
        return s.re[p]


src = DumpSource(DSC) if os.path.isfile(DSC) else CacheSource(DSC)
byname = collections.defaultdict(list)
for p in src.paths:
    byname[os.path.basename(p)].append(p)

memo = {}
def ex_for_path(p, depth=0):
    if p in memo:
        return memo[p]
    memo[p] = set()
    if p not in src.addr:
        return memo[p]
    s = src.exports(p)
    if depth < 4:
        for r in src.reexports(p):
            s |= ex_for_path(r, depth + 1)
    memo[p] = s
    return s

wanted = set(EXTRA)
for b in BINS:
    m = mo.MachO(open(b, 'rb').read())
    for _i, _c, nm in m.dylibs():
        wanted.add(os.path.basename(nm))

exports = {}
absent = []
for lib in sorted(wanted):
    if lib not in byname:
        absent.append(lib)
        continue
    s = set()
    for p in byname[lib]:
        s |= ex_for_path(p)
    exports[lib] = s

union = {}
for lib, s in exports.items():
    for n in s:
        union.setdefault(n, []).append(lib)

pickle.dump({'exports': exports, 'absent': absent, 'union': union},
            open(OUT, 'wb'))
print('images in cache:', src.count)
print('absent:', len(absent), sorted(absent))
for k in ('libswiftFoundation.dylib', 'libswiftCoreGraphics.dylib',
          'Foundation', 'UIKit', 'libswiftCore.dylib'):
    print(f'  {k}: {len(exports.get(k, ()))}')
