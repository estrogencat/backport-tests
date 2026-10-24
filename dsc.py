import struct, os

NUL = bytes([0])

class Cache:
    def __init__(s, D):
        s.files = []; s.maps = []
        for f in sorted(os.listdir(D)):
            if f.endswith('.symbols'):
                continue
            d = open(os.path.join(D, f), 'rb')
            h = d.read(0x1000)
            if h[:7] != b'dyld_v1':
                continue
            mo, mc = struct.unpack_from('<II', h, 0x10)
            idx = len(s.files); s.files.append(d)
            for i in range(mc):
                a, sz, fo, mx, ini = struct.unpack_from('<QQQII', h, mo + 32 * i)
                s.maps.append((a, sz, fo, idx))
            if f.endswith(('arm64', 'arm64e')):
                io, ic = struct.unpack_from('<II', h, 0x1c0)
                s.imgoff, s.imgcount, s.main = io, ic, idx

    def read(s, fidx, off, n):
        f = s.files[fidx]; f.seek(off); return f.read(n)

    def at(s, addr, n):
        for a, sz, fo, idx in s.maps:
            if a <= addr < a + sz:
                return s.read(idx, fo + addr - a, n)
        return None

    def file_of(s, addr):
        for a, sz, fo, idx in s.maps:
            if a <= addr < a + sz:
                return idx
        return None

    def images(s):
        d = s.read(s.main, s.imgoff, 32 * s.imgcount)
        out = []
        for i in range(s.imgcount):
            addr, mt, ino, poff, pad = struct.unpack_from('<QQQII', d, 32 * i)
            raw = s.read(s.main, poff, 256)
            out.append((addr, raw.split(NUL)[0].decode()))
        return out


def lcs(cache, addr):
    hdr = cache.at(addr, 32)
    ncmds, sizeofcmds = struct.unpack_from('<II', hdr, 16)
    body = cache.at(addr, 32 + sizeofcmds)
    p = 32; out = []
    for _ in range(ncmds):
        cmd, sz = struct.unpack_from('<II', body, p)
        out.append((cmd, body[p:p + sz])); p += sz
    return out


def uleb(d, p):
    r = 0; s = 0
    while True:
        b = d[p]; p += 1; r |= (b & 0x7f) << s; s += 7
        if not b & 0x80:
            return r, p


def trie_exports(d):
    out = []; stack = [(0, b'')]
    while stack:
        off, pfx = stack.pop()
        term, p = uleb(d, off)
        if term:
            out.append(pfx.decode('utf8', 'replace'))
        p += term
        n = d[p]; p += 1
        for _ in range(n):
            e = d.index(NUL, p)
            edge = d[p:e]; p = e + 1
            child, p = uleb(d, p)
            stack.append((child, pfx + edge))
    return out


def linkedit_file(cache, addr):
    for cmd, b in lcs(cache, addr):
        if cmd == 0x19 and b[8:24].split(NUL)[0] == b'__LINKEDIT':
            vm = struct.unpack_from('<Q', b, 24)[0]
            f = cache.file_of(vm)
            if f is not None:
                return f
    return len(cache.files) - 1


def image_exports(cache, addr):
    lef = linkedit_file(cache, addr)
    for cmd, b in lcs(cache, addr):
        if cmd == 0x80000033:
            off, size = struct.unpack_from('<II', b, 8)
            return trie_exports(cache.read(lef, off, size))
        if cmd in (0x22, 0x80000022):
            v = struct.unpack_from('<IIIIIIII', b, 8)
            if v[7]:
                return trie_exports(cache.read(lef, v[6], v[7]))
    return []
