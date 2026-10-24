"""at the doesNotRecognizeSelector: breakpoint, read chosen Discord slots.

discord's load address comes from lr at the breakpoint (lr = image+0x915368,
the return address the crash report showed), so no image-list query is needed.
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info

PREF = 0x100000000
CF_BASE = 0x180681000
DNRS = CF_BASE + 0x1635A4
LR_AT_BP = 0x915368


def clsname(g, p):
    if not p:
        return 'nil'
    try:
        nm, sup, iv = class_info(g, p)
        return repr(nm)
    except Exception as e:
        return f'<class_info failed: {e}>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('PHONE'))
    ap.add_argument('--port', type=int, default=1234)
    ap.add_argument('--slots', nargs='*',
                    default=['0x103188a40', '0x103188a38'])
    a = ap.parse_args()

    g = G(a.host, a.port)
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')
    assert g.cmd(f'Z0,{DNRS:x},4') == 'OK'
    stop = g.cmd('c')
    regs = g.regs(stop)
    pc = regs.get(32)
    fp = int.from_bytes(bytes.fromhex(g.cmd('p1d')), 'little')
    lr = int.from_bytes(bytes.fromhex(g.cmd('p1e')), 'little')
    print(f'pc {pc:#x}  lr {lr:#x}  fp {fp:#x}')
    # walk the frame pointers; the shared cache sits above 0x180000000, the
    # app's own image well below it
    base = None
    print('frames:')
    for i in range(16):
        if not fp:
            break
        ra = g.q64(fp + 8)
        nxt = g.q64(fp)
        if ra is None:
            break
        tag = 'cache' if ra > 0x140000000 else 'APP'
        print(f'  [{i}] fp {fp:#x} ra {ra:#x}  {tag}')
        if ra < 0x140000000 and base is None:
            base = ra - LR_AT_BP
        fp = nxt
    if base is None:
        print('no app frame found'); return 1
    print(f'=> discord base {base:#x} (slide {base - PREF:#x})')
    print(f'receiver x0 = {regs.get(0):#x} -> {clsname(g, regs.get(0))}')
    print(f'selector    = {g.cstr(regs.get(2))}')
    print()
    for s in a.slots:
        vm = int(s, 0)
        rt = base + (vm - PREF)
        val = g.q64(rt)
        print(f'slot {vm:#x} (runtime {rt:#x}) = '
              f'{val:#x} -> {clsname(g, val)}' if val is not None
              else f'slot {vm:#x} unreadable')
    return 0


if __name__ == '__main__':
    sys.exit(main())
