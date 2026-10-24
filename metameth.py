"""read a class's metaclass method list from the file and give each imp."""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from callsite import Image

def ro_of(img, cls):
    ro = img.chained_target(cls + 32)
    return (ro & ~0x7) if ro is not None else None

def methods(img, cls):
    ro = ro_of(img, cls)
    if ro is None:
        return []
    ml = img.chained_target(ro + 32)
    if not ml:
        return []
    hdr = img.read(ml, 8)
    if hdr is None:
        return []
    ent, count = struct.unpack('<II', hdr)
    size, small = ent & 0xffff, bool(ent & 0x80000000)
    out = []
    for i in range(min(count, 500)):
        e = ml + 8 + i * size
        if small:
            nrel, trel, irel = struct.unpack('<3i', img.read(e, 12))
            selref = e + nrel
            t = img.chained_target(selref)
            name = img.cstr(t) if t else img.cstr(selref)
            imp = (e + 8) + irel
        else:
            np_ = img.chained_target(e)
            name = img.cstr(np_) if np_ else None
            imp = img.chained_target(e + 16)
        out.append((name, imp))
    return out

ap = argparse.ArgumentParser()
ap.add_argument('binary')
ap.add_argument('cls')
ap.add_argument('--grep')
a = ap.parse_args()
img = Image(a.binary)
cls = int(a.cls, 0)
meta = img.chained_target(cls)
print(f'class {cls:#x}  metaclass {meta and hex(meta)}')
for label, c in (('instance', cls), ('class', meta)):
    if c is None:
        continue
    print(f'-- {label} methods')
    for nm, imp in methods(img, c):
        if a.grep and (nm is None or a.grep not in nm):
            continue
        print(f'   {nm:40s} imp {imp:#x}' if imp else f'   {nm} imp ?')
