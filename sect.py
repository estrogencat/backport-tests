import struct, sys

NUL = bytes([0])

def sections(path):
    d = open(path, 'rb').read()
    magic = struct.unpack_from('<I', d, 0)[0]
    o = 0
    if magic in (0xcafebabe, 0xbebafeca):
        o = struct.unpack_from('>I', d, 16)[0]
    ncmds = struct.unpack_from('<I', d, o + 16)[0]
    p = o + 32
    out = {}
    for _ in range(ncmds):
        cmd, sz = struct.unpack_from('<II', d, p)
        if cmd == 0x19:
            segname = d[p + 8:p + 24].split(NUL)[0].decode()
            nsects = struct.unpack_from('<I', d, p + 64)[0]
            q = p + 72
            for _i in range(nsects):
                sn = d[q:q + 16].split(NUL)[0].decode()
                addr, size = struct.unpack_from('<QQ', d, q + 32)
                off = struct.unpack_from('<I', d, q + 48)[0]
                out[(segname, sn)] = (o + off, size, addr)
                q += 80
        p += sz
    return d, out

if __name__ == '__main__':
    path, seg, sec = sys.argv[1], sys.argv[2], sys.argv[3]
    d, s = sections(path)
    if (seg, sec) not in s:
        print('missing section', file=sys.stderr)
        for k in s:
            print(k, file=sys.stderr)
        sys.exit(1)
    off, size, addr = s[(seg, sec)]
    blob = d[off:off + size]
    for part in blob.split(NUL):
        if part:
            print(part.decode('utf8', 'replace'))
