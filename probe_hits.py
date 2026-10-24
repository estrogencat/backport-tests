"""count hits on discord functions, and what each one returns.

  OFFS=0xd00170,0xd00674 ./dbg.sh probe_hits.py

each offset is an image offset of a function entry. on a hit it prints the
caller and then runs to the return to print x0, so a `canLoad...:` style
predicate shows its answer.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G
from probe_trace import images
from probe_bundleres import strings

HOST = os.environ['PHONE']
PORT = int(os.environ.get('DBG_PORT', '1234'))
OFFS = [int(x, 0) for x in os.environ.get('OFFS', '').split(',') if x]
HITS = int(os.environ.get('HITS', '12'))


def main():
    g = G(HOST, PORT)
    g.s.settimeout(float(os.environ.get('WAIT', '240')))
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')
    imgs = images(g) or []
    base = next((l for l, p in imgs if p.endswith('Discord.app/Discord')), None)
    bps = {base + o: o for o in OFFS}
    for a in bps:
        g.cmd(f'Z0,{a:x},4')
    print(f'discord at {base:#x}; watching ' +
          ', '.join(f'{o:#x}' for o in OFFS))

    for _ in range(HITS):
        stop = g.cmd('c')
        if not stop.startswith('T'):
            print('process stop:', stop[:160])
            return 1
        r = g.regs(stop)
        pc, lr = r.get(32), r.get(30)
        if pc not in bps:
            print(f'stopped elsewhere {pc:#x}')
            return 1
        print(f'\nimage+{bps[pc]:#x}  from image+{lr - base:#x}  '
              f'x2 = {strings(g, r.get(2))}')
        for rn in [int(x) for x in os.environ.get('SHOW', '').split(',') if x]:
            print(f'  x{rn} = {r.get(rn, 0):#x}')
        reg = int(os.environ.get('REG', '2'))
        if os.environ.get('DEREF') and r.get(reg):
            # follow the pointers inside the object, e.g. an NSURL's string
            raw = g.mem(r.get(reg), 48) or b''
            for k in range(8, len(raw) - 7, 8):
                q = int.from_bytes(raw[k:k + 8], 'little')
                if 0x100000000 < q < 0x400000000:
                    print(f'  [+{k:#x}] -> {strings(g, q, 200)}')
        if os.environ.get('NORET'):
            g.cmd(f'z0,{pc:x},4'); g.cmd('s'); g.cmd(f'Z0,{pc:x},4')
            continue
        g.cmd(f'z0,{pc:x},4')
        g.cmd(f'Z0,{lr:x},4')
        s2 = g.cmd('c')
        g.cmd(f'z0,{lr:x},4')
        g.cmd(f'Z0,{pc:x},4')
        r2 = g.regs(s2) if s2.startswith('T') else {}
        if r2.get(32) == lr:
            print(f'  returned x0 = {strings(g, r2.get(0), 200)}')
        else:
            print(f'  (did not stop at the return: {s2[:60]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
