"""Core-free host model and judge of the project-internal MA_MUL8S contract (Issue #74; docs/ma-mul8s.md)."""


def signed(value):
    return value - 256 if value & 0x80 else value


def expected(a, b):
    """Project contract: X:A = two's-complement 16-bit product of signed a and b."""
    return (signed(a) * signed(b)) & 0xFFFF


def expected_xa(a, b):
    """(X, A) = (high, low) bytes of expected(a, b)."""
    product = expected(a, b)
    return product >> 8, product & 0xFF


def expected_row(a):
    """Bytes the driver must leave at $2200-$27FF for request a (P pages are checked separately)."""
    products = [expected(a, b) for b in range(256)]
    return (bytes(p & 0xFF for p in products) + bytes(p >> 8 for p in products)
            + bytes(b ^ 0x5A for b in range(256)) + b"\xfd" * 256)


def check_row(a, data):
    """Mismatch strings for one row: product, Y, S, and P preserved (with the C/D inputs varied)."""
    errors = []
    want = expected_row(a)
    for b in range(256):
        got = [data[page * 256 + b] for page in range(6)]
        exp = [want[page * 256 + b] for page in range(4)]
        if got[:4] != exp:
            errors.append(f"a={a:02x} b={b:02x} lo/hi/Y/S={bytes(got[:4]).hex()} expected {bytes(exp).hex()}")
        if got[4] != got[5] or (got[5] & 0x09) != ((b & 1) | (b & 2) << 2):  # C=bit0, D=bit1 of b
            errors.append(f"a={a:02x} b={b:02x} P before {got[5]:02x} after {got[4]:02x}")
    return errors
