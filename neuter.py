"""neuter discord's ios-16-only window-scene geometry request.

at launch discord enqueues a block on the main NSOperationQueue that does

    scene = UIApplication.sharedApplication.connectedScenes.allObjects[0]
    prefs = [[UIWindowSceneGeometryPreferencesIOS alloc]
                initWithInterfaceOrientations:<captured mask>]
    [scene requestGeometryUpdateWithPreferences:prefs errorHandler:^...]
    [... .rootViewController setNeedsUpdateOfSupportedInterfaceOrientations]

every api in it is ios 16. stubbing is a dead end: the shim's inert
`UIWindowSceneGeometryPreferencesIOS` could be given the init, but the next
message goes to a *real* `UIWindowScene`, which ios 15 does not have either.
so the call site itself is neutered -- the block's invoke function is replaced
with a bare `ret`. doing nothing is the correct behaviour on ios 15, where
orientation goes through `supportedInterfaceOrientations` anyway; the block
returns void and owns none of what it touches, so an immediate return leaks
nothing.

**the site is located by pattern, not by offset** -- discord ships every week
or two. the anchors are the chained-fixup bind for the class, the
`__objc_methname` strings for the selectors, the `__objc_selrefs` slots that
point at them, the `__objc_stubs` entries that load those slots, and
`LC_FUNCTION_STARTS` for the enclosing function's bounds. every count is
asserted to be exactly one, so a release that moves or changes this code is a
loud failure rather than a silent miss.
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix, chains

SEL_INIT = 'initWithInterfaceOrientations:'
SEL_REQUEST = 'requestGeometryUpdateWithPreferences:errorHandler:'
SEL_ENQUEUE = 'addOperationWithBlock:'
CLASS_BIND = '_OBJC_CLASS_$_UIWindowSceneGeometryPreferencesIOS'

RET = 0xD65F03C0
STUB_STRIDE = 0x20


class Fail(SystemExit):
    pass


# ---- minimal arm64 decoding (capstone is not importable under python -I)

def sx(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def d_bl(w, pc):
    if (w >> 26) != 0x25:
        return None
    return pc + 4 * sx(w & 0x3FFFFFF, 26)


def d_adrp(w, pc):
    if (w & 0x9F000000) != 0x90000000:
        return None
    imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
    return w & 0x1F, (pc & ~0xFFF) + (sx(imm, 21) << 12)


def d_add_imm(w):
    if (w & 0xFF800000) != 0x91000000:
        return None
    imm = (w >> 10) & 0xFFF
    if (w >> 22) & 1:
        imm <<= 12
    return w & 0x1F, (w >> 5) & 0x1F, imm


def d_sub_imm(w):
    return (w & 0xFF800000) == 0xD1000000


def d_ldr_imm64(w):
    if (w & 0xFFC00000) != 0xF9400000:
        return None
    return w & 0x1F, (w >> 5) & 0x1F, ((w >> 10) & 0xFFF) * 8


def is_store(w):
    return ((w & 0xFFC00000) == 0xF9000000      # str xt, [xn, #imm]
            or (w & 0xFEC00000) == 0xA8800000   # stp pre/post index
            or (w & 0xFFC00000) == 0xA9000000)  # stp signed offset


# ---- the image

class Image:
    def __init__(s, path):
        s.path = path
        s.d = bytearray(open(path, 'rb').read())
        s.m = mo.MachO(s.d)
        s.base = min(v['vmaddr'] for v in s.m.segs().values() if v['filesize'])
        s.sects = {}
        for seg, sg in s.m.segs().items():
            for sn, sa, ssz, soff, q, fl in sg['sects']:
                s.sects[(seg, sn)] = (sa, ssz, soff)

    def sect(s, seg, sn):
        if (seg, sn) not in s.sects:
            raise Fail(f'{os.path.basename(s.path)}: no {seg},{sn} section')
        return s.sects[(seg, sn)]

    def off(s, vm):
        for (seg, sn), (sa, ssz, soff) in s.sects.items():
            if soff and sa <= vm < sa + ssz:
                return soff + (vm - sa)
        return None

    def word(s, vm):
        o = s.off(vm)
        return struct.unpack_from('<I', s.d, o)[0] if o is not None else None

    def ptr_target(s, vm):
        """a DYLD_CHAINED_PTR_64 rebase slot -> the unslid vmaddr it holds"""
        o = s.off(vm)
        if o is None:
            return None
        v = struct.unpack_from('<Q', s.d, o)[0]
        if v == 0 or v >> 63:
            return None
        return s.base + (v & 0xFFFFFFFFF)

    def function_starts(s):
        c = s.m.find(mo.LC_FUNCTION_STARTS)
        if not c:
            raise Fail('no LC_FUNCTION_STARTS: cannot bound the function')
        off, size = struct.unpack_from('<II', s.d, c[0][1] + 8)
        out, vm, p, end = [], s.base, off, off + size
        while p < end:
            delta, p = mo.uleb(s.d, p)
            if delta == 0:
                break
            vm += delta
            out.append(vm)
        return out


# ---- locating the pieces, each exactly once

def one(what, got):
    if len(got) != 1:
        raise Fail(f'expected exactly 1 {what}, found {len(got)}'
                   + (': ' + ', '.join(hex(x) for x in got[:8]) if got else ''))
    return got[0]


def methname(img, sel):
    sa, ssz, soff = img.sect('__TEXT', '__objc_methname')
    blob = bytes(img.d[soff:soff + ssz])
    pat = b'\x00' + sel.encode() + b'\x00'
    hits, p = [], 0
    while True:
        i = blob.find(pat, p)
        if i < 0:
            break
        hits.append(sa + i + 1)
        p = i + 1
    return one(f'__objc_methname "{sel}"', hits)


def selref(img, name_vm, sel):
    sa, ssz, soff = img.sect('__DATA', '__objc_selrefs')
    hits = [sa + o for o in range(0, ssz, 8)
            if img.ptr_target(sa + o) == name_vm]
    return one(f'__objc_selrefs slot for "{sel}"', hits)


def objc_stub(img, selref_vm, sel):
    """an __objc_stubs entry is adrp x1 / ldr x1, [x1, #n] / ... / br x16"""
    sa, ssz, soff = img.sect('__TEXT', '__objc_stubs')
    hits = []
    for o in range(0, ssz, STUB_STRIDE):
        a = d_adrp(struct.unpack_from('<I', img.d, soff + o)[0], sa + o)
        l = d_ldr_imm64(struct.unpack_from('<I', img.d, soff + o + 4)[0])
        if (a and l and a[0] == 1 and l[0] == 1 and l[1] == 1
                and a[1] + l[2] == selref_vm):
            hits.append(sa + o)
    return one(f'__objc_stubs entry for "{sel}"', hits)


def class_slot(img, name):
    f = cfix.Fixups(img.m)
    imports = [f.get(i) for i in range(f.imports_count)]
    hits = [slot for slot, kind, payload in chains.walk(img.m)
            if kind == 'bind' and payload < len(imports)
            and imports[payload][3] == name]
    return one(f'chained bind for {name}', hits)


def bl_sites(img, target, lo=None, hi=None):
    sa, ssz, soff = img.sect('__TEXT', '__text')
    lo = sa if lo is None else max(lo, sa)
    hi = sa + ssz if hi is None else min(hi, sa + ssz)
    out = []
    for vm in range(lo, hi, 4):
        w = struct.unpack_from('<I', img.d, soff + (vm - sa))[0]
        if d_bl(w, vm) == target:
            out.append(vm)
    return out


def loads_slot(img, lo, hi, slot):
    """instructions in [lo,hi) that adrp+ldr the given pointer slot"""
    page, out = {}, []
    for vm in range(lo, hi, 4):
        w = img.word(vm)
        a = d_adrp(w, vm)
        if a:
            page[a[0]] = a[1]
            continue
        l = d_ldr_imm64(w)
        if l and l[1] in page and page[l[1]] + l[2] == slot:
            out.append(vm)
    return out


def block_literal_setup(img, fn_start, enqueue_stub):
    """where is fn_start installed as a stack block's invoke pointer?

    `adrp x, #page` / `add x, x, #off` materializing fn_start, immediately
    stored, with the enqueue selector sent soon after. this is what makes an
    early `ret` safe: the function is a void block invoke, not a method whose
    caller wants a return value.
    """
    sa, ssz, soff = img.sect('__TEXT', '__text')
    page, hits = {}, []
    for vm in range(sa, sa + ssz, 4):
        w = struct.unpack_from('<I', img.d, soff + (vm - sa))[0]
        a = d_adrp(w, vm)
        if a:
            page[a[0]] = a[1]
            continue
        ad = d_add_imm(w)
        if not ad:
            continue
        rd, rn, imm = ad
        if rn not in page or page[rn] + imm != fn_start:
            continue
        if not any(is_store(img.word(vm + 4 * k) or 0) for k in (1, 2, 3)):
            continue
        if not bl_sites(img, enqueue_stub, vm, vm + 4 * 24):
            continue
        hits.append(vm)
    return hits


# ---- the edit

def find(img, verbose=True):
    def say(*a):
        if verbose:
            print(*a)

    cls = class_slot(img, CLASS_BIND)
    say(f'   classref slot {cls:#x}  ({CLASS_BIND})')

    stubs = {}
    for sel in (SEL_INIT, SEL_REQUEST, SEL_ENQUEUE):
        nm = methname(img, sel)
        sr = selref(img, nm, sel)
        stubs[sel] = objc_stub(img, sr, sel)
        say(f'   "{sel}" name {nm:#x} selref {sr:#x} stub {stubs[sel]:#x}')

    calls = bl_sites(img, stubs[SEL_INIT])
    if not calls:
        raise Fail(f'no call site of "{SEL_INIT}" at all')
    say('   init call sites ' + ', '.join(f'{c:#x}' for c in calls))

    # the init selector alone is not unique (350.0 has two senders). the
    # signature of the one we want: its enclosing function also allocs the
    # class and sends the geometry request, and is used as a block invoke that
    # gets enqueued -- which is also what makes an early `ret` safe, since a
    # block invoke here returns void.
    starts = img.function_starts()
    matches = []
    for call in calls:
        prev = [a for a in starts if a <= call]
        nxt = [a for a in starts if a > call]
        if not prev or not nxt:
            raise Fail(f'{call:#x} is outside the LC_FUNCTION_STARTS range')
        fn, end = max(prev), min(nxt)
        if not loads_slot(img, fn, end, cls):
            continue
        if not bl_sites(img, stubs[SEL_REQUEST], fn, end):
            continue
        setup = block_literal_setup(img, fn, stubs[SEL_ENQUEUE])
        if len(setup) != 1:
            continue
        matches.append((fn, end, call, setup[0]))
    if len(matches) != 1:
        raise Fail(f'expected exactly 1 geometry-request block, '
                   f'found {len(matches)}'
                   + (': ' + ', '.join(hex(x[0]) for x in matches)
                      if matches else ''))
    fn, end, call, setup = matches[0]
    say(f'   matched call site {call:#x}  (image+{call - img.base:#x})')
    say(f'   enclosing function {fn:#x} .. {end:#x}  '
        f'(image+{fn - img.base:#x}, {end - fn} bytes)')
    say(f'   block literal built at {setup:#x} -> {SEL_ENQUEUE} '
        f'(so the invoke returns void)')
    return fn, end


def neuter(path, verbose=True):
    img = Image(path)
    fn, end = find(img, verbose)
    o = img.off(fn)
    w = struct.unpack_from('<I', img.d, o)[0]
    if w == RET:
        if verbose:
            print(f'   already neutered at image+{fn - img.base:#x}')
        return fn, False
    if not (d_sub_imm(w) or is_store(w)):
        raise Fail(f'{fn:#x} does not start with a prologue '
                   f'(word {w:#010x}); refusing to patch')
    struct.pack_into('<I', img.d, o, RET)
    open(path, 'wb').write(bytes(img.d))
    if verbose:
        print(f'   ret written at image+{fn - img.base:#x} (was {w:#010x}): '
              f'the block now does nothing')
    return fn, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('binary')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.dry_run:
        find(Image(a.binary))
    else:
        neuter(a.binary)


if __name__ == '__main__':
    main()
