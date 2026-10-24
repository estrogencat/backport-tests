"""build libdiscordshim.dylib: an arm64 ios-15 dylib exporting the symbols
discord 349.0 imports and ios 15 does not have.

no apple toolchain is used; the mach-o is emitted byte by byte.

  _objc_retain_xN / _objc_release_xN  asm stubs that move xN into x0 and call
                                      libobjc objc_retain / objc_release, with
                                      every other register preserved
  _OBJC_CLASS_$_*                     inert objc class pair, superclass
                                      NSObject, no methods, no ivars
  swift metadata (Mn/Mp/Mc/MQ/Tq/Tb/Tl/Tn/WC)
                                      64 zero bytes each
  swift async function pointers (Tu)  {relative ptr -> trap, context size}
  everything else                     one instruction: brk #index (trap mode,
                                      so a crash pc names the symbol via the
                                      .map file) or ret (ret mode)
"""
import sys, os, struct, re, uuid, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a64, trie, swiftclock

PAGE = 0x4000
MH_MAGIC_64 = 0xfeedfacf
CPU_ARM64 = 0x0100000C
MH_DYLIB = 6
MH_DYLDLINK = 0x4
MH_TWOLEVEL = 0x80

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xb
LC_LOAD_DYLIB = 0xc
LC_ID_DYLIB = 0xd
LC_UUID = 0x1b
LC_SOURCE_VERSION = 0x2A
LC_BUILD_VERSION = 0x32
LC_DYLD_INFO_ONLY = 0x80000022
PLATFORM_IOS = 2

SEG_TEXT, SEG_DC, SEG_DA, SEG_LE = 0, 1, 2, 3

URL_BRIDGE = ('_$s10Foundation3URLV36_unconditionallyBridgeFromObjectiveC'
              'yACSo5NSURLCSgFZ')
LOCALE_CURRENT = '_$s10Foundation6LocaleV19autoupdatingCurrentACvgZ'
LOCALE_CURRENCY_CODE = '_$s10Foundation6LocaleV12currencyCodeSSSgvg'
CURRENCY_INIT = ('_$sSo9NSDecimala10FoundationE11FormatStyleV8CurrencyV4code'
                 '6localeAGSS_AC6LocaleVtcfC')
# ios 15's own Task.sleep(nanoseconds:) -- same async signature as the ios-16
# sleep we replace, so the latter can tail-branch straight into it
SLEEP_NANOS = ('_$sScTss5NeverORszABRs_rlE5sleep11nanosecondsys6UInt64V_'
               'tYaKFZ')

IMPORTS = [
    ('_objc_retain', 1),
    ('_objc_release', 1),
    ('_objc_msgSend', 1),
    ('_OBJC_CLASS_$_NSObject', 1),
    ('_OBJC_METACLASS_$_NSObject', 1),
    ('__objc_empty_cache', 1),
    ('_OBJC_CLASS_$_NSFileManager', 3),
    ('_clock_gettime', 2),
    (URL_BRIDGE, 4),
    (LOCALE_CURRENT, 4),
    (LOCALE_CURRENCY_CODE, 4),
    (CURRENCY_INIT, 4),
    (SLEEP_NANOS, 5),
]
GOT_IDX = {n: i for i, (n, _o) in enumerate(IMPORTS)}
DYLIBS = ['/usr/lib/libobjc.A.dylib', '/usr/lib/libSystem.B.dylib',
          '/System/Library/Frameworks/Foundation.framework/Foundation',
          '/usr/lib/swift/libswiftFoundation.dylib',
          '/usr/lib/swift/libswift_Concurrency.dylib']
NCMDS = 4 + 1 + 1 + 1 + 1 + len(DYLIBS) + 3

# selectors the real implementations send, uniqued by the runtime through
# __objc_selrefs like any other objc image
SELECTORS = ['defaultManager', 'URLsForDirectory:inDomains:', 'firstObject']

# section order is fixed so the symtab section indices can be derived from it
# rather than hardcoded
SECTIONS = ['__text', '__objc_classname', '__objc_methname',
            '__got', '__objc_classlist', '__objc_imageinfo',
            '__objc_const', '__objc_data', '__data', '__objc_selrefs']
SECT_IDX = {n: i + 1 for i, n in enumerate(SECTIONS)}

# ldid (which is what trollstore signs with) appends an LC_CODE_SIGNATURE,
# 16 bytes, to the load-command list and does not move anything to make room.
# without slack here that write lands on the first instructions of __text.
SIGN_SLACK = 32

# NSSearchPathDirectory values for the ios-16 URL directory getters
NS_USER_DOMAIN_MASK = 1
URL_DIRS = {
    '_$s10Foundation3URLV15cachesDirectoryACvgZ': 13,              # caches
    '_$s10Foundation3URLV16libraryDirectoryACvgZ': 5,              # library
    '_$s10Foundation3URLV18documentsDirectoryACvgZ': 9,            # documents
    '_$s10Foundation3URLV27applicationSupportDirectoryACvgZ': 14,  # appsupport
}

# ios-16 methods with no ios-15 equivalent whose least-wrong answer is the
# receiver itself, retained. measured abi: self in x20 (the swift self
# register), the result returned +1 in x0.
RET_SELF = {
    # CGPathRef.subtracting(_:using:) -- path boolean ops are ios 16. there is
    # no CoreGraphics equivalent on 15, so "A minus B" answers A: a mask built
    # this way then hides nothing instead of hiding the wrong thing.
    '_$sSo9CGPathRefa12CoreGraphicsE11subtracting_5usingA2B_AC0A8FillRuleOtF':
        None,
}

# Task.sleep(until:tolerance:clock:) -- swift 5.7, and discord's image
# prefetch timeout. forwarded to ios 15's Task.sleep(nanoseconds:).
#
# the async abi here was read off discord's own code and ios 15's stdlib:
# the context is in x22, a function's own ResumeParent sits at [x22, #8], the
# caller's funclet recovers its context itself with `ldr x22, [x22]`, and a
# thrown error comes back in **x20** (0 = success), not x21 -- the resume
# funclet after this very call does `str x20, [ctx, #0x60]` then `cbz x20`.
SLEEP_UNTIL = {
    '_$sScTss5NeverORszABRs_rlE5sleep5until9tolerance5clocky7InstantQyd___'
    '8DurationQyd__Sgqd__tYaKs5ClockRd__lFZ': None,
}

# StoreKit 2's Product.priceFormatStyle (ios 16). the type it returns and the
# initialiser for it both exist on ios 15, so this one is built for real.
PRICE_FMT = {
    '_$s8StoreKit7ProductV16priceFormatStyleSo9NSDecimala10FoundationE0e'
    'F0V8CurrencyVvg': None,
}
# Swift small-string form for the fallback code: the utf8 bytes sit in the
# first word and the second word's top byte is 0xE0 | count (discord builds
# "currency" exactly this way a few instructions earlier)
USD_LOW = 0x445355               # 'U','S','D'
USD_HIGH = 0xE300000000000000    # small string, 3 bytes

NANO_ODD = 1_953_125             # the odd half of 1e9 = 2**9 * 1953125

REAL_KIND = {}
for _n, _d in URL_DIRS.items():
    REAL_KIND[_n] = ('urldir', _d)
for _n in RET_SELF:
    REAL_KIND[_n] = ('retself', None)
for _n in SLEEP_UNTIL:
    REAL_KIND[_n] = ('sleepuntil', None)
for _n in PRICE_FMT:
    REAL_KIND[_n] = ('pricefmt', None)

META_SUFFIXES = ('Mn', 'Mp', 'Mc', 'MQ', 'Tq', 'Tb', 'Tl', 'Tn', 'WC')
RETREL = re.compile(r'^_objc_(retain|release)_x(\d+)$')


def align(v, a):
    return (v + a - 1) // a * a


uleb = trie.uleb


CLOCK_SYMS = set(swiftclock.PROVIDES)


def classify(name):
    if name in CLOCK_SYMS:
        return ('clock', None)
    m = RETREL.match(name)
    if m:
        return ('retrel', (m.group(1), int(m.group(2))))
    if name in REAL_KIND:
        return ('real', REAL_KIND[name])
    if name.startswith('_OBJC_CLASS_$_'):
        return ('objcclass', name[len('_OBJC_CLASS_$_'):])
    if name.startswith(('_$s', '_$S')):
        if name.endswith('Tu'):
            return ('asyncfp', None)
        if name.endswith(META_SUFFIXES):
            return ('meta', None)
    return ('code', None)


XPAIRS = [(0, 1, 0x00), (2, 3, 0x10), (4, 5, 0x20), (6, 7, 0x30), (8, 9, 0x40),
          (10, 11, 0x50), (12, 13, 0x60), (14, 15, 0x70), (16, 17, 0x80)]
QPAIRS = [(0, 1, 0xa0), (2, 3, 0xc0), (4, 5, 0xe0), (6, 7, 0x100)]
FRAME = 0x200
X18_OFF = 0x90
SPILL = 0x98
FP_OFF = 0x1f0


def retrel_stub(pc, got_addr, kind, n):
    """objc_{retain,release}_xN.

    the argument is in xN, not x0. retain returns the object in x0 the normal
    way -- discord relies on that: at image+0xcd4398 it does
    `bl _objc_retain_x19` then `mov x19, x0`. so x0 must NOT be restored for
    retain. release returns nothing, so it preserves every register.
    xN itself survives either way: retain hands back the same pointer, and
    x19-x28 are callee-saved while x1-x18 are restored here.
    """
    retain = kind == 'retain'
    o = [a64.sub_sp(FRAME), a64.stp_x(29, 30, 31, FP_OFF)]
    for x, y, off in XPAIRS:
        o.append(a64.stp_x(x, y, 31, off))
    o.append(a64.str_x(18, 31, X18_OFF))
    for x, y, off in QPAIRS:
        o.append(a64.stp_q(x, y, 31, off))
    o.append(a64.mov_x(0, n) if n != 0 else a64.nop())
    here = pc + 4 * len(o)
    slot = got_addr + 8 * GOT_IDX['_objc_retain' if retain
                                  else '_objc_release']
    o.append(a64.adrp(17, here, slot))
    o.append(a64.ldr_x(17, 17, slot & 0xfff))
    o.append(a64.blr(17))
    for x, y, off in QPAIRS:
        o.append(a64.ldp_q(x, y, 31, off))
    o.append(a64.ldr_x(18, 31, X18_OFF))
    for x, y, off in XPAIRS:
        if retain and x == 0:
            o.append(a64.ldr_x(1, 31, off + 8))     # keep the result in x0
        else:
            o.append(a64.ldp_x(x, y, 31, off))
    o.append(a64.ldp_x(29, 30, 31, FP_OFF))
    o += [a64.add_sp(FRAME), a64.ret()]
    return b''.join(o)


RETREL_SIZE = len(retrel_stub(0, 0x8000, 'retain', 19))
assert RETREL_SIZE == len(retrel_stub(0, 0x8000, 'release', 1))


def urldir_stub(pc, got_addr, selref_addr, directory):
    """a real `static URL.<x>Directory.getter`.

        [[NSFileManager defaultManager]
            URLsForDirectory:directory inDomains:NSUserDomainMask].firstObject
        -> URL._unconditionallyBridgeFromObjectiveC(that)

    `URL` is a resilient struct, so its size is not known to callers and the
    public abi returns it **indirectly**: the caller passes a result buffer in
    x8 and the getter returns void. discord does exactly that -- `mov x8, x20`
    right before the call -- and ios 15's bridge entry point takes the same
    shape (`mov x19, x8` on entry), so x8 is saved across the msgSends and
    handed to the bridge, which builds an owned URL in place. the NSURL from
    `firstObject` is +0 and the bridge takes it at +0, so no retain is needed
    here; objc_msgSend clobbers x16/x17, so the called pointer is reloaded from
    __got each time.
    """
    o = []

    def load(reg, slot):
        here = pc + 4 * len(o)
        o.append(a64.adrp(reg, here, slot))
        o.append(a64.ldr_x(reg, reg, slot & 0xfff))

    def got(name):
        return got_addr + 8 * GOT_IDX[name]

    def msgsend():
        load(16, got('_objc_msgSend'))
        o.append(a64.blr(16))

    o += [a64.sub_sp(0x20), a64.stp_x(29, 30, 31, 0x10),
          a64.str_x(8, 31, 0)]                     # keep the result buffer
    load(0, got('_OBJC_CLASS_$_NSFileManager'))
    load(1, selref_addr['defaultManager'])
    msgsend()
    load(1, selref_addr['URLsForDirectory:inDomains:'])
    o += [a64.movz_x(2, directory), a64.movz_x(3, NS_USER_DOMAIN_MASK)]
    msgsend()
    load(1, selref_addr['firstObject'])
    msgsend()
    o.append(a64.ldr_x(8, 31, 0))
    load(16, got(URL_BRIDGE))
    o.append(a64.blr(16))
    o += [a64.ldp_x(29, 30, 31, 0x10), a64.add_sp(0x20), a64.ret()]
    return b''.join(o)


def retself_stub(pc, got_addr, _selrefs=None, _arg=None):
    """an ios-16 method whose least-wrong answer is its own receiver.

    self arrives in x20 (swift's self register) and the result is returned +1
    in x0, both measured on the device with `probe_cgpath.py` rather than
    inferred -- the resilient enum argument goes by address in x1, which is
    what makes x20 rather than x0 the receiver here.
    """
    o = [a64.sub_sp(0x20), a64.stp_x(29, 30, 31, 0x10), a64.mov_x(0, 20)]
    slot = got_addr + 8 * GOT_IDX['_objc_retain']
    here = pc + 4 * len(o)
    o.append(a64.adrp(16, here, slot))
    o.append(a64.ldr_x(16, 16, slot & 0xfff))
    o.append(a64.blr(16))
    o += [a64.ldp_x(29, 30, 31, 0x10), a64.add_sp(0x20), a64.ret()]
    return b''.join(o)


def sleepuntil_stub(pc, got_addr, _selrefs=None, _arg=None):
    """Task.sleep(until:tolerance:clock:) -> ios 15's Task.sleep(nanoseconds:).

        remaining = deadline - ContinuousClock.now          (attoseconds)
        Task.sleep(nanoseconds: max(remaining, 0) / 1e9)

    `&deadline` is in **x0**, measured with `probe_sleep.py` (x1 is
    `&tolerance`, x2 `&clock`, x3 the clock's metadata). an `Instant` here is
    whatever `swiftclock.py` says it is -- a 128-bit signed attosecond count,
    `_low` then `_high` -- because the type is resilient.

    the forward is an async **tail-branch**, not a call: ios 15's
    `sleep(nanoseconds:)` is async throws -> Void, exactly the signature being
    replaced, so x22 is left alone and the context discord allocated (1024
    bytes, from our `Tu` record) becomes the callee's, with `Parent` and
    `ResumeParent` already filled in. there is no async return sequence here
    at all -- the real sleep performs it.

    the division is exact rather than approximate: both operands are whole
    nanoseconds scaled by 1e9, and `floor(floor(v / 512) / 1953125)` is
    `floor(v / 1e9)` for any v, so the 128-bit value is funnel-shifted down to
    64 bits with `extr` first and then divided once. 60 s is 6e19 attoseconds,
    which is under 2^73, so nothing is lost in the shift.
    """
    DL, TS, FP, FRAME = 0x00, 0x10, 0x30, 0x40
    LT = 0xB
    o = []

    def load(reg, slot):
        here = pc + 4 * len(o)
        o.append(a64.adrp(reg, here, slot))
        o.append(a64.ldr_x(reg, reg, slot & 0xfff))

    o += [a64.sub_sp(FRAME), a64.stp_x(29, 30, 31, FP),
          a64.ldp_x(9, 10, 0, 0),              # the deadline, across the call
          a64.stp_x(9, 10, 31, DL),
          a64.movz_x_sh(0, swiftclock.CLOCK_MONOTONIC_RAW),
          a64.add_x_imm(1, 31, TS)]
    load(16, got_addr + 8 * GOT_IDX['_clock_gettime'])
    o.append(a64.blr(16))
    o.append(a64.ldp_x(9, 10, 31, TS))         # tv_sec, tv_nsec
    o.append(a64.imm64(11, swiftclock.NANOS_PER_SEC))
    o += [a64.mul_x(9, 9, 11), a64.add_x(9, 9, 10),     # now, in nanoseconds
          a64.mul_x(12, 9, 11), a64.umulh_x(13, 9, 11),  # ...in attoseconds
          a64.ldp_x(14, 15, 31, DL),
          a64.subs_x(12, 14, 12),              # deadline - now, 128-bit
          a64.sbcs_x(13, 15, 13),
          a64.csel_x(12, 31, 12, LT),          # already past it -> 0
          a64.csel_x(13, 31, 13, LT),
          a64.extr_x(12, 13, 12, 9)]           # (hi:lo) >> 9
    o.append(a64.imm64(11, NANO_ODD))          # 1e9 == 2**9 * 1953125
    o.append(a64.udiv_x(0, 12, 11))            # x0 = nanoseconds to sleep
    load(17, got_addr + 8 * GOT_IDX[SLEEP_NANOS])   # x17, not the x16 the
    o += [a64.ldp_x(29, 30, 31, FP),                #  clock_gettime call used
          a64.add_sp(FRAME), a64.br(17)]
    return b''.join(o)


def pricefmt_stub(pc, got_addr, _selrefs=None, _arg=None):
    """Product.priceFormatStyle -> Decimal.FormatStyle.Currency.

        Decimal.FormatStyle.Currency(code: Locale.autoupdatingCurrent
                                           .currencyCode ?? "USD",
                                     locale: .autoupdatingCurrent)

    both the type and `init(code:locale:)` are in the ios 15 overlay -- the
    metadata accessor discord already imports binds straight to it, so the
    result buffer the caller hands over in x8 is sized by ios 15's own
    metadata and the value fits exactly.

    the abi was read off ios 15's init: x8 = result, x0/x1 = the code string,
    x2 = &locale (9 bytes, read as `ldr x8, [x2]` + `ldrb w9, [x2, #8]`).
    `self` -- the Product -- arrives in x20 and is **ignored**: on ios 16 the
    currency comes from the product's app store storefront, and every ios-16
    way to ask for it is itself missing here, so this reports the device
    locale's currency instead. right for almost everyone, wrong for anyone
    whose store region differs from their locale.
    """
    LOC, SAVE, FP = 0x10, 0x28, 0x30
    o = []

    def load(reg, slot):
        here = pc + 4 * len(o)
        o.append(a64.adrp(reg, here, slot))
        o.append(a64.ldr_x(reg, reg, slot & 0xfff))

    def call(name):
        load(16, got_addr + 8 * GOT_IDX[name])
        o.append(a64.blr(16))

    o += [a64.sub_sp(0x40), a64.stp_x(29, 30, 31, FP),
          a64.str_x(8, 31, SAVE),            # keep the result buffer
          a64.add_x_imm(8, 31, LOC)]         # x8 = &locale scratch
    call(LOCALE_CURRENT)
    o.append(a64.add_x_imm(20, 31, LOC))     # self = &locale
    call(LOCALE_CURRENCY_CODE)               # -> x0/x1 = String?
    # a nil String? has a zero object word; swap in "USD" branchlessly
    o.append(a64.imm64(9, USD_LOW))
    o.append(a64.imm64(10, USD_HIGH))
    o += [a64.subs_x_imm(31, 1, 0),
          a64.csel_x(0, 9, 0, 0),            # eq -> the fallback
          a64.csel_x(1, 10, 1, 0),
          a64.add_x_imm(2, 31, LOC),         # x2 = &locale
          a64.ldr_x(8, 31, SAVE)]
    call(CURRENCY_INIT)
    o += [a64.ldp_x(29, 30, 31, FP), a64.add_sp(0x40), a64.ret()]
    return b''.join(o)


_DUMMY_SELS = {s: 0x8000 + 8 * i for i, s in enumerate(SELECTORS)}
REAL_STUB = {'urldir': urldir_stub, 'retself': retself_stub,
             'sleepuntil': sleepuntil_stub, 'pricefmt': pricefmt_stub}
REAL_SIZE = {'urldir': len(urldir_stub(0, 0x8000, _DUMMY_SELS, 13)),
             'retself': len(retself_stub(0, 0x8000)),
             'sleepuntil': len(sleepuntil_stub(0, 0x8000)),
             'pricefmt': len(pricefmt_stub(0, 0x8000))}
URLDIR_SIZE = REAL_SIZE['urldir']


class Shim:
    def __init__(s, symbols, install_name, mode, minos=(15, 0)):
        s.syms = sorted(set(symbols))
        s.install = install_name
        s.mode = mode
        s.minos = minos
        s.kind = {n: classify(n) for n in s.syms}
        s.classes = sorted(v[1] for n, v in s.kind.items()
                           if v[0] == 'objcclass')
        s.layout()

    def lcs(s, L):
        out = []

        def seg(name, vm, vmsz, fo, fsz, maxp, initp, sects):
            b = bytearray(72 + 80 * len(sects))
            struct.pack_into('<II16sQQQQiiII', b, 0, LC_SEGMENT_64, len(b),
                             name.encode(), vm, vmsz, fo, fsz,
                             maxp, initp, len(sects), 0)
            q = 72
            for sn, sa, ssz, soff, salign, sflags in sects:
                struct.pack_into('<16s16sQQIIIIII', b, q, sn.encode(),
                                 name.encode(), sa, ssz, soff, salign,
                                 0, 0, sflags, 0)
                q += 80
            return bytes(b)

        out.append(seg('__TEXT', 0, L['text_vmsz'], 0, L['text_fsz'],
                       5, 5, L['text_sects']))
        out.append(seg('__DATA_CONST', L['dc_vm'], L['dc_vmsz'], L['dc_fo'],
                       L['dc_fsz'], 3, 3, L['dc_sects']))
        out.append(seg('__DATA', L['da_vm'], L['da_vmsz'], L['da_fo'],
                       L['da_fsz'], 3, 3, L['da_sects']))
        out.append(seg('__LINKEDIT', L['le_vm'], L['le_vmsz'], L['le_fo'],
                       L['le_fsz'], 1, 1, []))

        def dylib(cmd, path):
            nm = path.encode() + b'\x00'
            sz = align(24 + len(nm), 8)
            b = bytearray(sz)
            struct.pack_into('<IIIIII', b, 0, cmd, sz, 24, 2, 0x10000, 0x10000)
            b[24:24 + len(nm)] = nm
            return bytes(b)

        out.append(dylib(LC_ID_DYLIB, s.install))

        b = bytearray(48)
        struct.pack_into('<II', b, 0, LC_DYLD_INFO_ONLY, 48)
        struct.pack_into('<10I', b, 8,
                         L['rebase_off'], L['rebase_sz'],
                         L['bind_off'], L['bind_sz'],
                         0, 0, 0, 0,
                         L['export_off'], L['export_sz'])
        out.append(bytes(b))

        out.append(struct.pack('<6I', LC_SYMTAB, 24, L['symoff'], L['nsyms'],
                               L['stroff'], L['strsize']))

        b = bytearray(80)
        struct.pack_into('<II', b, 0, LC_DYSYMTAB, 80)
        struct.pack_into('<18I', b, 8,
                         0, 0,
                         0, L['nexts'],
                         L['nexts'], L['nundefs'],
                         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        out.append(bytes(b))

        for d in DYLIBS:
            out.append(dylib(LC_LOAD_DYLIB, d))

        out.append(struct.pack('<6I', LC_BUILD_VERSION, 24, PLATFORM_IOS,
                               (s.minos[0] << 16) | (s.minos[1] << 8),
                               (15 << 16), 0))
        out.append(struct.pack('<II', LC_UUID, 24) + L['uuid'])
        out.append(struct.pack('<IIQ', LC_SOURCE_VERSION, 16, 0))
        assert len(out) == NCMDS, (len(out), NCMDS)
        return b''.join(out)

    def dummy_layout(s):
        z = dict.fromkeys(
            ['text_vmsz', 'text_fsz', 'dc_vm', 'dc_vmsz', 'dc_fo', 'dc_fsz',
             'da_vm', 'da_vmsz', 'da_fo', 'da_fsz', 'le_vm', 'le_vmsz',
             'le_fo', 'le_fsz', 'rebase_off', 'rebase_sz', 'bind_off',
             'bind_sz', 'export_off', 'export_sz', 'symoff', 'nsyms',
             'stroff', 'strsize', 'nexts', 'nundefs'], 0)
        z['uuid'] = b'\x00' * 16
        z['text_sects'] = [('__text', 0, 0, 0, 2, 0x80000400),
                           ('__objc_classname', 0, 0, 0, 0, 2),
                           ('__objc_methname', 0, 0, 0, 0, 2)]
        z['dc_sects'] = [('__got', 0, 0, 0, 3, 6),
                         ('__objc_classlist', 0, 0, 0, 3, 0x10000000),
                         ('__objc_imageinfo', 0, 0, 0, 2, 0)]
        z['da_sects'] = [('__objc_const', 0, 0, 0, 3, 0),
                         ('__objc_data', 0, 0, 0, 3, 0),
                         ('__data', 0, 0, 0, 3, 0),
                         ('__objc_selrefs', 0, 0, 0, 3, 0x10000005)]
        return z

    def layout(s):
        hdrsz = 32 + len(s.lcs(s.dummy_layout()))

        # leave room for the LC_CODE_SIGNATURE ldid appends when trollstore
        # signs this, or it overwrites the first instructions of __text
        code_addr = align(hdrsz + SIGN_SLACK, 16)
        p = code_addr
        s.code_sym_addr = {}
        s.generic = [n for n in s.syms if s.kind[n][0] == 'code']
        s.generic_index = {n: i for i, n in enumerate(s.generic)}
        for n in s.generic:
            s.code_sym_addr[n] = p
            p += 4
        s.trap_shared = p
        p += 4
        s.retrel = [(n, s.kind[n][1]) for n in s.syms
                    if s.kind[n][0] == 'retrel']
        for n, _v in s.retrel:
            s.code_sym_addr[n] = p
            p += RETREL_SIZE
        s.real = [(n, s.kind[n][1][0], s.kind[n][1][1]) for n in s.syms
                  if s.kind[n][0] == 'real']
        for n, rk, _arg in s.real:
            s.code_sym_addr[n] = p
            p += REAL_SIZE[rk]
        s.clock = swiftclock.Clock(
            s.syms, lambda n: getattr(s, 'got_addr', 0) + 8 * GOT_IDX[n])
        p += s.clock.layout_code(p)
        code_size = p - code_addr

        cn_addr = align(p, 8)
        s.classname_addr = {}
        blob = bytearray()
        for c in s.classes:
            s.classname_addr[c] = cn_addr + len(blob)
            blob += c.encode() + b'\x00'
        s.classname_blob = bytes(blob)

        mn_addr = align(cn_addr + len(blob), 8)
        s.selname_addr = {}
        mnblob = bytearray()
        for sel in SELECTORS:
            s.selname_addr[sel] = mn_addr + len(mnblob)
            mnblob += sel.encode() + b'\x00'
        s.methname_blob = bytes(mnblob)
        text_fsz = align(mn_addr + len(mnblob), PAGE)

        dc = text_fsz
        s.got_addr = dc
        got_sz = 8 * len(IMPORTS)
        s.classlist_addr = s.got_addr + got_sz
        cl_sz = 8 * len(s.classes)
        s.imageinfo_addr = s.classlist_addr + cl_sz
        dc_fsz = align(s.imageinfo_addr + 8 - dc, PAGE)

        da = dc + dc_fsz
        s.da_base = da
        p = da
        s.ro_addr = {}
        for c in s.classes:
            s.ro_addr[c] = (p, p + 72)
            p += 144
        objc_const_end = p
        s.cls_addr = {}
        for c in s.classes:
            s.cls_addr[c] = (p, p + 40)
            p += 80
        objc_data_end = p
        data_start = align(p, 8)
        p = data_start
        s.meta_addr = {}
        s.async_addr = {}
        for n in s.syms:
            if s.kind[n][0] == 'meta':
                s.meta_addr[n] = p
                p += 64
            elif s.kind[n][0] == 'asyncfp':
                s.async_addr[n] = p
                p += 8
        p = align(p, 8)
        p += s.clock.layout_data(p)
        data_end = p
        selrefs_start = align(data_end, 8)
        s.selref_addr = {}
        p = selrefs_start
        for sel in SELECTORS:
            s.selref_addr[sel] = p
            p += 8
        selrefs_end = p
        da_fsz = align(selrefs_end - da, PAGE)

        s.addr_of = {}
        s.sect_of = {}
        for n in s.syms:
            k, v = s.kind[n]
            if k == 'clock':
                pass
            elif k in ('code', 'retrel', 'real'):
                s.addr_of[n] = s.code_sym_addr[n]
            elif k == 'objcclass':
                s.addr_of[n] = s.cls_addr[v][0]
            elif k == 'meta':
                s.addr_of[n] = s.meta_addr[n]
            else:
                s.addr_of[n] = s.async_addr[n]
            if k == 'clock':
                a, where = s.clock.exports[n]
                s.addr_of[n] = a
                s.sect_of[n] = SECT_IDX['__text' if where == 'text'
                                        else '__data']
                continue
            s.sect_of[n] = SECT_IDX[{'code': '__text', 'retrel': '__text',
                                     'real': '__text',
                                     'objcclass': '__objc_data',
                                     'meta': '__data',
                                     'asyncfp': '__data'}[k]]

        rebase = s.rebase_blob()
        bind = s.bind_blob()
        export = trie.build([(n, 0, s.addr_of[n]) for n in s.syms])

        nlist = bytearray()
        strtab = bytearray(b'\x00')
        for n in s.syms:
            strx = len(strtab)
            strtab += n.encode() + b'\x00'
            nlist += struct.pack('<IBBHQ', strx, 0x0f, s.sect_of[n], 0,
                                 s.addr_of[n])
        for n, o in IMPORTS:
            strx = len(strtab)
            strtab += n.encode() + b'\x00'
            nlist += struct.pack('<IBBHQ', strx, 0x01, 0, (o << 8) & 0xffff, 0)
        while len(strtab) % 8:
            strtab += b'\x00'

        le = da + da_fsz
        o = le
        L = {}
        L['rebase_off'] = o; L['rebase_sz'] = len(rebase); o += len(rebase)
        L['bind_off'] = o; L['bind_sz'] = len(bind); o += len(bind)
        L['export_off'] = o; L['export_sz'] = len(export); o += len(export)
        L['symoff'] = o; L['nsyms'] = len(s.syms) + len(IMPORTS)
        o += len(nlist)
        L['stroff'] = o; L['strsize'] = len(strtab); o += len(strtab)
        le_fsz = o - le

        L.update(
            text_vmsz=text_fsz, text_fsz=text_fsz,
            dc_vm=dc, dc_vmsz=dc_fsz, dc_fo=dc, dc_fsz=dc_fsz,
            da_vm=da, da_vmsz=da_fsz, da_fo=da, da_fsz=da_fsz,
            le_vm=le, le_vmsz=align(le_fsz, PAGE), le_fo=le, le_fsz=le_fsz,
            nexts=len(s.syms), nundefs=len(IMPORTS),
            uuid=uuid.uuid5(uuid.NAMESPACE_OID, 'discordshim').bytes,
            text_sects=[
                ('__text', code_addr, code_size, code_addr, 2, 0x80000400),
                ('__objc_classname', cn_addr, len(s.classname_blob), cn_addr,
                 0, 2),
                ('__objc_methname', mn_addr, len(s.methname_blob), mn_addr,
                 0, 2)],
            dc_sects=[
                ('__got', s.got_addr, got_sz, s.got_addr, 3, 6),
                ('__objc_classlist', s.classlist_addr, cl_sz,
                 s.classlist_addr, 3, 0x10000000),
                ('__objc_imageinfo', s.imageinfo_addr, 8, s.imageinfo_addr,
                 2, 0)],
            da_sects=[
                ('__objc_const', da, objc_const_end - da, da, 3, 0),
                ('__objc_data', objc_const_end, objc_data_end - objc_const_end,
                 objc_const_end, 3, 0),
                ('__data', data_start, data_end - data_start, data_start,
                 3, 0),
                ('__objc_selrefs', selrefs_start, selrefs_end - selrefs_start,
                 selrefs_start, 3, 0x10000005)],
        )
        order = [x[0] for x in L['text_sects'] + L['dc_sects'] + L['da_sects']]
        assert order == SECTIONS, (order, SECTIONS)
        s.L = L
        s.blobs = dict(rebase=rebase, bind=bind, export=export,
                       nlist=bytes(nlist), strtab=bytes(strtab))
        s.total = o

    def rebases(s):
        out = [(SEG_DC, s.classlist_addr + 8 * i - s.got_addr)
               for i in range(len(s.classes))]
        for c in s.classes:
            ro, mro = s.ro_addr[c]
            cls, meta = s.cls_addr[c]
            b = s.da_base
            out += [(SEG_DA, ro + 24 - b), (SEG_DA, mro + 24 - b),
                    (SEG_DA, cls - b), (SEG_DA, cls + 32 - b),
                    (SEG_DA, meta + 32 - b)]
        # each __objc_selrefs slot points at its __objc_methname string; the
        # objc runtime uniques them into real SELs when the image loads
        out += [(SEG_DA, s.selref_addr[sel] - s.da_base) for sel in SELECTORS]
        # the swift metadata records and value witness tables hold absolute
        # pointers to their witnesses, metadata and descriptors
        out += [(SEG_DA, s.clock.data_addr + off - s.da_base)
                for off, _t in s.clock.rebase_offs]
        return sorted(out)

    def binds(s):
        out = [(SEG_DC, s.got_addr + 8 * GOT_IDX[n] - s.got_addr, o, n)
               for n, o in IMPORTS]
        for c in s.classes:
            cls, meta = s.cls_addr[c]
            b = s.da_base
            out += [(SEG_DA, cls + 8 - b, 1, '_OBJC_CLASS_$_NSObject'),
                    (SEG_DA, cls + 16 - b, 1, '__objc_empty_cache'),
                    (SEG_DA, meta - b, 1, '_OBJC_METACLASS_$_NSObject'),
                    (SEG_DA, meta + 8 - b, 1, '_OBJC_METACLASS_$_NSObject'),
                    (SEG_DA, meta + 16 - b, 1, '__objc_empty_cache')]
        return out

    def rebase_blob(s):
        out = bytearray([0x10 | 1])
        for seg, off in s.rebases():
            out.append(0x20 | seg)
            out += uleb(off)
            out.append(0x50 | 1)
        out.append(0x00)
        while len(out) % 8:
            out.append(0x00)
        return bytes(out)

    def bind_blob(s):
        out = bytearray([0x50 | 1])
        for seg, off, ordi, name in s.binds():
            out.append(0x10 | ordi)
            out.append(0x40)
            out += name.encode() + b'\x00'
            out.append(0x70 | seg)
            out += uleb(off)
            out.append(0x90)
        out.append(0x00)
        while len(out) % 8:
            out.append(0x00)
        return bytes(out)

    def emit(s):
        L = s.L
        d = bytearray(s.total)
        lc = s.lcs(L)
        struct.pack_into('<IiiIIIII', d, 0, MH_MAGIC_64, CPU_ARM64, 0,
                         MH_DYLIB, NCMDS, len(lc),
                         MH_DYLDLINK | MH_TWOLEVEL, 0)
        d[32:32 + len(lc)] = lc

        for n in s.generic:
            a = s.code_sym_addr[n]
            d[a:a + 4] = (a64.brk(s.generic_index[n] & 0xffff)
                          if s.mode == 'trap' else a64.ret())
        d[s.trap_shared:s.trap_shared + 4] = (a64.brk(0xffff)
                                              if s.mode == 'trap'
                                              else a64.ret())
        for n, (kind, nn) in s.retrel:
            a = s.code_sym_addr[n]
            body = retrel_stub(a, s.got_addr, kind, nn)
            d[a:a + len(body)] = body
        for n, rk, arg in s.real:
            a = s.code_sym_addr[n]
            body = REAL_STUB[rk](a, s.got_addr, s.selref_addr, arg)
            assert len(body) == REAL_SIZE[rk]
            d[a:a + len(body)] = body

        ccode, cdata = s.clock.emit()
        if ccode:
            a = s.clock.code_addr
            d[a:a + len(ccode)] = ccode
            a = s.clock.data_addr
            d[a:a + len(cdata)] = cdata

        cn = L['text_sects'][1]
        d[cn[3]:cn[3] + len(s.classname_blob)] = s.classname_blob
        mn = L['text_sects'][2]
        d[mn[3]:mn[3] + len(s.methname_blob)] = s.methname_blob
        for sel in SELECTORS:
            struct.pack_into('<Q', d, s.selref_addr[sel], s.selname_addr[sel])

        for i, c in enumerate(s.classes):
            struct.pack_into('<Q', d, s.classlist_addr + 8 * i,
                             s.cls_addr[c][0])
        struct.pack_into('<II', d, s.imageinfo_addr, 0, 0)

        for c in s.classes:
            ro, mro = s.ro_addr[c]
            cls, meta = s.cls_addr[c]
            nm = s.classname_addr[c]
            struct.pack_into('<IIIIQQQQQQQ', d, ro, 0, 8, 8, 0, 0, nm,
                             0, 0, 0, 0, 0)
            struct.pack_into('<IIIIQQQQQQQ', d, mro, 1, 40, 40, 0, 0, nm,
                             0, 0, 0, 0, 0)
            struct.pack_into('<QQQQQ', d, cls, meta, 0, 0, 0, ro)
            struct.pack_into('<QQQQQ', d, meta, 0, 0, 0, 0, mro)

        for n, a in s.async_addr.items():
            tgt = s.code_sym_addr.get(n[:-2], s.trap_shared)
            struct.pack_into('<iI', d, a, tgt - a, 1024)

        for k in ('rebase', 'bind', 'export'):
            off = L[k + '_off']
            d[off:off + len(s.blobs[k])] = s.blobs[k]
        d[L['symoff']:L['symoff'] + len(s.blobs['nlist'])] = s.blobs['nlist']
        d[L['stroff']:L['stroff'] + len(s.blobs['strtab'])] = s.blobs['strtab']
        return bytes(d)

    def symbol_map(s):
        lines = [f'# addr(file offset from mach_header) kind symbol',
                 f'# trap mode: brk #N where N is the line index among '
                 f'"code" symbols, in this file order']
        i = 0
        for n in s.syms:
            k = s.kind[n][0]
            extra = ''
            if k == 'code':
                extra = f' brk#{s.generic_index[n]}'
            lines.append(f'{s.addr_of[n]:#010x} {k:9s} {n}{extra}')
        lines.append(f'{s.trap_shared:#010x} asynctrap - brk#65535')
        return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('lists', nargs='+')
    ap.add_argument('--mode', choices=['trap', 'ret'], default='trap')
    ap.add_argument('--install', default='@rpath/libdiscordshim.dylib')
    a = ap.parse_args()
    syms = set()
    for f in a.lists:
        for line in open(f):
            line = line.strip()
            if line:
                syms.add(line)
    sh = Shim(syms, a.install, a.mode)
    data = sh.emit()
    open(a.out, 'wb').write(data)
    open(a.out + '.map', 'w').write(sh.symbol_map())
    counts = {}
    for n in sh.syms:
        k = sh.kind[n][0]
        counts[k] = counts.get(k, 0) + 1
    print(f'{a.out}: {len(data)} bytes, {len(sh.syms)} exports, {counts}')


if __name__ == '__main__':
    main()
