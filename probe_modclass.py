"""which class does react native pick for a module name, and where is it from.

breaks on -[RCTTurboModuleManager _getModuleClassFromName:], waits for the
name in $NAME (default ImageLoader), runs to the return and names the class
and the image that defines it.

  NAME=ImageLoader ./dbg.sh probe_modclass.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info
from probe_trace import images
from probe_bundleres import strings

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
GET_CLASS = 0x7ece34          # Discord image offset, 350.0
NAME = os.environ.get('NAME', 'ImageLoader')


def main():
    g = G(HOST, PORT)
    g.s.settimeout(float(os.environ.get('WAIT', '120')))
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')
    imgs = images(g) or []
    base = next(l for l, p in imgs if p.endswith('Discord.app/Discord'))
    bp = base + GET_CLASS
    g.cmd(f'Z0,{bp:x},4')
    while True:
        stop = g.cmd('c')
        r = g.regs(stop)
        if r.get(32) == bp and NAME in strings(g, r.get(2)).split(' ', 1)[-1]:
            break
        g.cmd(f'z0,{bp:x},4'); g.cmd('s'); g.cmd(f'Z0,{bp:x},4')
    lr = r.get(30)
    g.cmd(f'z0,{bp:x},4')
    g.cmd(f'Z0,{lr:x},4')
    r2 = g.regs(g.cmd('c'))
    cls = r2.get(0)
    imgs = images(g) or imgs
    owner = max(((l, p) for l, p in imgs if l <= cls), default=(0, '?'))
    try:
        nm, sup, _iv = class_info(g, cls)
    except Exception as e:
        nm, sup = f'? ({e})', None
    print(f'{NAME!r} -> class {cls:#x} named {nm!r}')
    print(f'  nearest image below: {owner[1]}  (+{cls - owner[0]:#x})')
    for l, p in sorted(imgs):
        if cls - 0x4000000 < l < cls + 0x1000000:
            print(f'    {l:#x}  {p}')
    bits = g.q64(cls + 0x20) & 0xffffffff8
    ro = g.q64(bits + 8)
    if ro & 1:
        ro = g.q64(ro & ~7)
    ro &= 0xffffffff8
    namep = g.q64(ro + 0x18)
    meths = g.q64(ro + 0x20)
    def own(a):
        o = max(((l, p) for l, p in imgs if l <= a), default=(0, '?'))
        return f'{a:#x} ({os.path.basename(o[1])}+{a - o[0]:#x})'
    print(f'  rw {bits:#x}  ro {own(ro)}')
    print(f'  name string at {own(namep)}')
    print(f'  method list at {own(meths) if meths else 0}')
    print(f'  isa (metaclass) {own(g.q64(cls))}')
    # walk back page by page to the mach-o header that maps this class
    a = cls & ~0x3fff
    for _ in range(4096):
        if g.mem(a, 4) == bytes.fromhex('cffaedfe'):
            break
        a -= 0x4000
    hdr = g.mem(a, 32)
    import struct as _s
    _m, _c, _st, ft, ncmds, sz, _f, _r = _s.unpack('<8I', hdr)
    print(f'  mach-o header at {a:#x}  filetype {ft}  ncmds {ncmds}')
    lc = g.mem(a + 32, min(sz, 0x4000))
    off = 0
    for _ in range(ncmds):
        cmd, csz = _s.unpack_from('<II', lc, off)
        if cmd in (0xd, 0xc, 0x80000018):   # ID_DYLIB, LOAD_DYLIB, LOAD_WEAK
            so = _s.unpack_from('<I', lc, off + 8)[0]
            nm_ = lc[off + so:off + csz].split(bytes(1))[0].decode('latin1')
            print(f'    {"ID" if cmd == 0xd else "loads"} {nm_}')
        off += csz
    if sup:
        try:
            print(f'  superclass {class_info(g, sup)[0]!r}')
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
