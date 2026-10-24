"""check every _objc_(retain|release)_xN export really operates on xN."""
import sys, os, re, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mo, dsc
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

path = sys.argv[1]
raw = open(path, 'rb').read()
m = mo.MachO(raw)
info = m.find(0x80000022)[0]
v = struct.unpack_from('<10I', m.d, info[1] + 8)
trie = raw[v[8]:v[8] + v[9]]

# addresses come from the symtab (same values the trie encodes)
import cfix
st = cfix.Symtab(m)
addr = {}
for i in range(st.nsyms):
    e = st.symoff + 16 * i
    strx, ntype, sect, desc = struct.unpack_from('<IBBH', m.d, e)
    val = struct.unpack_from('<Q', m.d, e + 8)[0]
    if (ntype & 0x0e) == 0x0e:
        nm, _ = mo.read_cstr(m.d, st.stroff + strx)
        addr[nm] = val

md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
pat = re.compile(r'^_objc_(retain|release)_x(\d+)$')
bad = 0
checked = 0
for nm, a in sorted(addr.items()):
    mt = pat.match(nm)
    if not mt:
        continue
    kind, n = mt.group(1), int(mt.group(2))
    body = raw[a:a + 0x200]
    movs = []
    ldrs = []
    for ins in md.disasm(body, a):
        if ins.mnemonic == 'ret':
            break
        if ins.mnemonic == 'mov' and ins.op_str.startswith('x0, x'):
            movs.append(int(ins.op_str.split('x')[-1]))
        if ins.mnemonic == 'ldp' and \
                ins.op_str.replace(' ', '').startswith('x0,x1,[sp'):
            ldrs.append(0)
    checked += 1
    want_mov = [n] if n != 0 else []
    # release restores x0; retain must leave the result there
    want_ldr = [] if kind == 'retain' else [0]
    ok = movs == want_mov and ldrs == want_ldr
    if not ok:
        bad += 1
        print(f'MISMATCH {nm} @ {a:#x}: mov x0,x{movs} x0-restore {ldrs} '
              f'(expected mov {want_mov}, x0-restore {want_ldr})')
print(f'checked {checked} retain/release stubs, {bad} mismatched')

# and the got slot each stub calls
print('\ngot usage per stub (first 3):')
shown = 0
for nm, a in sorted(addr.items()):
    if not pat.match(nm) or shown >= 3:
        continue
    for ins in md.disasm(raw[a:a + 0x200], a):
        if ins.mnemonic == 'ldr' and 'x17, [x17' in ins.op_str:
            print(f'  {nm}: {ins.op_str}')
            shown += 1
            break

# ---- the real URL directory getters: x8 is the caller's result buffer, so it
# must be spilled before the first objc_msgSend and restored before the bridge
# call. getting this wrong would not trap, it would scribble somewhere else.
import mkshim

real = sorted(n for n in addr if n in mkshim.URL_DIRS)
badreal = 0
for nm in real:
    a = addr[nm]
    blrs, spill, restore = [], None, None
    for i, ins in enumerate(md.disasm(raw[a:a + mkshim.URLDIR_SIZE], a)):
        if ins.mnemonic == 'ret':
            break
        if ins.mnemonic == 'blr':
            blrs.append(i)
        if ins.mnemonic == 'str' and ins.op_str.startswith('x8, [sp'):
            spill = i
        if ins.mnemonic == 'ldr' and ins.op_str.startswith('x8, [sp'):
            restore = i
    why = []
    if len(blrs) != 4:
        why.append(f'{len(blrs)} blr, expected 4')
    if spill is None or (blrs and spill > blrs[0]):
        why.append('x8 not spilled before the first call')
    if restore is None or not (blrs and blrs[-2] < restore < blrs[-1]):
        why.append('x8 not restored just before the bridge call')
    if why:
        badreal += 1
        print(f'MISMATCH {nm} @ {a:#x}: ' + '; '.join(why))
print(f'checked {len(real)} real URL getters, {badreal} mismatched')
if bad or badreal:
    sys.exit(1)

# ---- the ret-self stubs must take the receiver from x20, swift's self
# register, not x0: x0 holds the *other* argument, so reading it would return
# the wrong object without trapping.
rs = sorted(n for n in addr if n in mkshim.RET_SELF)
badrs = 0
for nm in rs:
    a = addr[nm]
    movs = [ins.op_str for ins in md.disasm(raw[a:a + mkshim.REAL_SIZE['retself']], a)
            if ins.mnemonic == 'mov' and ins.op_str.startswith('x0, x')]
    if movs != ['x0, x20']:
        badrs += 1
        print(f'MISMATCH {nm} @ {a:#x}: moves {movs} into x0, expected x20')
print(f'checked {len(rs)} ret-self stubs, {badrs} mismatched')
if badrs:
    sys.exit(1)

# ---- Task.sleep(until:tolerance:clock:): forwarded to ios 15's
# Task.sleep(nanoseconds:). four things can be wrong here without crashing:
# the deadline could be read from the wrong argument register (x0 was
# measured; x1 is &tolerance and x2 &clock, both of which would produce a
# plausible-looking but wrong duration), x22 could be clobbered, the forward
# could be a call rather than a tail-branch (which would return into our own
# frame with the context already consumed), and the 1e9 division could be
# split wrong. all four are checked against the emitted bytes.
sl = sorted(n for n in addr if n in mkshim.SLEEP_UNTIL)
badsl = 0
got_base = None
for seg, sg in m.segs().items():
    for sn, sa, ssz, soff, q, fl in sg['sects']:
        if sn == '__got':
            got_base = sa
slotname = {got_base + 8 * i: n for i, (n, _o) in enumerate(mkshim.IMPORTS)}
for nm in sl:
    a = addr[nm]
    ins = list(md.disasm(raw[a:a + mkshim.REAL_SIZE['sleepuntil']], a))
    text = [f'{i.mnemonic} {i.op_str}' for i in ins]
    page, loaded, last_ldr_reg = {}, [], {}
    for i in ins:
        if i.mnemonic == 'adrp':
            d, t = i.op_str.split(', ')
            page[d] = int(t.replace('#', ''), 0)
        elif i.mnemonic == 'ldr' and '[' in i.op_str:
            d = i.op_str.split(',')[0].strip()
            inner = i.op_str[i.op_str.index('[') + 1:i.op_str.index(']')]
            b = [x.strip() for x in inner.split(',')]
            if b[0] in page:
                slot = page[b[0]] + (int(b[1].replace('#', ''), 0)
                                     if len(b) > 1 else 0)
                if slot in slotname:
                    loaded.append(slotname[slot])
                    last_ldr_reg[slotname[slot]] = d
    why = []
    if not any(t.startswith('ldp x') and t.endswith('[x0]') for t in text):
        why.append('does not read the deadline from [x0]')
    if any(i.op_str.split(',')[0].strip() == 'x22' for i in ins):
        why.append('writes x22')
    if loaded != ['_clock_gettime', mkshim.SLEEP_NANOS]:
        why.append(f'loads {[c[:28] for c in loaded]}')
    # the forward must be the last instruction, a br, through the register the
    # sleep pointer was loaded into -- and nothing may follow it
    tail = last_ldr_reg.get(mkshim.SLEEP_NANOS)
    if not text or text[-1] != f'br {tail}':
        why.append(f'does not end in `br {tail}` (ends {text[-1:]})')
    if f'blr {tail}' in text:
        why.append('calls the ios 15 sleep instead of tail-branching')
    if 'ret' in text:
        why.append('returns normally')
    if not any(t.startswith('add sp, sp') for t in text[:-1]):
        why.append('does not drop its frame before the branch')
    # 1e9 == 2**9 * 1953125: an `extr #9` then a single udiv by the odd half
    if not any(t.startswith('extr ') and t.endswith('#9') for t in text):
        why.append('no `extr #9` to narrow the 128-bit remainder')
    if not any(t.startswith('udiv ') for t in text):
        why.append('no udiv')
    odd = [t for t in text if t.startswith('mov x')
           and hex(mkshim.NANO_ODD & 0xffff) in t]
    if not odd:
        why.append(f'never materialises {mkshim.NANO_ODD}')
    if why:
        badsl += 1
        print(f'MISMATCH {nm[:40]} @ {a:#x}: ' + '; '.join(why))
print(f'checked {len(sl)} sleep-forward stubs, {badsl} mismatched')
if badsl:
    sys.exit(1)

# and the async function pointer for it must target that stub, not the shared
# trap: the caller reads the entry point out of the Tu record
import struct as _struct
for nm in sl:
    tu = nm + 'Tu'
    if tu not in addr:
        continue
    a = addr[tu]
    rel, size = _struct.unpack_from('<iI', raw, a)
    if a + rel != addr[nm]:
        print(f'MISMATCH {tu}: targets {a + rel:#x}, want {addr[nm]:#x}')
        sys.exit(1)
    print(f'{tu} -> the implementation, context size {size}, ok')

# ---- Product.priceFormatStyle: x8 is the caller's result buffer (sized by
# ios 15's own metadata for the type), so it must survive the two calls made
# before the initialiser, and the three imports must be called in order.
pf = sorted(n for n in addr if n in mkshim.PRICE_FMT)
badpf = 0
got_base = None
for seg, sg in m.segs().items():
    for sn, sa, ssz, soff, q, fl in sg['sects']:
        if sn == '__got':
            got_base = sa
slotname = {got_base + 8 * i: n for i, (n, _o) in enumerate(mkshim.IMPORTS)}
for nm in pf:
    a = addr[nm]
    page, called, spill, restore = {}, [], None, None
    for i, ins in enumerate(md.disasm(raw[a:a + mkshim.REAL_SIZE['pricefmt']],
                                      a)):
        if ins.mnemonic == 'adrp':
            p = ins.op_str.split(', ')
            page[p[0]] = int(p[1].replace('#', ''), 0)
        elif ins.mnemonic == 'ldr' and '[' in ins.op_str:
            inner = ins.op_str[ins.op_str.index('[') + 1:
                               ins.op_str.index(']')]
            b = [x.strip() for x in inner.split(',')]
            if b[0] in page:
                slot = page[b[0]] + (int(b[1].replace('#', ''), 0)
                                     if len(b) > 1 else 0)
                if slot in slotname:
                    called.append(slotname[slot])
            if ins.op_str.startswith('x8, [sp'):
                restore = i
        elif ins.mnemonic == 'str' and ins.op_str.startswith('x8, [sp'):
            spill = i
    want = [mkshim.LOCALE_CURRENT, mkshim.LOCALE_CURRENCY_CODE,
            mkshim.CURRENCY_INIT]
    why = []
    if called != want:
        why.append(f'calls {[c[:28] for c in called]}')
    if spill is None:
        why.append('never spills x8')
    if restore is None or spill is None or not spill < restore:
        why.append('does not reload x8 before the initialiser')
    if why:
        badpf += 1
        print(f'MISMATCH {nm[:40]} @ {a:#x}: ' + '; '.join(why))
print(f'checked {len(pf)} price-format stubs, {badpf} mismatched')
if badpf:
    sys.exit(1)
