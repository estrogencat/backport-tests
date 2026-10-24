"""read the emitted shim back and check the hand-built swift metadata.

hand-written metadata fails the way hand-written stubs do -- quietly, with the
runtime reading a plausible-looking wrong number -- so everything here is
re-derived from the file rather than from the builder: follow the metadata
accessor's own instructions to the record, the record to its value witness
table and descriptor, and the descriptor's relative pointers back to names and
to the accessor they came from.

run with `python -E -P clockverify.py <shim>` (capstone lives in user
site-packages).
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, cfix, swiftclock
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

EXPECT = {
    swiftclock.INSTANT_MA: ('Instant', 16, 16, 0x7),
    swiftclock.CLOCK_MA: ('ContinuousClock', 0, 1, 0x0),
}


class Shim:
    def __init__(s, path):
        s.raw = open(path, 'rb').read()
        s.m = mo.MachO(s.raw)
        s.sects = []
        for seg, sg in s.m.segs().items():
            for sn, sa, ssz, soff, q, fl in sg['sects']:
                if ssz:
                    s.sects.append((sa, sa + ssz, f'{seg},{sn}'))
        s.sym = {}
        st = cfix.Symtab(s.m)
        for i in range(st.nsyms):
            e = st.symoff + 16 * i
            strx, ntype, sect, desc = struct.unpack_from('<IBBH', s.m.d, e)
            val = struct.unpack_from('<Q', s.m.d, e + 8)[0]
            if (ntype & 0x0e) == 0x0e:
                nm, _ = mo.read_cstr(s.m.d, st.stroff + strx)
                s.sym[nm] = val

    def where(s, a):
        for lo, hi, nm in s.sects:
            if lo <= a < hi:
                return nm
        return None

    def u64(s, a):
        return struct.unpack_from('<Q', s.raw, a)[0]

    def i32(s, a):
        return struct.unpack_from('<i', s.raw, a)[0]

    def cstr(s, a):
        e = s.raw.index(b'\x00', a)
        return s.raw[a:e].decode()


def main():
    sh = Shim(sys.argv[1])
    md_dis = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    bad = 0

    def fail(msg):
        nonlocal bad
        bad += 1
        print(f'  FAIL {msg}')

    present = [n for n in swiftclock.PROVIDES if n in sh.sym]
    print(f'clock symbols in the shim: {len(present)}/'
          f'{len(swiftclock.PROVIDES)}')
    if not present:
        print('RESULT: nothing to check')
        return 0

    for accessor, (name, size, stride, flags) in EXPECT.items():
        if accessor not in sh.sym:
            continue
        a = sh.sym[accessor]
        print(f'\n{name}: accessor at {a:#x}')

        # follow the accessor's own code to the metadata it returns
        page = reg = None
        ret_state = None
        for ins in md_dis.disasm(sh.raw[a:a + 0x20], a):
            if ins.mnemonic == 'adrp':
                p = ins.op_str.split(', ')
                reg, page = p[0], int(p[1].replace('#', ''), 0)
            elif ins.mnemonic == 'add' and page is not None:
                parts = [x.strip() for x in ins.op_str.split(',')]
                md = page + int(parts[2].replace('#', ''), 0)
            elif ins.mnemonic == 'mov' and ins.op_str.startswith('x1, #'):
                ret_state = int(ins.op_str.split('#')[1], 0)
            elif ins.mnemonic == 'ret':
                break
        if md is None:
            fail(f'{name}: cannot follow the accessor to a metadata address')
            continue
        print(f'  -> metadata {md:#x} in {sh.where(md)}')
        if sh.where(md) is None:
            fail(f'{name}: metadata is not inside any section')
            continue
        if ret_state != 0:
            fail(f'{name}: returns metadata state {ret_state}, want 0 '
                 f'(Complete)')
        if md % 8:
            fail(f'{name}: metadata {md:#x} is not 8-aligned')

        kind = sh.u64(md)
        if kind != swiftclock.METADATA_KIND_STRUCT:
            fail(f'{name}: kind word {kind:#x}, want '
                 f'{swiftclock.METADATA_KIND_STRUCT:#x}')
        else:
            print(f'  kind {kind:#x} (struct)')

        vwt = sh.u64(md - 8)
        print(f'  vwt {vwt:#x} in {sh.where(vwt)}')
        if sh.where(vwt) is None:
            fail(f'{name}: vwt pointer is not inside any section')
            continue
        gsize, gstride = struct.unpack_from('<QQ', sh.raw, vwt + 0x40)
        gflags, gxi = struct.unpack_from('<II', sh.raw, vwt + 0x50)
        print(f'  size {gsize} stride {gstride} flags {gflags:#x} '
              f'extraInhabitants {gxi}')
        if (gsize, gstride, gflags, gxi) != (size, stride, flags, 0):
            fail(f'{name}: vwt scalars are '
                 f'{(gsize, gstride, gflags, gxi)}, want '
                 f'{(size, stride, flags, 0)}')
        for i in range(8):
            w = sh.u64(vwt + 8 * i)
            if sh.where(w) != '__TEXT,__text':
                fail(f'{name}: witness[{i}] {w:#x} is in {sh.where(w)}, '
                     f'want __TEXT,__text')

        desc = sh.u64(md + 8)
        print(f'  descriptor {desc:#x} in {sh.where(desc)}')
        if sh.where(desc) is None:
            fail(f'{name}: descriptor is not inside any section')
            continue
        dflags = struct.unpack_from('<I', sh.raw, desc)[0]
        kind5 = dflags & 0x1f
        if kind5 != swiftclock.CTX_KIND_STRUCT:
            fail(f'{name}: descriptor kind {kind5}, want '
                 f'{swiftclock.CTX_KIND_STRUCT}')
        nm_rel = sh.i32(desc + 8)
        got = sh.cstr(desc + 8 + nm_rel) if nm_rel else None
        print(f'  descriptor name "{got}"')
        if got != name:
            fail(f'{name}: descriptor name is "{got}"')
        acc_rel = sh.i32(desc + 12)
        if desc + 12 + acc_rel != a:
            fail(f'{name}: descriptor access function -> '
                 f'{desc + 12 + acc_rel:#x}, want the accessor {a:#x}')
        par_rel = sh.i32(desc + 4)
        if not par_rel or sh.where(desc + 4 + par_rel) is None:
            fail(f'{name}: descriptor parent does not resolve')
        else:
            par = desc + 4 + par_rel
            pn_rel = sh.i32(par + 8)
            print(f'  parent "{sh.cstr(par + 8 + pn_rel)}" '
                  f'(kind {struct.unpack_from("<I", sh.raw, par)[0] & 0x1f})')

    # the nominal type descriptor export must be the same record the metadata
    # points at, not a second copy
    mn = swiftclock.INSTANT_MN
    if mn in sh.sym and swiftclock.INSTANT_MA in sh.sym:
        a = sh.sym[swiftclock.INSTANT_MA]
        page = None
        md = None
        for ins in md_dis.disasm(sh.raw[a:a + 0x20], a):
            if ins.mnemonic == 'adrp':
                page = int(ins.op_str.split(', ')[1].replace('#', ''), 0)
            elif ins.mnemonic == 'add' and page is not None:
                md = page + int(ins.op_str.split(',')[2].strip()
                                .replace('#', ''), 0)
                break
        if md is not None and sh.u64(md + 8) != sh.sym[mn]:
            fail(f'{mn} exports {sh.sym[mn]:#x} but the metadata points at '
                 f'{sh.u64(md + 8):#x}')
        else:
            print(f'\n{mn} == the descriptor the metadata points at, ok')

    # the enum-tag witnesses branch by hand-computed offsets, and getting one
    # wrong is silent: storing the payload case would skip clearing the tag
    # byte, so a later read reports an empty case instead. so check that the
    # "whichCase == 0" branch really lands on the instruction that zeroes it.
    for accessor, (name, size, _st, _fl) in EXPECT.items():
        if accessor not in sh.sym:
            continue
        if size == 0:
            # an empty payload has no separate branch to check: writing the
            # case number straight into the tag byte already stores 0 for the
            # payload case
            continue
        page = None
        md = None
        a = sh.sym[accessor]
        for ins in md_dis.disasm(sh.raw[a:a + 0x20], a):
            if ins.mnemonic == 'adrp':
                page = int(ins.op_str.split(', ')[1].replace('#', ''), 0)
            elif ins.mnemonic == 'add' and page is not None:
                md = page + int(ins.op_str.split(',')[2].strip()
                                .replace('#', ''), 0)
                break
        if md is None:
            continue
        setw = sh.u64(sh.u64(md - 8) + 8 * 7)
        ins = list(md_dis.disasm(sh.raw[setw:setw + 0x40], setw))
        tgt = None
        for i in ins[:3]:
            if i.mnemonic == 'cbz' and i.op_str.startswith('w1,'):
                tgt = int(i.op_str.split('#')[1], 0)
        if tgt is None:
            fail(f'{name}: storeEnumTagSinglePayload has no "whichCase == 0" '
                 f'branch')
            continue
        land = next((i for i in ins if i.address == tgt), None)
        if land is None or not (land.mnemonic == 'strb'
                                and land.op_str.startswith('wzr')):
            fail(f'{name}: storeEnumTagSinglePayload "whichCase == 0" branches '
                 f'to {tgt:#x} = '
                 f'"{land.mnemonic + " " + land.op_str if land else "?"}", '
                 f'want a strb wzr that clears the tag byte')
        else:
            print(f'\n{name}: storeEnumTagSinglePayload payload branch -> '
                  f'{land.mnemonic} {land.op_str}, ok')

    # the code stubs: check the register contracts that were measured
    contracts = {
        swiftclock.ADVANCED: (['x20'], ['x8']),
        swiftclock.NOW: ([], ['x8']),
    }
    for nm, (reads, writes) in contracts.items():
        if nm not in sh.sym:
            continue
        a = sh.sym[nm]
        text = []
        for ins in md_dis.disasm(sh.raw[a:a + 0x80], a):
            text.append(f'{ins.mnemonic} {ins.op_str}')
            if ins.mnemonic == 'ret':
                break
        body = ' | '.join(text)
        for r in reads:
            if f'[{r}]' not in body:
                fail(f'{nm} never reads through {r} (self register): {body}')
        for wr in writes:
            if f'[{wr}]' not in body and f'{wr}, [sp' not in body:
                fail(f'{nm} never writes through {wr} (result buffer)')
    print(f'\nchecked {len(present)} clock symbols')
    print('RESULT:', 'ok' if bad == 0 else f'{bad} problems')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
