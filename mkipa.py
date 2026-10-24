"""zip a patched bundle into an .ipa, keeping the executable bits.

trollstore does its own signing, so nothing here is signed; the mach-o files
just need mode 0755 to survive the round trip through zip.
"""
import sys, os, zipfile, stat, argparse

MAGICS = (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


def is_macho(path):
    with open(path, 'rb') as f:
        return f.read(4) in MAGICS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True, help='dir holding Payload/')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    root = os.path.abspath(a.bundle)
    payload = os.path.join(root, 'Payload')
    if not os.path.isdir(payload):
        raise SystemExit(f'no Payload/ in {root}')

    n = nx = 0
    with zipfile.ZipFile(a.out, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(payload):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, '/')
                exe = is_macho(full)
                zi = zipfile.ZipInfo(rel)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = ((0o100755 if exe else 0o100644) << 16)
                zi.date_time = (2026, 1, 1, 0, 0, 0)
                with open(full, 'rb') as f:
                    z.writestr(zi, f.read())
                n += 1
                nx += exe
    sz = os.path.getsize(a.out)
    print(f'{a.out}: {n} files ({nx} executable), {sz / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
