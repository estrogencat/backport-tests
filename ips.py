"""read an ios .ips crash report: exception, reason, and symbolized frames."""
import sys, json, os, argparse


def load(path):
    with open(path, encoding='utf-8') as f:
        head = json.loads(f.readline())
        body = json.loads(f.read())
    return head, body


def img_name(images, i):
    if i is None or i < 0 or i >= len(images):
        return '?'
    return images[i].get('name') or os.path.basename(
        images[i].get('path', '?'))


def frame(images, fr):
    i = fr.get('imageIndex')
    nm = img_name(images, i)
    off = fr.get('imageOffset', 0)
    sym = fr.get('symbol')
    loc = fr.get('symbolLocation', 0)
    base = images[i].get('base', 0) if 0 <= (i or -1) < len(images) else 0
    s = f'  {nm:28s} +{off:#09x}'
    if sym:
        s += f'  {sym} + {loc}'
    else:
        s += f'  (addr {base + off:#x})'
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('report')
    ap.add_argument('--threads', type=int, default=1)
    a = ap.parse_args()
    head, b = load(a.report)
    images = b.get('usedImages', [])

    print(f"{head.get('app_name')} {head.get('app_version')} "
          f"({head.get('build_version')}) on {head.get('os_version')}")
    exc = b.get('exception', {})
    print(f"exception {exc.get('type')} {exc.get('signal', '')} "
          f"codes {exc.get('codes', '')}")
    for k in ('asi', 'termination', 'exceptionReason', 'vmregioninfo',
              'lastExceptionBacktrace'):
        if k == 'lastExceptionBacktrace':
            continue
        if k in b:
            print(f'{k}: {json.dumps(b[k])[:600]}')

    if 'lastExceptionBacktrace' in b:
        print('\nlast NSException backtrace:')
        for fr in b['lastExceptionBacktrace']:
            print(frame(images, fr))

    faulting = [t for t in b.get('threads', []) if t.get('triggered')]
    for t in faulting[:a.threads]:
        print(f"\ntriggered thread ({t.get('name') or t.get('queue') or ''}):")
        for fr in t.get('frames', []):
            print(frame(images, fr))

    print('\nimages of interest:')
    for i, im in enumerate(images):
        nm = img_name(images, i)
        if any(s in nm.lower() for s in ('discord', 'shim', 'swift', 'hermes',
                                         'rive')):
            print(f'  [{i:2d}] {nm:30s} base {im.get("base", 0):#x} '
                  f'{im.get("path", "")[:70]}')


if __name__ == '__main__':
    main()
