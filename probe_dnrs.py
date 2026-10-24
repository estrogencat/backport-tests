"""catch the unrecognized selector on the device and name receiver + selector.

breaks on CoreFoundation's doesNotRecognizeSelector: (its address comes from
the crash report's image base, which is stable while the phone stays up), then
reads x0 (receiver) and x2 (the selector that was not recognized).

any other stop -- a SIGTRAP from one of the shim's brk stubs, say -- is
reported with its pc so it can be looked up in libdiscordshim.dylib.map.
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info

CF_BASE = 0x180681000
DNRS = CF_BASE + 0x1635A4        # +[NSObject doesNotRecognizeSelector:]


def stop_regs(g, stop):
    r = g.regs(stop)
    return r


def describe(g, obj):
    if not obj:
        return 'nil'
    isa = g.q64(obj)
    if isa is None:
        return f'{obj:#x} (unreadable)'
    isa &= 0xffffffff8
    try:
        name, sup, ivars = class_info(g, isa)
    except Exception as e:
        return f'{obj:#x} isa {isa:#x} (class_info failed: {e})'
    return f'{obj:#x} isa {isa:#x} class {name!r}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('PHONE'))
    ap.add_argument('--port', type=int, default=1234)
    ap.add_argument('--cf-base', default=hex(CF_BASE))
    ap.add_argument('--shim-base', default=None,
                    help='libdiscordshim.dylib load address, if known')
    a = ap.parse_args()

    dnrs = int(a.cf_base, 0) + 0x1635A4
    g = G(a.host, a.port)
    print('connected:', g.cmd('qSupported:xmlRegisters=arm')[:60])
    g.cmd('QStartNoAckMode')
    g.noack = True
    stop = g.cmd('?')
    print('initial stop:', stop[:120])

    r = g.cmd(f'Z0,{dnrs:x},4')
    print(f'breakpoint at {dnrs:#x} -> {r!r}')
    if r != 'OK':
        print('could not set breakpoint; is the cf base still valid?')
        return 1

    print('continuing...')
    stop = g.cmd('c')
    print('stop:', stop[:200])
    regs = stop_regs(g, stop)
    pc = regs.get(32)
    print(f'pc = {pc:#x}' if pc else 'no pc in stop packet')

    if pc and abs(pc - dnrs) < 0x40:
        x0 = regs.get(0)
        x1 = regs.get(1)
        x2 = regs.get(2)
        print('\n--- unrecognized selector')
        print('receiver :', describe(g, x0))
        print('_cmd     :', g.cstr(x1) if x1 else '?')
        print('selector :', g.cstr(x2) if x2 else '?')
        # is the receiver itself a class object?
        if x0:
            try:
                nm, sup, iv = class_info(g, x0)
                print(f'receiver as class: {nm!r} (so this was a class method)')
            except Exception:
                pass
    else:
        print('\nstopped somewhere else; registers:')
        for i in sorted(regs):
            print(f'  x{i} = {regs[i]:#x}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
