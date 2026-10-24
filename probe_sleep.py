"""measure the argument registers of Task.sleep(until:tolerance:clock:).

run to the shim's `brk` for it and dump every candidate register plus, for the
ones that look like pointers, the two 64-bit words behind them. the deadline is
identifiable by magnitude: it is an attosecond count close to the machine's
uptime, so its high word is a small positive number and its low word is large.

  ./dbg.sh probe_sleep.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_cgpath import images

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
CANDIDATES = [0, 1, 2, 3, 4, 5, 6, 7, 19, 20, 21, 22]
ATTOS_PER_NANO = 1_000_000_000


def main():
    g = G(HOST, PORT)
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')
    stop = g.cmd('c')
    if not stop.startswith('T'):
        print('no signal stop:', stop[:120])
        return 1
    r = g.regs(stop)
    pc = r.get(32)
    imgs = [(l, p) for l, p in images(g) if l]
    shim = next((l for l, p in imgs if 'libdiscordshim' in p), None)
    disc = next((l for l, p in imgs if p.endswith('Discord.app/Discord')), None)
    print(f'pc {pc:#x}' + (f'  = shim+{pc - shim:#x}' if shim else ''))
    if disc:
        print(f'lr = Discord image+{r.get(30, 0) - disc:#x}')

    print('\nregisters, and what they point at:')
    for n in CANDIDATES:
        v = r.get(n)
        if v is None:
            continue
        line = f'  x{n:<2} = {v:#018x}'
        if v > 0x100000000:
            m = g.mem(v, 16)
            if len(m) == 16:
                lo = int.from_bytes(m[:8], 'little')
                hi = int.from_bytes(m[8:], 'little')
                line += f'   -> [{lo:#x}, {hi:#x}]'
                if 0 < hi < 1 << 20:
                    attos = (hi << 64) | lo
                    line += (f'  = {attos / ATTOS_PER_NANO / 1e9:.1f}s '
                             f'<-- looks like an Instant')
        print(line)
    print('\n(x22 is the async context: [x22]=parent, [x22+8]=resume)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
