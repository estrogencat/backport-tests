"""dump an ios dyld shared cache's exports to a small xz text file.

the cache is gigabytes; what the pipeline needs from it is only, per image,
the exported symbol names and the images it re-exports. names share long
prefixes, so xz brings that down to a few megabytes, small enough to ship in
the repo. collect15.py reads either the cache or this file and produces the
same index.

  python -I dumpdb.py <cache dir> ios15-exports.txt.xz

format, one record per image:

  @<image path>
  ><re-exported image path>       (zero or more)
  <symbol>                        (zero or more)
"""
import sys, os, struct, lzma
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsc import Cache, image_exports, lcs, NUL

LC_REEXPORT_DYLIB = 0x8000001f


def main():
    src, out = sys.argv[1], sys.argv[2]
    c = Cache(src)
    imgs = c.images()
    bypath = {p: a for a, p in imgs}      # last one wins, as in collect15
    nsym = 0
    with lzma.open(out, 'wt', encoding='utf-8', preset=9 | lzma.PRESET_EXTREME,
                   newline='\n') as f:
        f.write(f'#images {len(imgs)}\n')
        for p in sorted(bypath):
            a = bypath[p]
            f.write(f'@{p}\n')
            for cmd, b in lcs(c, a):
                if cmd == LC_REEXPORT_DYLIB:
                    noff = struct.unpack_from('<I', b, 8)[0]
                    f.write('>' + b[noff:].split(NUL)[0].decode() + '\n')
            try:
                syms = sorted(set(image_exports(c, a)))
            except Exception:
                syms = []
            nsym += len(syms)
            for s in syms:
                f.write(s + '\n')
    print(f'{len(bypath)} images, {nsym} symbols -> {out} '
          f'({os.path.getsize(out) / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
