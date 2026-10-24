"""watch x0/x19/x20 across the two shim release stubs at the end of init."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import UIAPPMAIN, images, clsname

OFFS = [(0xcd441c, 'before _objc_release_x19'),
        (0xcd4420, 'after  _objc_release_x19 / before _objc_release_x20'),
        (0xcd4424, 'after  _objc_release_x20  (mov x0, x19 next)'),
        (0xcd4438, 'ret')]

g = G(os.environ['PHONE'], int(os.environ.get('DBG_PORT', '1234')))
g.cmd('qSupported:xmlRegisters=arm'); g.cmd('QStartNoAckMode'); g.noack = True
g.cmd('?')
g.cmd(f'Z0,{UIAPPMAIN:x},4'); g.cmd('c'); g.cmd(f'z0,{UIAPPMAIN:x},4')
imgs = images(g)
base = next(l for l, p in imgs if p.endswith('Discord.app/Discord'))
shim = next((l for l, p in imgs if 'libdiscordshim' in p), None)
print(f'discord {base:#x}  shim {shim:#x}\n')
for off, _ in OFFS:
    g.cmd(f'Z0,{base + off:x},4')
pc = None
first = True
for _ in range(8):
    if first:
        stop = g.cmd('c'); first = False
    else:
        g.cmd(f'z0,{pc:x},4'); g.cmd('s'); g.cmd(f'Z0,{pc:x},4')
        stop = g.cmd('c')
    if not stop.startswith('T'):
        print('stopped:', stop[:80]); break
    r = g.regs(stop); pc = r.get(32); off = pc - base
    d = dict(OFFS).get(off)
    if d is None:
        print(f'unexpected {pc:#x}'); break
    print(f'image+{off:#x}  {d}')
    for reg in (0, 19, 20, 21):
        print(f'    x{reg:<2} = {clsname(g, r.get(reg))}')
    if off == 0xcd4438:
        break
