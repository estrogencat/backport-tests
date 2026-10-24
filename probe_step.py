"""single-step into a call and report which image / imp actually runs."""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info
from probe_trace import UIAPPMAIN, images, PREF, clsname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('PHONE'))
    ap.add_argument('--port', type=int, default=1234)
    ap.add_argument('--at', default='0x915354', help='image offset to step from')
    ap.add_argument('--steps', type=int, default=400)
    a = ap.parse_args()

    g = G(a.host, a.port)
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')
    assert g.cmd(f'Z0,{UIAPPMAIN:x},4') == 'OK'
    g.cmd('c')
    g.cmd(f'z0,{UIAPPMAIN:x},4')

    imgs = images(g)
    ranges = []
    for load, path in imgs:
        ranges.append((load, os.path.basename(path), path))
    ranges.sort()

    def where(pc):
        best = None
        for load, nm, path in ranges:
            if load <= pc:
                best = (load, nm)
            else:
                break
        if best is None:
            return f'{pc:#x} ?'
        return f'{best[1]}+{pc - best[0]:#x}'

    base = next(l for l, p in imgs if p.endswith('Discord.app/Discord'))
    at = base + int(a.at, 0)
    print(f'discord base {base:#x}; stepping from image+{a.at}\n')
    assert g.cmd(f'Z0,{at:x},4') == 'OK'
    stop = g.cmd('c')
    pc = g.regs(stop).get(32)
    print(f'at {where(pc)}  x0 = {clsname(g, g.regs(stop).get(0))}')
    g.cmd(f'z0,{at:x},4')

    last_img = None
    for i in range(a.steps):
        stop = g.cmd('s')
        regs = g.regs(stop)
        pc = regs.get(32)
        if pc is None:
            print('no pc'); break
        w = where(pc)
        img = w.split('+')[0]
        if img != last_img:
            print(f'  [{i:3d}] -> {w:40s} x0={regs.get(0, 0):#x} '
                  f'x1={regs.get(1, 0):#x} x16={regs.get(16, 0):#x}')
            last_img = img
        if pc == at + 4 or pc == at + 8:
            print(f'  returned to {where(pc)} after {i} steps')
            print(f'  x0 = {clsname(g, regs.get(0))}')
            break
    return 0


if __name__ == '__main__':
    sys.exit(main())
