"""trace x0 through the crashing call sequence in discord's app delegate.

breaks in UIApplicationMain (a stable shared-cache address while the phone
stays up), reads dyld's image list to learn discord's load address, then puts
breakpoints on the individual instructions and reports x0 at each.
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gdbr import G, class_info

UIKIT_BASE = 0x1829bd000
UIAPPMAIN = UIKIT_BASE + 0x27EB20
PREF = 0x100000000

# image offset -> what the instruction there does
WATCH_INIT = [
    (0xcd4264, 'DCDFrameRateTracker once-fn entry'),
    (0xcd4270, 'after metadata accessor      (x0 = metadata/class)'),
    (0xcd4274, 'after objc_allocWithZone     (x0 = fresh instance)'),
    (0xcd4278, 'after [x init]               (x0 = stored singleton)'),
    (0xcd427c, 'str x0, [global]'),
]

WATCH = [
    (0x9152e8, 'bl objc_stubs "defaultCenter"   (x0 = receiver)'),
    (0x915314, 'bl objc_stubs "addObserver:selector:name:object:"'),
    (0x915320, 'bl objc_stubs "ttiAppStartLifecycleTracker"'),
    (0x915330, 'bl objc_stubs "markAppOpened"'),
    (0x915340, 'bl objc_stubs "shared"  on DCDMetricKitCollector'),
    (0x915348, 'bl retainAutoreleasedReturnValue (x0 = returned obj)'),
    (0x915350, 'ldr x0, [classref DCDFrameRateTracker]'),
    (0x915354, 'bl objc_stubs "shared"  on DCDFrameRateTracker'),
    (0x91535c, 'bl retainAutoreleasedReturnValue (x0 = returned obj)'),
    (0x915360, 'mov x21, x0'),
    (0x915364, 'bl objc_stubs "startHangTracking"  <-- crashes'),
]


def clsname(g, p):
    if not p:
        return 'nil'
    try:
        isa = g.q64(p)
        if isa is None:
            return f'{p:#x} (unreadable)'
        nm, sup, iv = class_info(g, isa & 0xffffffff8)
        return f'{p:#x} instance-of {nm!r}'
    except Exception:
        pass
    try:
        nm, sup, iv = class_info(g, p)
        return f'{p:#x} class {nm!r}'
    except Exception as e:
        return f'{p:#x} (not an object: {e})'


def images(g):
    addr = g.cmd('qShlibInfoAddr')
    if not addr or addr.startswith('E'):
        return None
    aii = int(addr, 16)
    hdr = g.mem(aii, 16)
    ver, count = struct.unpack_from('<II', hdr, 0)
    arr = struct.unpack_from('<Q', hdr, 8)[0]
    out = []
    for i in range(min(count, 600)):
        e = g.mem(arr + 24 * i, 24)
        if len(e) < 24:
            break
        load, pathp, _m = struct.unpack('<3Q', e)
        out.append((load, g.cstr(pathp, 256)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('PHONE'))
    ap.add_argument('--port', type=int, default=1234)
    a = ap.parse_args()

    g = G(a.host, a.port)
    g.cmd('qSupported:xmlRegisters=arm')
    g.cmd('QStartNoAckMode')
    g.noack = True
    g.cmd('?')

    print(f'break in UIApplicationMain at {UIAPPMAIN:#x}')
    assert g.cmd(f'Z0,{UIAPPMAIN:x},4') == 'OK', 'could not set bp'
    stop = g.cmd('c')
    pc = g.regs(stop).get(32)
    print(f'stopped at {pc:#x}')
    g.cmd(f'z0,{UIAPPMAIN:x},4')

    imgs = images(g)
    if imgs is None:
        print('no image list from debugserver')
        return 1
    base = None
    for load, path in imgs:
        if path.endswith('Discord.app/Discord'):
            base = load
        if 'libdiscordshim' in path:
            print(f'shim loaded at {load:#x}  {path}')
    if base is None:
        print('discord image not found in the list')
        for load, path in imgs[:10]:
            print(f'  {load:#x} {path}')
        return 1
    print(f'discord base {base:#x} (slide {base - PREF:#x})\n')

    watch = WATCH_INIT if os.environ.get('WATCH_INIT') else WATCH
    for off, _d in watch:
        g.cmd(f'Z0,{base + off:x},4')

    seen = 0
    first = True
    while seen < 40:
        if first:
            stop = g.cmd('c')
            first = False
        else:
            # step off the breakpoint we are parked on, then continue
            g.cmd(f'z0,{pc:x},4')
            g.cmd('s')
            g.cmd(f'Z0,{pc:x},4')
            stop = g.cmd('c')
        if not stop.startswith('T'):
            print('process stop:', stop[:120])
            break
        regs = g.regs(stop)
        pc = regs.get(32)
        off = (pc or 0) - base
        desc = dict(watch).get(off)
        if desc is None:
            print(f'stopped at unexpected {pc:#x} (image+{off:#x})')
            break
        x0 = regs.get(0)
        print(f'image+{off:#07x}  {desc}')
        print(f'              x0 = {clsname(g, x0)}')
        seen += 1
        if off in (0x915364, 0xcd427c):
            break
    return 0


if __name__ == '__main__':
    sys.exit(main())
