"""structural self-check of a generated shim dylib."""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix, dsc

LC_DYLD_INFO_ONLY = 0x80000022


def main():
    path, want = sys.argv[1], sys.argv[2]
    d = open(path, 'rb').read()
    m = mo.MachO(d)
    segs = m.segs()
    print(f'file {len(d)} bytes, ncmds {m.ncmds}, sizeofcmds {m.sizeofcmds}')
    bad = 0

    cmds = [c[0] for c in m.cmds]
    for need in (mo.LC_SYMTAB, mo.LC_DYSYMTAB, LC_DYLD_INFO_ONLY,
                 mo.LC_BUILD_VERSION, 0xd):
        if need not in cmds:
            print(f'  MISSING load command {need:#x}'); bad += 1

    for name, sg in segs.items():
        end = sg['fileoff'] + sg['filesize']
        ok = end <= len(d) and sg['filesize'] <= sg['vmsize']
        print(f'  {name:14s} vm {sg["vmaddr"]:#08x}+{sg["vmsize"]:#08x} '
              f'file {sg["fileoff"]:#08x}+{sg["filesize"]:#08x} '
              f'{"ok" if ok else "BAD"}')
        if not ok:
            bad += 1
        if sg['vmaddr'] % 0x4000 or sg['vmsize'] % 0x4000:
            print(f'    not 16k aligned'); bad += 1
        for sn, sa, ssz, soff, q, fl in sg['sects']:
            if ssz and not (sg['fileoff'] <= soff
                            and soff + ssz <= sg['fileoff'] + sg['filesize']):
                print(f'    section {sn} outside segment'); bad += 1

    # ldid appends a 16-byte LC_CODE_SIGNATURE in place when trollstore signs
    # this; without slack after the load commands that write lands on __text
    first = min(soff for name, sg in segs.items()
                for sn, sa, ssz, soff, q, fl in sg['sects'] if ssz)
    slack = first - (32 + m.sizeofcmds)
    print(f'  header slack {slack} bytes before {first:#x} '
          f'{"ok" if slack >= 16 else "BAD (ldid would clobber __text)"}')
    if slack < 16:
        bad += 1

    bv = struct.unpack_from('<4I', m.d, m.find(mo.LC_BUILD_VERSION)[0][1] + 8)
    print(f'  platform {bv[0]} minos {bv[1] >> 16}.{(bv[1] >> 8) & 0xff}')
    if bv[1] >> 16 != 15:
        print('    minos is not 15'); bad += 1

    info = m.find(LC_DYLD_INFO_ONLY)[0]
    (roff, rsz, boff, bsz, woff, wsz, loff, lsz, eoff, esz) = \
        struct.unpack_from('<10I', m.d, info[1] + 8)
    for nm, o, sz in (('rebase', roff, rsz), ('bind', boff, bsz),
                      ('export', eoff, esz)):
        inside = o + sz <= len(d)
        print(f'  {nm} {o:#x}+{sz} {"ok" if inside else "BAD"}')
        if not inside:
            bad += 1

    exp = set(dsc.trie_exports(d[eoff:eoff + esz]))
    wantset = set(l.strip() for l in open(want) if l.strip())
    print(f'  export trie: {len(exp)} symbols; '
          f'missing {len(wantset - exp)}, extra {len(exp - wantset)}')
    if wantset - exp:
        print('   ', sorted(wantset - exp)[:5]); bad += 1
    if exp - wantset:
        print('   ', sorted(exp - wantset)[:5]); bad += 1

    # every export address must land inside a real section
    ranges = []
    for name, sg in segs.items():
        for sn, sa, ssz, soff, q, fl in sg['sects']:
            if ssz:
                ranges.append((sa, sa + ssz, f'{name},{sn}'))
    st = cfix.Symtab(m)
    nundef = 0
    outside = 0
    for i, nm, ordi, weak in st.undef():
        nundef += 1
    for i in range(st.nsyms):
        e = st.symoff + 16 * i
        strx, ntype, sect, desc = struct.unpack_from('<IBBH', m.d, e)
        val = struct.unpack_from('<Q', m.d, e + 8)[0]
        if (ntype & 0x0e) == 0x0e:
            if not any(a <= val < b for a, b, _ in ranges):
                outside += 1
    print(f'  symtab: {st.nsyms} syms, {nundef} undefined, '
          f'{outside} defined outside any section')
    if outside:
        bad += 1

    undefs = sorted(nm for _i, nm, _o, _w in st.undef())
    print(f'  imports: {undefs}')

    # bind/rebase offsets must be inside their segment
    order = ['__TEXT', '__DATA_CONST', '__DATA', '__LINKEDIT']
    def check_stream(blob, kind):
        p = 0
        seg = off = None
        n = 0
        while p < len(blob):
            b = blob[p]; p += 1
            op, imm = b & 0xf0, b & 0x0f
            if b == 0:
                continue
            if kind == 'rebase':
                if op == 0x10:
                    pass
                elif op == 0x20:
                    seg = imm; off, p = mo.uleb(blob, p)
                elif op == 0x50:
                    n += imm
                    sg = segs[order[seg]]
                    if not (0 <= off < sg['vmsize']):
                        print(f'    rebase outside {order[seg]}'); return -1
                    off += 8 * imm
                else:
                    print(f'    unexpected rebase op {b:#x}'); return -1
            else:
                if op == 0x50 or op == 0x10:
                    pass
                elif op == 0x40:
                    e = blob.index(0, p); p = e + 1
                elif op == 0x70:
                    seg = imm; off, p = mo.uleb(blob, p)
                elif b == 0x90:
                    n += 1
                    sg = segs[order[seg]]
                    if not (0 <= off < sg['vmsize']):
                        print(f'    bind outside {order[seg]}'); return -1
                    off += 8
                else:
                    print(f'    unexpected bind op {b:#x}'); return -1
        return n

    nr = check_stream(d[roff:roff + rsz], 'rebase')
    nb = check_stream(d[boff:boff + bsz], 'bind')
    print(f'  {nr} rebases, {nb} binds')
    if nr < 0 or nb < 0:
        bad += 1

    print('RESULT:', 'ok' if bad == 0 else f'{bad} problems')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
