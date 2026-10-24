"""read an objc class at a vmaddr: its name, superclass and method list.

run with `python -E -P` (it reuses callsite.Image, which needs capstone).
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from callsite import Image


def cls_info(img, vm):
    d = img.read(vm, 40)
    if d is None:
        return None
    isa, sup, cache, cache2, bits = struct.unpack('<5Q', d)
    ro = img.chained_target(vm + 32)
    swift = None
    if ro is not None:
        swift = ro & 0x3          # FAST_IS_SWIFT_LEGACY/STABLE
        ro &= ~0x7
    out = {'isa_slot': vm, 'ro': ro, 'swift': swift}
    if ro is None:
        return out
    r = img.read(ro, 72)
    flags, istart, isize, _res = struct.unpack_from('<4I', r, 0)
    name_p = img.chained_target(ro + 24)
    meth_p = img.chained_target(ro + 32)
    out['flags'] = flags
    out['name'] = img.cstr(name_p) if name_p else None
    out['methods'] = []
    if meth_p:
        hdr = img.read(meth_p, 8)
        if hdr is None:
            out['methods'] = ['<method list at %#x not mapped>' % meth_p]
            return out
        ent, count = struct.unpack('<II', hdr)
        size = ent & 0xffff
        small = bool(ent & 0x80000000)
        for i in range(min(count, 400)):
            e = meth_p + 8 + i * size
            if small:
                rel = struct.unpack('<i', img.read(e, 4))[0]
                # small method list: name field is a relative pointer to a
                # selref, which then points at the string
                selref = e + rel
                t = img.chained_target(selref)
                nm = img.cstr(t) if t else None
                if nm is None:
                    nm = img.cstr(selref)
            else:
                np_ = img.chained_target(e)
                nm = img.cstr(np_) if np_ else None
            out['methods'].append(nm)
    sup_t = img.chained_target(vm + 8)
    out['superclass'] = sup_t
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('binary')
    ap.add_argument('addrs', nargs='+')
    ap.add_argument('--grep', help='only print methods containing this')
    a = ap.parse_args()
    img = Image(a.binary)
    for s in a.addrs:
        vm = int(s, 0)
        i = cls_info(img, vm)
        if i is None:
            print(f'{vm:#x}: not mapped')
            continue
        print(f'{vm:#x}  name={i.get("name")!r} swift={i.get("swift")} '
              f'flags={i.get("flags")} '
              f'ro={i.get("ro") and hex(i["ro"])} '
              f'super={i.get("superclass") and hex(i["superclass"])} '
              f'methods={len(i.get("methods", []))}')
        ms = i.get('methods', [])
        if a.grep:
            ms = [m for m in ms if m and a.grep in m]
        for m in ms[:60]:
            print(f'    {m}')


if __name__ == '__main__':
    main()
