"""Core-free host model of the project-internal DEV contract for MA_DIV16U (slot $43, Issue #478).

Contract (project definition, not a reference-BIOS ABI; see src/overrides/slot_43.s header): unsigned truncating
16-bit division. Divide by zero: quotient $FFFF, remainder = dividend, C=1, V=0. Otherwise C=0, V=0.
N and Z come from the quotient. The independent oracle is Python integer division (n // d, n % d).
"""


def expected(dividend, divisor):
    """Return (quotient, remainder, C, V, N, Z) as 16-bit values/flags for unsigned 16-bit inputs."""
    n, d = dividend & 0xFFFF, divisor & 0xFFFF
    if d == 0:
        q, r, c = 0xFFFF, n, 1
    else:
        q, r, c = n // d, n % d, 0
    return q, r, c, 0, q >> 15, int(q == 0)


def check_case(dividend, divisor, observed):
    """Mismatch strings for one case; observed = (quotient, remainder, C, V, N, Z)."""
    want = expected(dividend, divisor)
    names = ("quotient", "remainder", "C", "V", "N", "Z")
    return [f"{names[i]}: got {got:04x} want {exp:04x}"
            for i, (got, exp) in enumerate(zip(observed, want)) if got != exp]
