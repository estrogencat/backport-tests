"""real swift 5.7 `ContinuousClock` / `Duration` for the ios-15 shim.

discord uses this during react native startup to build an image-prefetch
deadline:

    ContinuousClock.now.advanced(by: .milliseconds(x))     x clamped to 0..60000

none of it exists on ios 15, and unlike every other gap so far it cannot be
answered with a stub: `ContinuousClock.Instant` is **resilient** (the stdlib is
built with library evolution and `Instant` is not `@frozen`), so the caller
asks the type's metadata for its size and allocates for it:

    bl   "type metadata accessor for ContinuousClock.Instant"
    ldur x19, [x0, #-8]        ; the value witness table
    ldr  x22, [x19, #0x40]     ; VWT + 0x40 = the type's size
    mov  x8, sp                ; ...and then allocates that much

so this emits **real metadata**: a struct metadata record, a value witness
table, and nominal type descriptors. the layout was confirmed by reading
`Swift.Int`'s own metadata out of the 15.7.7 cache -- kind word `0x200`, size
at VWT+0x40, flags `0x7` for an 8-aligned POD -- and the two enum-tag
witnesses are modelled on `Int`'s, which has zero extra inhabitants just like
these do.

being resilient is also a licence: nothing in discord's code knows the layout,
so `Instant` is defined here as exactly what the stdlib uses -- a
`Swift.Duration`, i.e. a 128-bit signed attosecond count, `_low` then `_high`.
`Duration` itself *is* `@frozen`, so its layout is not ours to choose and it is
returned directly in x0/x1.

register abi, read off discord's own call site (see README):

    Instant metadata accessor   x0 = request        -> x0 = metadata, x1 = state
    ContinuousClock.now         x8 = result buffer  -> void
    Instant.advanced(by:)       x8 = result, x20 = &self, x0/x1 = Duration
    Instant.<                   x0 = &lhs, x1 = &rhs -> w0
    Duration.milliseconds(_:)   d0 = Double         -> x0 = low, x1 = high
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a64

INSTANT_MA = '_$ss15ContinuousClockV7InstantVMa'
INSTANT_MN = '_$ss15ContinuousClockV7InstantVMn'
CLOCK_MA = '_$ss15ContinuousClockVMa'
NOW = '_$ss15ContinuousClockV3nowAB7InstantVvgZ'
ADVANCED = '_$ss15ContinuousClockV7InstantV8advanced2byADs8DurationV_tF'
LESS = '_$ss15ContinuousClockV7InstantV1loiySbAD_ADtFZ'
MILLIS = '_$ss8DurationV12millisecondsyABSdFZ'
SECONDS = '_$ss8DurationV7secondsyABSdFZ'
CONTINUOUS = '_$ss5ClockPss010ContinuousA0VRszrlE10continuousADvgZ'

PROVIDES = [INSTANT_MA, INSTANT_MN, CLOCK_MA, NOW, ADVANCED, LESS,
            MILLIS, SECONDS, CONTINUOUS]

# the one libSystem function needed, and its GOT name
CLOCK_GETTIME = '_clock_gettime'
CLOCK_MONOTONIC_RAW = 4          # continuous: keeps running across sleep

INSTANT_SIZE = 16                # a Swift.Duration: _low: UInt64, _high: Int64
METADATA_KIND_STRUCT = 0x200     # confirmed against Swift.Int in the 15 cache
POD_ALIGN8_FLAGS = 0x7           # alignmentMask 7, POD, inline, bitwise-takable

CTX_UNIQUE = 0x40
CTX_KIND_MODULE = 0
CTX_KIND_STRUCT = 17

NANOS_PER_SEC = 1_000_000_000
ATTOS_PER_NANO = 1_000_000_000
D_1E6 = 0x412E848000000000       # (double)1e6, ms -> ns
D_1E9 = 0x41CDCD6500000000       # (double)1e9, s  -> ns


def add_w_imm(d, n, imm):
    return a64.w(0x11000000 | (imm << 10) | (n << 5) | d)


def sub_w_imm(d, n, imm):
    return a64.w(0x51000000 | (imm << 10) | (n << 5) | d)


class Clock:
    """lays out and emits the code and data for the above symbols.

    two phases, like the rest of mkshim: `layout` assigns addresses and
    reports the sizes, `emit` produces the bytes once everything is placed.
    """

    def __init__(s, want, got_slot):
        """want: the shim's symbol set. got_slot(name) -> a __got address."""
        s.want = set(want)
        s.got_slot = got_slot
        s.active = bool(s.want & set(PROVIDES))

    # ---- phase 1: sizes and addresses

    def layout_code(s, code_addr):
        """mkshim knows __text addresses before __data ones, so this comes
        first; every body is a fixed size, so dummy addresses are enough."""
        s.code_addr = code_addr
        s.code = {}
        if not s.active:
            s.code_size = 0
            return 0
        p = code_addr
        for nm, body in s._bodies(dummy=True):
            s.code[nm] = p
            p += len(body)
        s.code_size = p - code_addr
        return s.code_size

    def layout_data(s, data_addr):
        s.data_addr = data_addr
        if not s.active:
            s.data_size = 0
            s.exports = {}
            s.rebase_offs = []
            return 0

        # data: [vwt][vwtptr|metadata] per type, then descriptors and names
        p = data_addr
        s.instant_vwt = p
        p += 0x58
        s.instant_blk = p                 # +0 vwt pointer, +8 metadata
        p += 24
        s.clock_vwt = p
        p += 0x58
        s.clock_blk = p
        p += 24
        s.module_desc = p
        p += 12
        s.clock_desc = p
        p += 28
        s.instant_desc = p
        p += 28
        s.names = {}
        for n in ('Swift', 'ContinuousClock', 'Instant'):
            s.names[n] = p
            p += len(n) + 1
        p = (p + 7) & ~7
        s.data_size = p - data_addr

        s.instant_md = s.instant_blk + 8
        s.clock_md = s.clock_blk + 8

        s.exports = {}
        for nm in PROVIDES:
            if nm not in s.want:
                continue
            if nm == INSTANT_MN:
                s.exports[nm] = (s.instant_desc, 'data')
            else:
                s.exports[nm] = (s.code[nm], 'text')

        # absolute pointers inside the data block, as (offset, target)
        r = []
        for vwt, blk, md, desc, witnesses in (
                (s.instant_vwt, s.instant_blk, s.instant_md, s.instant_desc,
                 s._instant_witnesses()),
                (s.clock_vwt, s.clock_blk, s.clock_md, s.clock_desc,
                 s._clock_witnesses())):
            for i, wn in enumerate(witnesses):
                r.append((vwt + 8 * i - data_addr, s.code[wn]))
            r.append((blk - data_addr, vwt))
            r.append((md + 8 - data_addr, desc))
        s.rebase_offs = r
        return s.data_size

    def _instant_witnesses(s):
        return ['w.copy16', 'w.destroy', 'w.copy16', 'w.copy16', 'w.copy16',
                'w.copy16', 'w.getenum16', 'w.setenum16']

    def _clock_witnesses(s):
        return ['w.destroy'] * 6 + ['w.getenum0', 'w.setenum0']

    # ---- phase 2: the code

    def _bodies(s, dummy=False):
        """[(name, bytes)] in layout order. with dummy=True the addresses are
        not yet known, which is fine because every body is a fixed size."""
        at = (lambda nm: 0) if dummy else (lambda nm: s.code[nm])
        out = []

        def add(nm, fn):
            out.append((nm, fn(at(nm))))

        add('w.copy16', lambda pc: b''.join([
            a64.ldp_x(8, 9, 1, 0), a64.stp_x(8, 9, 0, 0), a64.ret()]))
        add('w.destroy', lambda pc: a64.ret())
        add('w.getenum16', lambda pc: s._getenum(pc, INSTANT_SIZE))
        add('w.setenum16', lambda pc: s._setenum(pc, INSTANT_SIZE))
        add('w.getenum0', lambda pc: s._getenum0(pc))
        add('w.setenum0', lambda pc: s._setenum0(pc))

        if INSTANT_MA in s.want:
            add(INSTANT_MA, lambda pc: s._accessor(pc, 'instant_md'))
        if CLOCK_MA in s.want:
            add(CLOCK_MA, lambda pc: s._accessor(pc, 'clock_md'))
        if NOW in s.want:
            add(NOW, s._now)
        if ADVANCED in s.want:
            add(ADVANCED, lambda pc: b''.join([
                a64.ldp_x(9, 10, 20, 0),       # self is &Instant in x20
                a64.adds_x(9, 9, 0),
                a64.adc_x(10, 10, 1),
                a64.stp_x(9, 10, 8, 0),        # x8 = indirect result
                a64.ret()]))
        if LESS in s.want:
            add(LESS, lambda pc: b''.join([
                a64.ldp_x(9, 10, 0, 0),
                a64.ldp_x(11, 12, 1, 0),
                a64.subs_x(31, 9, 11),         # 128-bit signed compare
                a64.sbcs_x(31, 10, 12),
                a64.cset_w(0, 0xA),            # cset w0, lt  (inverse ge)
                a64.ret()]))
        if MILLIS in s.want:
            add(MILLIS, lambda pc: s._scale(pc, D_1E6))
        if SECONDS in s.want:
            add(SECONDS, lambda pc: s._scale(pc, D_1E9))
        if CONTINUOUS in s.want:
            # ContinuousClock is an empty struct: nothing to write to [x8]
            add(CONTINUOUS, lambda pc: a64.ret())
        return out

    def _accessor(s, pc, which):
        """a metadata accessor: x0 = request in, x0 = metadata / x1 = state"""
        md = 0 if not hasattr(s, which) else getattr(s, which)
        o = [a64.adrp(0, pc, md), a64.add_x_imm(0, 0, md & 0xfff),
             a64.movz_x_sh(1, 0), a64.ret()]      # state 0 = Complete
        return b''.join(o)

    def _now(s, pc):
        """clock_gettime(CLOCK_MONOTONIC_RAW) -> attoseconds into [x8]"""
        o = [a64.sub_sp(0x30),
             a64.stp_x(29, 30, 31, 0x20),
             a64.str_x(8, 31, 0x18),              # keep the result buffer
             a64.movz_x_sh(0, CLOCK_MONOTONIC_RAW),
             a64.add_x_imm(1, 31, 0)]             # x1 = sp = &timespec
        slot = s.got_slot(CLOCK_GETTIME)
        here = pc + 4 * len(o)
        o += [a64.adrp(16, here, slot), a64.ldr_x(16, 16, slot & 0xfff),
              a64.blr(16),
              a64.ldp_x(9, 10, 31, 0)]            # tv_sec, tv_nsec
        o.append(a64.imm64(11, NANOS_PER_SEC))
        o += [a64.mul_x(9, 9, 11),                # seconds -> ns
              a64.add_x(9, 9, 10)]                # + nanoseconds
        o.append(a64.imm64(11, ATTOS_PER_NANO))
        o += [a64.mul_x(12, 9, 11),               # ns -> attoseconds, 128 bit
              a64.umulh_x(13, 9, 11),
              a64.ldr_x(8, 31, 0x18),
              a64.stp_x(12, 13, 8, 0),
              a64.ldp_x(29, 30, 31, 0x20),
              a64.add_sp(0x30),
              a64.ret()]
        return b''.join(o)

    def _scale(s, pc, dbits):
        """Duration.<unit>(Double) -> 128-bit attoseconds in x0/x1.

        the double is scaled to whole nanoseconds first, then multiplied out to
        attoseconds as a 128-bit integer -- 60000 ms is 6e19 attoseconds, which
        does not fit in 64 bits, so the high half is real.
        """
        o = [a64.imm64(9, dbits), a64.fmov_dx(1, 9),
             a64.fmul_d(0, 0, 1), a64.fcvtzs_x_d(9, 0)]
        o.append(a64.imm64(10, ATTOS_PER_NANO))
        o += [a64.mul_x(11, 9, 10), a64.smulh_x(12, 9, 10),
              a64.mov_x(0, 11), a64.mov_x(1, 12), a64.ret()]
        return b''.join(o)

    def _getenum(s, pc, size):
        """getEnumTagSinglePayload(x0 value, w1 emptyCases, x2 type) -> w0.

        modelled on Swift.Int's witness: zero extra inhabitants, so the tag
        lives in one byte just past the payload and any case index beyond it
        is held in the payload itself.
        """
        zero = pc + 6 * 4
        return b''.join([
            a64.cbz_w(1, pc, zero),
            a64.ldrb_w(8, 0, size),
            a64.cbz_w(8, pc + 8, zero),
            a64.ldr_w(8, 0, 0),
            add_w_imm(0, 8, 1),
            a64.ret(),
            a64.movz_w(0, 0),
            a64.ret()])

    def _setenum(s, pc, size):
        """storeEnumTagSinglePayload(x0 value, w1 case, w2 emptyCases, x3 ty)"""
        nocases = pc + 7 * 4
        payload = pc + 11 * 4
        return b''.join([
            a64.cbz_w(2, pc, nocases),
            a64.cbz_w(1, pc + 4, payload),
            sub_w_imm(8, 1, 1),
            a64.stp_x(8, 31, 0, 0),          # index, zero-filled payload
            a64.movz_w(8, 1),
            a64.strb_w(8, 0, size),          # tag byte = 1
            a64.ret(),
            a64.cbz_w(1, nocases, nocases + 3 * 4),
            sub_w_imm(8, 1, 1),
            a64.stp_x(8, 31, 0, 0),
            a64.ret(),                       # nocases + 3*4
            a64.strb_w(31, 0, size),         # payload case: tag byte = 0
            a64.ret()])

    def _getenum0(s, pc):
        """same, for an empty payload: the tag byte is the whole thing"""
        zero = pc + 3 * 4
        return b''.join([
            a64.cbz_w(1, pc, zero),
            a64.ldrb_w(0, 0, 0),
            a64.ret(),
            a64.movz_w(0, 0),
            a64.ret()])

    def _setenum0(s, pc):
        done = pc + 2 * 4
        return b''.join([
            a64.cbz_w(2, pc, done),
            a64.strb_w(1, 0, 0),
            a64.ret()])

    # ---- phase 2: the data

    def emit(s):
        if not s.active:
            return b'', b''
        code = bytearray(s.code_size)
        for nm, body in s._bodies():
            off = s.code[nm] - s.code_addr
            code[off:off + len(body)] = body

        d = bytearray(s.data_size)

        def put(addr, blob):
            o = addr - s.data_addr
            d[o:o + len(blob)] = blob

        def vwt(addr, size, stride, flags):
            # the 8 witness pointers are filled in by rebases; only the
            # trailing scalars are literal here
            put(addr + 0x40, struct.pack('<QQII', size, stride, flags, 0))

        vwt(s.instant_vwt, INSTANT_SIZE, INSTANT_SIZE, POD_ALIGN8_FLAGS)
        vwt(s.clock_vwt, 0, 1, 0)
        # metadata: [-8] = vwt (rebased), [0] = kind, [8] = descriptor (rebased)
        put(s.instant_md, struct.pack('<Q', METADATA_KIND_STRUCT))
        put(s.clock_md, struct.pack('<Q', METADATA_KIND_STRUCT))

        def rel(at, target):
            return struct.pack('<i', 0 if target is None else target - at)

        put(s.module_desc, struct.pack('<I', CTX_KIND_MODULE | CTX_UNIQUE)
            + rel(s.module_desc + 4, None)
            + rel(s.module_desc + 8, s.names['Swift']))

        def struct_desc(addr, parent, name, accessor):
            return (struct.pack('<I', CTX_KIND_STRUCT | CTX_UNIQUE)
                    + rel(addr + 4, parent)
                    + rel(addr + 8, name)
                    + rel(addr + 12, accessor)
                    + rel(addr + 16, None)        # no field descriptor
                    + struct.pack('<II', 1, 0))   # 1 field, no offset vector

        put(s.clock_desc, struct_desc(
            s.clock_desc, s.module_desc, s.names['ContinuousClock'],
            s.code.get(CLOCK_MA)))
        put(s.instant_desc, struct_desc(
            s.instant_desc, s.clock_desc, s.names['Instant'],
            s.code.get(INSTANT_MA)))
        for n, a in s.names.items():
            put(a, n.encode() + b'\x00')

        for off, target in s.rebase_offs:
            struct.pack_into('<Q', d, off, target)
        return bytes(code), bytes(d)
