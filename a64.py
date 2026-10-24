"""minimal arm64 instruction encoders."""
import struct

def w(x):
    return struct.pack('<I', x & 0xffffffff)

def stp_x(t1, t2, base, imm):
    assert imm % 8 == 0 and -512 <= imm < 512
    return w(0xA9000000 | (((imm // 8) & 0x7f) << 15) | (t2 << 10) | (base << 5) | t1)

def ldp_x(t1, t2, base, imm):
    assert imm % 8 == 0 and -512 <= imm < 512
    return w(0xA9400000 | (((imm // 8) & 0x7f) << 15) | (t2 << 10) | (base << 5) | t1)

def stp_q(t1, t2, base, imm):
    assert imm % 16 == 0 and -1024 <= imm < 1024
    return w(0xAD000000 | (((imm // 16) & 0x7f) << 15) | (t2 << 10) | (base << 5) | t1)

def ldp_q(t1, t2, base, imm):
    assert imm % 16 == 0 and -1024 <= imm < 1024
    return w(0xAD400000 | (((imm // 16) & 0x7f) << 15) | (t2 << 10) | (base << 5) | t1)

def str_x(t, base, imm):
    assert imm % 8 == 0 and 0 <= imm < 0x8000
    return w(0xF9000000 | ((imm // 8) << 10) | (base << 5) | t)

def ldr_x(t, base, imm):
    assert imm % 8 == 0 and 0 <= imm < 0x8000
    return w(0xF9400000 | ((imm // 8) << 10) | (base << 5) | t)

def mov_x(d, m):
    return w(0xAA0003E0 | (m << 16) | d)

def movz_x(d, imm16):
    assert 0 <= imm16 < 0x10000
    return w(0xD2800000 | (imm16 << 5) | d)

def sub_sp(imm):
    assert 0 <= imm < 0x1000
    return w(0xD1000000 | (imm << 10) | (31 << 5) | 31)

def add_sp(imm):
    assert 0 <= imm < 0x1000
    return w(0x91000000 | (imm << 10) | (31 << 5) | 31)

def adrp(d, pc, target):
    delta = (target >> 12) - (pc >> 12)
    assert -(1 << 20) <= delta < (1 << 20), delta
    imm = delta & 0x1fffff
    return w(0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7ffff) << 5) | d)

def blr(n):
    return w(0xD63F0000 | (n << 5))

def ret():
    return w(0xD65F03C0)

def brk(imm16):
    return w(0xD4200000 | ((imm16 & 0xffff) << 5))

def nop():
    return w(0xD503201F)

# ---- added for the swift Clock/Duration implementations

def movz_x_sh(d, imm16, sh=0):
    assert 0 <= imm16 < 0x10000 and sh in (0, 16, 32, 48)
    return w(0xD2800000 | ((sh // 16) << 21) | (imm16 << 5) | d)

def movk_x(d, imm16, sh=0):
    assert 0 <= imm16 < 0x10000 and sh in (0, 16, 32, 48)
    return w(0xF2800000 | ((sh // 16) << 21) | (imm16 << 5) | d)

def movz_w(d, imm16):
    assert 0 <= imm16 < 0x10000
    return w(0x52800000 | (imm16 << 5) | d)

def imm64(d, value):
    """materialise a full 64-bit constant in Xd"""
    value &= (1 << 64) - 1
    out = [movz_x_sh(d, value & 0xffff, 0)]
    for sh in (16, 32, 48):
        h = (value >> sh) & 0xffff
        if h:
            out.append(movk_x(d, h, sh))
    return b''.join(out)

def add_x_imm(d, n, imm):
    assert 0 <= imm < 0x1000
    return w(0x91000000 | (imm << 10) | (n << 5) | d)

def add_x(d, n, m):
    return w(0x8B000000 | (m << 16) | (n << 5) | d)

def adds_x(d, n, m):
    return w(0xAB000000 | (m << 16) | (n << 5) | d)

def adc_x(d, n, m):
    return w(0x9A000000 | (m << 16) | (n << 5) | d)

def subs_x(d, n, m):
    return w(0xEB000000 | (m << 16) | (n << 5) | d)

def sbcs_x(d, n, m):
    return w(0xFA000000 | (m << 16) | (n << 5) | d)

def cset_w(d, invcond):
    """CSET Wd, cond  ==  CSINC Wd, WZR, WZR, invert(cond)"""
    return w(0x1A9F07E0 | (invcond << 12) | d)

def mul_x(d, n, m):
    return w(0x9B007C00 | (m << 16) | (n << 5) | d)

def umulh_x(d, n, m):
    return w(0x9BC07C00 | (m << 16) | (n << 5) | d)

def smulh_x(d, n, m):
    return w(0x9B407C00 | (m << 16) | (n << 5) | d)

def fmov_dx(d, n):
    """FMOV Dd, Xn -- raw bit move, so a double constant needs no literal pool"""
    return w(0x9E670000 | (n << 5) | d)

def fmul_d(d, n, m):
    return w(0x1E600800 | (m << 16) | (n << 5) | d)

def fcvtzs_x_d(d, n):
    return w(0x9E780000 | (n << 5) | d)

def ldrb_w(t, n, imm):
    assert 0 <= imm < 0x1000
    return w(0x39400000 | (imm << 10) | (n << 5) | t)

def strb_w(t, n, imm):
    assert 0 <= imm < 0x1000
    return w(0x39000000 | (imm << 10) | (n << 5) | t)

def ldr_w(t, n, imm):
    assert imm % 4 == 0 and 0 <= imm < 0x4000
    return w(0xB9400000 | ((imm // 4) << 10) | (n << 5) | t)

def cbz_w(t, pc, target):
    off = (target - pc) // 4
    assert -(1 << 18) <= off < (1 << 18)
    return w(0x34000000 | ((off & 0x7ffff) << 5) | t)

def b(pc, target):
    off = (target - pc) // 4
    assert -(1 << 25) <= off < (1 << 25)
    return w(0x14000000 | (off & 0x3ffffff))

def br(n):
    return w(0xD61F0000 | (n << 5))

def subs_x_imm(d, n, imm):
    assert 0 <= imm < 0x1000
    return w(0xF1000000 | (imm << 10) | (n << 5) | d)

def csel_x(d, n, m, cond):
    return w(0x9A800000 | (m << 16) | (cond << 12) | (n << 5) | d)

def cbz_x(t, pc, target):
    off = (target - pc) // 4
    assert -(1 << 18) <= off < (1 << 18)
    return w(0xB4000000 | ((off & 0x7ffff) << 5) | t)


def udiv_x(d, n, m):
    return w(0x9AC00800 | (m << 16) | (n << 5) | d)


def extr_x(d, n, m, lsb):
    """EXTR Xd, Xn, Xm, #lsb -- (Xn:Xm) >> lsb, i.e. a 128-bit funnel shift"""
    assert 0 <= lsb < 64
    return w(0x93C00000 | (m << 16) | (lsb << 10) | (n << 5) | d)
