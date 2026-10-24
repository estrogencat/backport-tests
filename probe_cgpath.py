"""measure the register abi of CGPathRef.subtracting(_:using:) on the device.

run to the shim's `brk` for that symbol -- no hardcoded shared-cache address
is needed, the trap itself is the breakpoint -- and report what each candidate
register actually holds. a wrong register abi in a stub does not crash, it
silently returns the wrong object, so this is measured rather than inferred.

  ./dbg.sh probe_cgpath.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
CANDIDATES = [0, 1, 2, 3, 19, 20, 21, 22]


def images(g):
    out = []
    r = g.cmd('jGetLoadedDynamicLibrariesInfos:{"fetch_all_solibs":true}')
    import json
    try:
        d = json.loads(G.unesc(r))
    except Exception:
        return out
    for im in d.get('images', []):
        out.append((im.get('load_address'), im.get('pathname') or ''))
    return out


def describe(g, v):
    """what does this register value look like"""
    if v is None:
        return 'None'
    if v < 0x1000:
        return f'{v:#x}  (small integer -- an enum value or a count)'
    blob = g.mem(v, 16)
    if len(blob) < 16:
        return f'{v:#x}  (unreadable)'
    lo = int.from_bytes(blob[:8], 'little')
    note = ''
    try:
        isa = lo & 0xffffffff8
        if isa and isa > 0x100000000:
            nm, _sup, _iv = class_info(g, isa)
            note = f'  isa -> {nm}'
    except Exception:
        pass
    return (f'{v:#x}  first words {lo:#x} '
            f'{int.from_bytes(blob[8:16], "little"):#x}{note}')


def main():
    g = G(HOST, PORT)
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    print('initial stop:', g.cmd('?')[:60])

    stop = g.cmd('c')
    if not stop.startswith('T'):
        print('process did not stop with a signal:', stop[:120])
        return 1
    r = g.regs(stop)
    pc = r.get(32)
    print(f'\nstopped at pc {pc:#x}  ({stop[:12]}...)')

    imgs = [(l, p) for l, p in images(g) if l]
    shim = next((l for l, p in imgs if 'libdiscordshim' in p), None)
    disc = next((l for l, p in imgs if p.endswith('Discord.app/Discord')),
                None)
    if shim:
        print(f'shim   base {shim:#x}  -> trap at shim+{pc - shim:#x}')
    if disc:
        print(f'discord base {disc:#x}  lr = image+{r.get(30, 0) - disc:#x}')

    print('\nregisters at the trap:')
    for n in CANDIDATES:
        print(f'  x{n:<2} = {describe(g, r.get(n))}')
    print(f'  lr  = {r.get(30, 0):#x}')
    print(f'  sp  = {r.get(31, 0):#x}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
