import struct, sys

NUL = b'\x00'
LC_SEGMENT_64=0x19; LC_SYMTAB=0x2; LC_DYSYMTAB=0xb
LC_LOAD_DYLIB=0xc; LC_ID_DYLIB=0xd; LC_LOAD_WEAK_DYLIB=0x80000018
LC_REEXPORT_DYLIB=0x8000001f; LC_LOAD_UPWARD_DYLIB=0x80000023
LC_LAZY_LOAD_DYLIB=0x20
LC_DYLD_INFO_ONLY=0x80000022; LC_DYLD_EXPORTS_TRIE=0x80000033
LC_DYLD_CHAINED_FIXUPS=0x80000034
LC_BUILD_VERSION=0x32; LC_VERSION_MIN_IPHONEOS=0x25
LC_CODE_SIGNATURE=0x1d; LC_FUNCTION_STARTS=0x26; LC_DATA_IN_CODE=0x29
LC_SEGMENT_SPLIT_INFO=0x1e; LC_DYLIB_CODE_SIGN_DRS=0x2b
LC_LINKER_OPTIMIZATION_HINT=0x2e; LC_ATOM_INFO=0x36
LC_DYLD_EXPORTS=LC_DYLD_EXPORTS_TRIE
DYLIB_CMDS=(LC_LOAD_DYLIB,LC_LOAD_WEAK_DYLIB,LC_REEXPORT_DYLIB,LC_LOAD_UPWARD_DYLIB,LC_LAZY_LOAD_DYLIB)

class MachO:
    def __init__(s, data):
        s.d = bytearray(data)
        magic = struct.unpack_from('<I', s.d, 0)[0]
        assert magic == 0xfeedfacf, hex(magic)
        s.ncmds, s.sizeofcmds = struct.unpack_from('<II', s.d, 16)
        s.parse()

    def parse(s):
        s.cmds = []   # list of [cmd, off, size]
        p = 32
        for _ in range(s.ncmds):
            cmd, sz = struct.unpack_from('<II', s.d, p)
            s.cmds.append([cmd, p, sz]); p += sz

    def find(s, cmd):
        return [c for c in s.cmds if c[0] == cmd]

    def dylibs(s):
        """ordered list of (idx1based, cmd, name)"""
        out = []
        for c in s.cmds:
            if c[0] in DYLIB_CMDS or c[0] == LC_ID_DYLIB:
                if c[0] == LC_ID_DYLIB:
                    continue
                noff = struct.unpack_from('<I', s.d, c[1]+8)[0]
                nm = bytes(s.d[c[1]+noff:c[1]+c[2]]).split(NUL)[0].decode()
                out.append((len(out)+1, c[0], nm))
        return out

    def segs(s):
        out = {}
        for c in s.find(LC_SEGMENT_64):
            p = c[1]
            name = bytes(s.d[p+8:p+24]).split(NUL)[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', s.d, p+24)
            maxp, initp, nsects, flags = struct.unpack_from('<iiII', s.d, p+56)
            sects = []
            q = p+72
            for _i in range(nsects):
                sn = bytes(s.d[q:q+16]).split(NUL)[0].decode()
                sa, ssz = struct.unpack_from('<QQ', s.d, q+32)
                soff = struct.unpack_from('<I', s.d, q+48)[0]
                sflags = struct.unpack_from('<I', s.d, q+64)[0]
                sects.append((sn, sa, ssz, soff, q, sflags)); q += 80
            out[name] = dict(off=p, vmaddr=vmaddr, vmsize=vmsize,
                             fileoff=fileoff, filesize=filesize, sects=sects)
        return out

    def first_sect_off(s):
        """lowest file offset of any section/linkedit-ish data = header slack limit"""
        best = None
        for name, sg in s.segs().items():
            for sn, sa, ssz, soff, q, sflags in sg['sects']:
                if (sflags & 0xff) in (0x1, 0xc, 0x12):
                    continue
                if ssz and (best is None or soff < best):
                    best = soff
        return best

    def hdr_slack(s):
        return s.first_sect_off() - (32 + s.sizeofcmds)

    def set_ncmds(s, n, size):
        s.ncmds = n; s.sizeofcmds = size
        struct.pack_into('<II', s.d, 16, n, size)

    def append_cmd(s, blob):
        """insert a load command at end of LC list, using header slack"""
        assert len(blob) % 8 == 0
        need = len(blob)
        if s.hdr_slack() < need:
            raise RuntimeError(f'no header slack: have {s.hdr_slack()} need {need}')
        at = 32 + s.sizeofcmds
        s.d[at:at+need] = blob
        s.set_ncmds(s.ncmds+1, s.sizeofcmds+need)
        s.parse()
        return at

def dylib_cmd(cmd, path, ts=2, cur=0x10000, compat=0x10000):
    name = path.encode()+NUL
    sz = 24 + len(name)
    sz = (sz + 7) & ~7
    b = bytearray(sz)
    struct.pack_into('<IIIIII', b, 0, cmd, sz, 24, ts, cur, compat)
    b[24:24+len(name)] = name
    return bytes(b)

def uleb(d, p):
    r = 0; sh = 0
    while True:
        b = d[p]; p += 1; r |= (b & 0x7f) << sh; sh += 7
        if not b & 0x80: return r, p

def read_cstr(d, p):
    e = d.index(0, p); return bytes(d[p:e]).decode('utf8','replace'), e+1
