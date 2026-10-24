"""build ios15 export sets: per-image-basename, plus a global union index."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sys, os, pickle, collections, struct
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

c = Cache(DSC)
imgs = c.images()
bypath = {p: a for a, p in imgs}
byname = collections.defaultdict(list)
for a, p in imgs:
    byname[os.path.basename(p)].append(p)

def reexports(addr):
    out = []
    for cmd, b in lcs(c, addr):
        if cmd == 0x8000001f:
            noff = struct.unpack_from('<I', b, 8)[0]
            out.append(b[noff:].split(NUL)[0].decode())
    return out

memo = {}
def ex_for_path(p, depth=0):
    if p in memo:
        return memo[p]
    memo[p] = set()
    a = bypath.get(p)
    if a is None:
        return memo[p]
    s = set()
    try:
        s |= set(image_exports(c, a))
    except Exception:
        pass
    if depth < 4:
        for r in reexports(a):
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
print('images in cache:', len(imgs))
print('absent:', len(absent), sorted(absent))
for k in ('libswiftFoundation.dylib', 'libswiftCoreGraphics.dylib',
          'Foundation', 'UIKit', 'libswiftCore.dylib'):
    print(f'  {k}: {len(exports.get(k, ()))}')
