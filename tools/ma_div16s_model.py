"""Core-free host model and judge of the project-internal MA_DIV16S contract (Issue #76).

Contract (project definition, not a reference-BIOS ABI; see src/math/ma_div16s.s header):
truncating signed 16-bit division. Divide by zero: C=1, quotient 0, remainder = dividend.
-32768 / -1 overflow: V=1, quotient $8000. Remainder sign follows the dividend.
"""


def signed(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def expected(dividend, divisor):
    """Return (quotient, remainder, C, V, N, Z) as 16-bit values/flags for unsigned-encoded inputs."""
    a, b = signed(dividend), signed(divisor)
    if b == 0:
        q, r, c, v = 0, a, 1, 0
    else:
        magnitude = abs(a) // abs(b)
        q = -magnitude if (a < 0) != (b < 0) else magnitude
        r = a - q * b
        c = 0
        v = 1 if q == 32768 else 0
    q16 = q & 0xFFFF
    return q16, r & 0xFFFF, c, v, q16 >> 15, int(q16 == 0)


def check_case(dividend, divisor, observed):
    """Mismatch strings for one case; observed = (quotient, remainder, C, V, N, Z)."""
    want = expected(dividend, divisor)
    names = ("quotient", "remainder", "C", "V", "N", "Z")
    return [f"{names[i]}: got {got:04x} want {exp:04x}"
            for i, (got, exp) in enumerate(zip(observed, want)) if got != exp]
