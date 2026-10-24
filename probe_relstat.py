"""find who stats the icon files with a bare relative path.

every bundled icon is looked up as `AccessibilityIcon@2x.png` rather than as a
path under the app bundle, so the stat fails with ENOENT and nothing is drawn.
this breaks on stat64, ignores every absolute path, and for the first relative
one walks the frame-pointer chain to name the caller.

  ./dbg.sh probe_relstat.py          (go to settings on the phone)
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import images

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
STAT64 = 0x12c8                  # offset into libsystem_kernel.dylib, 15.7.7
WANT = os.environ.get('WANT', 'Icon@')
REL = os.environ.get('ABS') is None     # by default only relative paths
MAX_FRAMES = 24


def where(imgs, a):
    best = None
    for load, path in imgs:
        if load <= a and (best is None or load > best[0]):
            best = (load, path)
    if best is None:
        return f'{a:#x}'
    return f'{os.path.basename(best[1])}+{a - best[0]:#x}'


def main():
    g = G(HOST, PORT)
    g.s.settimeout(float(os.environ.get('WAIT', '240')))
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')

    imgs = images(g) or []
    k = next((l for l, p in imgs if p.endswith('libsystem_kernel.dylib')), None)
    if k is None:
        print('libsystem_kernel not in the image list')
        return 1
    bp = k + STAT64
    print(f'break on stat64 at {bp:#x}; waiting for a {'relative ' if REL else ''}path containing {WANT!r}')
    g.cmd(f'Z0,{bp:x},4')

    seen = 0
    while True:
        stop = g.cmd('c')
        if not stop.startswith('T'):
            print('process stop:', stop[:160])
            return 1
        r = g.regs(stop)
        if r.get(32) != bp:
            print(f'stopped elsewhere: {r.get(32):#x}')
            return 1
        path = g.cstr(r.get(0), 256)
        seen += 1
        if WANT not in path or (REL and path.startswith('/')):
            g.cmd(f'z0,{bp:x},4')
            g.cmd('s')
            g.cmd(f'Z0,{bp:x},4')
            continue

        print(f'\nstat64({path!r})   after {seen} stats')
        print(f'  lr = {where(imgs, r.get(30))}')
        fp = r.get(29)
        for i in range(MAX_FRAMES):
            m = g.mem(fp, 16)
            if len(m) < 16:
                break
            nfp, lr = struct.unpack('<QQ', m)
            if not lr or not nfp or nfp <= fp:
                break
            print(f'  #{i:<2} {where(imgs, lr)}')
            fp = nfp
        break

    g.cmd(f'z0,{bp:x},4')
    return 0


if __name__ == '__main__':
    sys.exit(main())
