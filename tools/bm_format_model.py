"""Core-free, non-executing FORMAT safety model for a synthetic backup-RAM image (Issue #303).

Scope: operates only on in-memory bytes. It performs no file, device, or process I/O and
never calls a BIOS routine. The signature below is a project-internal synthetic marker,
NOT the System Card backup-RAM format. The BM_FORMAT ABI (entry, arguments, confirm string,
return codes) is unconfirmed (#302), so the API stays BLOCKED_ABI and release_eligible=false.

Fail-closed order of checks in format_image():
  1. no explicit destructive permission -> REFUSED_UNCONFIRMED (image unchanged)
  2. image shorter than min_capacity    -> INSUFFICIENT_CAPACITY (image unchanged)
  3. valid signature present            -> ALREADY_FORMATTED (double initialization refused)
  4. otherwise (blank or invalid sig)   -> OK, returns a new formatted image
"""

SIGNATURE = b"SYNBM\x01\x00\x00"  # synthetic 8-byte marker, not a System Card value
HEADER_LEN = 16                   # signature + reserved bytes
MIN_CAPACITY = 64                 # synthetic minimum image size (header + 48 body bytes)

OK = "OK"
REFUSED_UNCONFIRMED = "REFUSED_UNCONFIRMED"
INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
ALREADY_FORMATTED = "ALREADY_FORMATTED"

VALID = "VALID_SIGNATURE"
INVALID = "INVALID_SIGNATURE"
SHORT = "INSUFFICIENT_CAPACITY"


def classify(image):
    """Classify a synthetic image without modifying it."""
    if len(image) < MIN_CAPACITY:
        return SHORT
    return VALID if bytes(image[:len(SIGNATURE)]) == SIGNATURE else INVALID


def format_image(image, *, allow_destructive=False):
    """Return (status, image). On any status other than OK the returned image equals the input."""
    if not isinstance(image, (bytes, bytearray)):
        raise TypeError("synthetic image must be bytes or bytearray")
    original = bytes(image)
    if allow_destructive is not True:
        return REFUSED_UNCONFIRMED, original
    if len(original) < MIN_CAPACITY:
        return INSUFFICIENT_CAPACITY, original
    if classify(original) == VALID:
        return ALREADY_FORMATTED, original
    formatted = bytearray(len(original))
    formatted[:len(SIGNATURE)] = SIGNATURE
    return OK, bytes(formatted)
