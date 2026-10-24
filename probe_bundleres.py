"""watch the bundled-icon lookup: what discord asks NSBundle for, and whether
it gets anything back.

the ota asset resolver at Discord image+0xe1dc.. checks the downloaded ota
directory first and, on a miss, falls back to

    [[NSBundle mainBundle] URLForResource:name withExtension:ext
                                                subdirectory:subdir]

which is the objc stub at image+0x258de00. this breaks there, dumps the three
string arguments and then reports the URL that comes back.

  ./dbg.sh probe_bundleres.py
"""
import sys, os, struct, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import images

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
STUB = int(os.environ.get('STUB', '0x258de00'), 0)   # image offset of the stub
HITS = int(os.environ.get('HITS', '6'))
PRINTABLE = re.compile(rb'[ -~]{4,}')


def strings(g, p, n=96):
    """swift's bridged NSString keeps its utf8 bytes inline, so just look for
    the printable run in the object rather than decoding the representation."""
    if not p or p < 0x1000:
        return repr(p)
    m = g.mem(p, n)
    if not m:
        return f'{p:#x} (unreadable)'
    hits = PRINTABLE.findall(m)
    if not hits:
        return f'{p:#x} ' + m[:32].hex()
    return f'{p:#x} ' + ' | '.join(h.decode() for h in hits[:3])


def main():
    g = G(HOST, PORT)
    g.s.settimeout(float(os.environ.get('WAIT', '240')))
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')

    imgs = images(g) or []
    base = next((l for l, p in imgs if p.endswith('Discord.app/Discord')), None)
    if base is None:
        print('discord image not found')
        return 1
    bp = base + STUB
    print(f'discord at {base:#x}; break on '
          f'URLForResource:withExtension:subdirectory: at {bp:#x}')
    g.cmd(f'Z0,{bp:x},4')

    for _ in range(HITS):
        stop = g.cmd('c')
        if not stop.startswith('T'):
            print('process stop:', stop[:160])
            return 1
        r = g.regs(stop)
        if r.get(32) != bp:
            print(f'stopped elsewhere: {r.get(32):#x}')
            return 1
        lr = r.get(30)
        print(f'\ncalled from Discord+{lr - base:#x}')
        print(f'  resource     = {strings(g, r.get(2))}')
        print(f'  extension    = {strings(g, r.get(3))}')
        print(f'  subdirectory = {strings(g, r.get(4))}')

        g.cmd(f'z0,{bp:x},4')
        g.cmd(f'Z0,{lr:x},4')
        stop2 = g.cmd('c')
        g.cmd(f'z0,{lr:x},4')
        g.cmd(f'Z0,{bp:x},4')
        if not stop2.startswith('T'):
            print('  (lost the process waiting for the return)')
            return 1
        r2 = g.regs(stop2)
        if r2.get(32) != lr:
            print(f'  (stopped at {r2.get(32):#x}, not the return)')
            continue
        ret = r2.get(0)
        print('  -> ' + ('nil   <-- the icon is never found'
                         if not ret else strings(g, ret, 160)))
    g.cmd(f'z0,{bp:x},4')
    return 0


if __name__ == '__main__':
    sys.exit(main())
