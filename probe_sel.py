"""what selector does the "shared" objc stub actually load at runtime?"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import UIAPPMAIN, images, clsname

ap = argparse.ArgumentParser()
ap.add_argument('--at', default='0x915354')
ap.add_argument('--steps', type=int, default=12)
a = ap.parse_args()

g = G(os.environ['PHONE'], int(os.environ.get('DBG_PORT', '1234')))
g.cmd('qSupported:xmlRegisters=arm'); g.cmd('QStartNoAckMode'); g.noack = True
g.cmd('?')
g.cmd(f'Z0,{UIAPPMAIN:x},4'); g.cmd('c'); g.cmd(f'z0,{UIAPPMAIN:x},4')
imgs = images(g)
base = next(l for l, p in imgs if p.endswith('Discord.app/Discord'))
at = base + int(a.at, 0)
g.cmd(f'Z0,{at:x},4'); stop = g.cmd('c'); g.cmd(f'z0,{at:x},4')
print(f'stopped at image+{a.at}, x0 = {clsname(g, g.regs(stop).get(0))}')
for i in range(a.steps):
    stop = g.cmd('s')
    r = g.regs(stop)
    pc, x1 = r.get(32), r.get(1)
    s = ''
    if x1 and x1 > 0x100000000:
        try:
            s = repr(g.cstr(x1, 64))
        except Exception:
            s = '?'
    print(f'  [{i:2d}] pc {pc:#x} (image+{pc - base:#x}) x1 {x1:#x} {s}')
