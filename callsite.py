"""disassemble around a crash offset and name the objc selector being sent.

run with `python -E -P callsite.py ...` (capstone lives in user site-packages,
which -I would hide; -E -P still keeps cwd and the script dir off sys.path).
"""
import sys, os, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

CHAINED = 0x80000034


class Image:
    def __init__(s, path):
        s.raw = open(path, 'rb').read()
        s.m = mo.MachO(s.raw)
        s.segs = s.m.segs()
        s.base = min(v['vmaddr'] for v in s.segs.values() if v['filesize'])
        s.sects = []
        for name, sg in s.segs.items():
            for sn, sa, ssz, soff, q, fl in sg['sects']:
                s.sects.append((sa, sa + ssz, soff, f'{name},{sn}', fl))
        s.sects.sort()

    def sect_of(s, vm):
        for a, b, off, nm, fl in s.sects:
            if a <= vm < b:
                return a, off, nm, fl
        return None

    def file_off(s, vm):
        r = s.sect_of(vm)
        if r is None:
            return None
        a, off, nm, fl = r
        return off + (vm - a)

    def read(s, vm, n):
        o = s.file_off(vm)
        if o is None:
            return None
        return s.raw[o:o + n]

    def cstr(s, vm):
        o = s.file_off(vm)
        if o is None:
            return None
        e = s.raw.index(b'\x00', o)
        return s.raw[o:e].decode('utf8', 'replace')

    def chained_target(s, vm):
        """read a pointer slot and return the unslid vmaddr it rebases to."""
        b = s.read(vm, 8)
        if b is None:
            return None
        v = struct.unpack('<Q', b)[0]
        if v == 0:
            return None
        # DYLD_CHAINED_PTR_64 rebase: target is a 36-bit offset from the
        # image's preferred base; bind entries have bit 63 set.
        if v >> 63:
            return None
        return s.base + (v & 0xFFFFFFFFF)

    def describe_ptr(s, vm):
        """what does this pointer slot point at"""
        t = s.chained_target(vm)
        if t is None:
            return None
        r = s.sect_of(t)
        if r is None:
            return f'{t:#x}'
        nm = r[2]
        if 'methname' in nm or 'cstring' in nm or 'classname' in nm:
            return f'"{s.cstr(t)}"'
        return f'{nm} {t:#x}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('binary')
    ap.add_argument('offset', help='imageOffset from the crash report, hex ok')
    ap.add_argument('--before', type=int, default=24)
    ap.add_argument('--after', type=int, default=2)
    a = ap.parse_args()

    img = Image(a.binary)
    off = int(a.offset, 0)
    vm = img.base + off
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = False

    start = vm - 4 * a.before
    n = a.before + a.after
    code = img.read(start, 4 * n)
    print(f'{os.path.basename(a.binary)} base {img.base:#x}, '
          f'crash return address = image+{off:#x} ({vm:#x})')
    r = img.sect_of(vm)
    print(f'in section {r[2] if r else "?"}\n')

    adrp_reg = {}
    for ins in md.disasm(code, start):
        mark = '>>' if ins.address == vm - 4 else ('->' if ins.address == vm
                                                   else '  ')
        line = f'{mark} {ins.address:#012x}  {ins.mnemonic:8s} {ins.op_str}'
        note = ''
        if ins.mnemonic == 'adrp':
            parts = ins.op_str.split(', ')
            adrp_reg[parts[0]] = int(parts[1].replace('#', ''), 0)
        elif ins.mnemonic == 'ldr' and '[' in ins.op_str:
            dst = ins.op_str.split(',')[0].strip()
            inner = ins.op_str[ins.op_str.index('[') + 1:
                               ins.op_str.index(']')]
            bits = [x.strip() for x in inner.split(',')]
            breg = bits[0]
            if len(bits) > 1 and not bits[1].lstrip('-').startswith('#'):
                continue          # register-offset form, nothing to resolve
            disp = int(bits[1].replace('#', ''), 0) if len(bits) > 1 else 0
            if breg in adrp_reg:
                slot = adrp_reg[breg] + disp
                d = img.describe_ptr(slot)
                note = f'   ; [{slot:#x}] -> {d}'
                if d and d.startswith('"'):
                    adrp_reg.pop(breg, None)
        elif ins.mnemonic == 'add' and '#' in ins.op_str:
            parts = [x.strip() for x in ins.op_str.split(',')]
            if len(parts) == 3 and parts[1] in adrp_reg:
                tgt = adrp_reg[parts[1]] + int(parts[2].replace('#', ''), 0)
                rr = img.sect_of(tgt)
                if rr and ('methname' in rr[2] or 'cstring' in rr[2]
                           or 'classname' in rr[2]):
                    note = f'   ; "{img.cstr(tgt)}"'
                elif rr:
                    note = f'   ; {rr[2]} {tgt:#x}'
        elif ins.mnemonic in ('bl', 'b'):
            tgt = int(ins.op_str.replace('#', '').strip(), 0)
            rr = img.sect_of(tgt)
            if rr and 'objc_stubs' in rr[2]:
                note = f'   ; __objc_stubs -> {stub_sel(img, tgt, md)}'
            elif rr:
                note = f'   ; {rr[2]}'
        print(line + note)


def stub_sel(img, vm, md):
    """an __objc_stubs entry loads its selector then tail-calls objc_msgSend"""
    code = img.read(vm, 32)
    if code is None:
        return '?'
    adrp_reg = {}
    for ins in md.disasm(code, vm):
        if ins.mnemonic == 'adrp':
            p = ins.op_str.split(', ')
            adrp_reg[p[0]] = int(p[1].replace('#', ''), 0)
        elif ins.mnemonic == 'ldr' and '[' in ins.op_str:
            inner = ins.op_str[ins.op_str.index('[') + 1:
                               ins.op_str.index(']')]
            bits = [x.strip() for x in inner.split(',')]
            if bits[0] in adrp_reg:
                slot = adrp_reg[bits[0]] + (
                    int(bits[1].replace('#', ''), 0) if len(bits) > 1 else 0)
                d = img.describe_ptr(slot)
                if d and d.startswith('"'):
                    return d
    return '?'


if __name__ == '__main__':
    main()
