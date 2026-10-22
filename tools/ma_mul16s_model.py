"""Core-free host oracle of the project-internal MA_MUL16S DEV contract (Issue #479; docs/ma-mul16s.md).

Two independent computations: expected() uses Python integer multiplication and is the oracle the real-core test
(tests/integration/geargrafx_dev_mul16s.py, Issue #494) compares the assembled src/overrides/slot_4F.s against;
shift_add() mirrors the algorithm at 16-bit-word level (byte carries are modeled by tests/host/test_ma_mul16s_dev.py).
Not the reference System Card MA_MUL16S ABI (UNVERIFIED).
"""

P_B, P_T = 0x10, 0x20  # PHP pushes B=1 and PLP does not store it; T must be 0 on return


def signed16(v):
    return v - 0x10000 if v & 0x8000 else v


def expected(a, b):
    """Contract: 32-bit two's-complement product of signed 16-bit a, b (range -1073709056..1073741824, no overflow)."""
    return (signed16(a & 0xFFFF) * signed16(b & 0xFFFF)) & 0xFFFFFFFF


def frame(product):
    """Four stack bytes in push order (first pushed = bits 0-7 ... fourth pushed = bits 24-31)."""
    return tuple((product >> (8 * i)) & 0xFF for i in range(4))


def shift_add(a, b, skip_a_fix=False, skip_b_fix=False):
    """Unsigned 16x16 shift-add, then the mod 2^16 sign correction on the high word:
    a<0 -> high -= b, b<0 -> high -= a. The skip flags build negative-control mutants."""
    a, b = a & 0xFFFF, b & 0xFFFF
    high, low = 0, b
    for _ in range(16):
        if low & 1:
            high += a
        low = (low >> 1) | ((high & 1) << 15)
        high >>= 1
    if a & 0x8000 and not skip_a_fix:
        high -= b
    if b & 0x8000 and not skip_b_fix:
        high -= a
    return ((high & 0xFFFF) << 16) | low


def check_case(a, b, got_frame, y_in, y_out, s_in, s_out, p_in, p_out):
    """Mismatch strings for one call. p_in/p_out compare every bit except B; T must be 0 on return.
    p_in must have T=0 (T=1 is out of contract)."""
    errors = []
    want = frame(expected(a, b))
    if tuple(got_frame) != want:
        errors.append(f"a={a:04x} b={b:04x} frame {bytes(got_frame).hex()} expected {bytes(want).hex()}")
    if y_out != y_in:
        errors.append(f"a={a:04x} b={b:04x} Y {y_in:02x}->{y_out:02x}")
    if s_out != s_in:
        errors.append(f"a={a:04x} b={b:04x} S {s_in:02x}->{s_out:02x}")
    if p_in & P_T:
        errors.append(f"a={a:04x} b={b:04x} caller T=1 is out of contract")
    elif (p_in ^ p_out) & ~P_B & 0xFF or p_out & P_T:
        errors.append(f"a={a:04x} b={b:04x} P {p_in:02x}->{p_out:02x}")
    return errors
