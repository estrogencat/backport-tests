"""watch the image decode path: is ImageIO ever asked, and what does it say.

downloads clearly work (SDWebImage's on-disk cache fills up), so if images
still do not appear the failure is after the bytes arrive. this breaks on
ImageIO's two entry points, reports the caller and, for each call, the value
returned.

  ./dbg.sh probe_decode.py

the ImageIO offsets come from the 15.7.7 cache, not from a crash report, so
they survive a reboot; the runtime base is read from the process.
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import images

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
MAX_HITS = int(os.environ.get('HITS', '12'))

# offsets into ImageIO in the 15.7.7 shared cache
WATCH = {
    0x55e0: 'CGImageSourceCreateWithData',
    0x57f28: 'CGImageSourceCreateImageAtIndex',
    0xd2c0: 'CGImageSourceCreateThumbnailAtIndex',
}


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
    g.s.settimeout(float(os.environ.get('WAIT', '180')))
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')

    imgs = images(g) or []
    iio = next((l for l, p in imgs if p.endswith('.framework/ImageIO')), None)
    if iio is None:
        print('ImageIO not loaded yet; images seen:', len(imgs))
        return 1
    print(f'ImageIO at {iio:#x}')

    bps = {iio + o: n for o, n in WATCH.items()}
    for a in bps:
        g.cmd(f'Z0,{a:x},4')

    hits = 0
    pc = None
    while hits < MAX_HITS:
        if pc is not None:
            g.cmd(f'z0,{pc:x},4')
            g.cmd('s')
            g.cmd(f'Z0,{pc:x},4')
        stop = g.cmd('c')
        if not stop.startswith('T'):
            print('process stop:', stop[:160])
            break
        r = g.regs(stop)
        pc = r.get(32)
        nm = bps.get(pc)
        if nm is None:
            print(f'stopped somewhere else: {pc:#x} = {where(imgs, pc)}')
            break
        lr = r.get(30)
        print(f'\n{nm}  called from {where(imgs, lr)}')
        hits += 1

        # run to the return and read x0
        g.cmd(f'z0,{pc:x},4')
        g.cmd(f'Z0,{lr:x},4')
        stop2 = g.cmd('c')
        g.cmd(f'z0,{lr:x},4')
        g.cmd(f'Z0,{pc:x},4')
        if not stop2.startswith('T'):
            print('  (lost the process waiting for the return)')
            break
        r2 = g.regs(stop2)
        if r2.get(32) != lr:
            print(f'  (stopped at {r2.get(32):#x}, not the return address)')
            pc = r2.get(32)
            continue
        ret = r2.get(0)
        print(f'  -> {ret:#x}' + ('   <-- NULL, the decode failed'
                                  if not ret else ''))
        pc = lr
        pc = None            # the bp at lr is gone; nothing to step off
    print(f'\n{hits} calls seen')
    return 0


if __name__ == '__main__':
    sys.exit(main())
