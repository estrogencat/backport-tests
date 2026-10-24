"""step through objc_msgSend until the imp runs, and say where the imp lives."""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import UIAPPMAIN, images, clsname

ap = argparse.ArgumentParser()
ap.add_argument('--at', default='0x915354')
ap.add_argument('--steps', type=int, default=4000)
a = ap.parse_args()

g = G(os.environ['PHONE'], int(os.environ.get('DBG_PORT', '1234')))
g.cmd('qSupported:xmlRegisters=arm'); g.cmd('QStartNoAckMode'); g.noack = True
g.cmd('?')
g.cmd(f'Z0,{UIAPPMAIN:x},4'); g.cmd('c'); g.cmd(f'z0,{UIAPPMAIN:x},4')
imgs = images(g)
rng = sorted((l, os.path.basename(p)) for l, p in imgs)

def where(pc):
    best = None
    for l, nm in rng:
        if l <= pc:
            best = (l, nm)
        else:
            break
    return f'{best[1]}+{pc - best[0]:#x}' if best else f'{pc:#x} ?'

base = next(l for l, p in imgs if p.endswith('Discord.app/Discord'))
shim = next((l for l, p in imgs if 'libdiscordshim' in p), None)
at = base + int(a.at, 0)
g.cmd(f'Z0,{at:x},4'); stop = g.cmd('c'); g.cmd(f'z0,{at:x},4')
print(f'from image+{a.at}; discord {base:#x}, shim {shim and hex(shim)}')
last = None
for i in range(a.steps):
    stop = g.cmd('s')
    r = g.regs(stop)
    pc = r.get(32)
    if pc is None:
        break
    w = where(pc)
    img = w.split('+')[0]
    if img != last:
        print(f'  [{i:4d}] {w:34s} x0={r.get(0,0):#x} x17={r.get(17,0):#x}')
        last = img
    if pc in (at + 4, at + 8):
        print(f'  back at {w} after {i} steps; x0 = {clsname(g, r.get(0))}')
        break
