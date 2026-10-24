import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import struct
import mo

class Fixups:
    """DYLD_CHAINED_FIXUPS imports array view over a MachO's bytes"""
    def __init__(s, m):
        s.m = m
        c = m.find(mo.LC_DYLD_CHAINED_FIXUPS)
        assert len(c) == 1
        s.base, s.size = struct.unpack_from('<II', m.d, c[0][1]+8)
        (s.version, s.starts_off, s.imports_off, s.symbols_off,
         s.imports_count, s.imports_format, s.symbols_format) = \
            struct.unpack_from('<7I', m.d, s.base)
        s.ent = {1: 4, 2: 8, 3: 16}[s.imports_format]

    def _ent_off(s, i):
        return s.base + s.imports_off + i * s.ent

    def get(s, i):
        o = s._ent_off(i)
        if s.imports_format in (1, 2):
            v = struct.unpack_from('<I', s.m.d, o)[0]
            lib = v & 0xff
            weak = (v >> 8) & 1
            noff = v >> 9
        else:
            v = struct.unpack_from('<Q', s.m.d, o)[0]
            lib = v & 0xffff
            weak = (v >> 16) & 1
            noff = v >> 32
        name, _ = mo.read_cstr(s.m.d, s.base + s.symbols_off + noff)
        return lib, weak, noff, name

    def set_lib(s, i, lib):
        o = s._ent_off(i)
        if s.imports_format in (1, 2):
            v = struct.unpack_from('<I', s.m.d, o)[0]
            v = (v & ~0xff) | (lib & 0xff)
            struct.pack_into('<I', s.m.d, o, v)
        else:
            v = struct.unpack_from('<Q', s.m.d, o)[0]
            v = (v & ~0xffff) | (lib & 0xffff)
            struct.pack_into('<Q', s.m.d, o, v)

    def all(s):
        return [s.get(i) for i in range(s.imports_count)]


class Symtab:
    def __init__(s, m):
        s.m = m
        c = m.find(mo.LC_SYMTAB)[0]
        s.symoff, s.nsyms, s.stroff, s.strsize = struct.unpack_from('<4I', m.d, c[1]+8)

    def undef(s):
        """yield (index, name, ordinal, weak)"""
        for i in range(s.nsyms):
            e = s.symoff + 16*i
            strx, ntype, sect, desc = struct.unpack_from('<IBBH', s.m.d, e)
            if (ntype & 0x0e) == 0x0:
                name, _ = mo.read_cstr(s.m.d, s.stroff + strx)
                yield i, name, (desc >> 8) & 0xff, bool(desc & 0x40)

    def set_ord(s, i, ordi):
        e = s.symoff + 16*i
        desc = struct.unpack_from('<H', s.m.d, e+6)[0]
        desc = (desc & 0x00ff) | ((ordi & 0xff) << 8)
        struct.pack_into('<H', s.m.d, e+6, desc)
