"""walk DYLD_CHAINED_FIXUPS and say what each pointer slot resolves to.

  chains.py <binary> --slot 0x102e61ba8
  chains.py <binary> --name _objc_alloc
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix

START_NONE = 0xFFFF


def walk(m):
    """yield (slot_vmaddr, kind, payload) for every chained fixup slot."""
    f = cfix.Fixups(m)
    segs = list(m.segs().items())
    base = min(v['vmaddr'] for v in m.segs().values() if v['filesize'])
    si = f.base + f.starts_off
    seg_count = struct.unpack_from('<I', m.d, si)[0]
    offs = struct.unpack_from(f'<{seg_count}I', m.d, si + 4)
    for sidx, so in enumerate(offs):
        if so == 0:
            continue
        p = si + so
        size, page_size, ptr_fmt = struct.unpack_from('<IHH', m.d, p)
        seg_off, max_valid, page_count = struct.unpack_from('<QIH', m.d, p + 8)
        starts = struct.unpack_from(f'<{page_count}H', m.d, p + 22)
        for pi, st in enumerate(starts):
            if st == START_NONE:
                continue
            cur = seg_off + pi * page_size + st
            while True:
                raw = struct.unpack_from('<Q', m.d, cur)[0]
                bind = (raw >> 63) & 1
                nxt = (raw >> 51) & 0xFFF
                if bind:
                    if ptr_fmt in (2, 6):          # arm64e variants
                        ordinal = raw & 0xFFFF
                    else:
                        ordinal = raw & 0xFFFFFF
                    yield base + cur, 'bind', ordinal
                else:
                    tgt = raw & 0xFFFFFFFFF
                    yield base + cur, 'rebase', tgt
                if nxt == 0:
                    break
                cur += nxt * 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('binary')
    ap.add_argument('--slot')
    ap.add_argument('--name')
    ap.add_argument('--limit', type=int, default=40)
    a = ap.parse_args()
    raw = open(a.binary, 'rb').read()
    m = mo.MachO(raw)
    f = cfix.Fixups(m)
    libs = {i: n for i, c, n in m.dylibs()}
    imports = [f.get(i) for i in range(f.imports_count)]

    def desc(ordinal):
        if ordinal >= len(imports):
            return f'<ordinal index {ordinal} out of range>'
        lib, weak, noff, name = imports[ordinal]
        return (f'{name}  <- ordinal {lib} '
                f'{libs.get(lib, "special/"+hex(lib))}'
                f'{" (weak)" if weak else ""}')

    want = int(a.slot, 0) if a.slot else None
    n = 0
    for slot, kind, payload in walk(m):
        if want is not None and slot != want:
            continue
        if a.name:
            if kind != 'bind':
                continue
            if a.name not in imports[payload][3]:
                continue
        if kind == 'bind':
            print(f'{slot:#x}  bind   {desc(payload)}')
        else:
            print(f'{slot:#x}  rebase -> {payload:#x}')
        n += 1
        if n >= a.limit:
            break
    if n == 0:
        print('no matching slot')


if __name__ == '__main__':
    main()
