"""Fail-closed verdict checker for the synthetic BM_FORMAT model (Issue #303).

In-memory only: no file, device, or BIOS I/O. Synthetic results are NOT BIOS compatibility.
The BM_FORMAT ABI is UNKNOWN (#302; reference ABI VERIFIED 0/81), so the overall verdict is
never PASS: it is BLOCKED_ABI when every run case passed, FAIL on any failing case, and
NOT_RUN when no case ran. release_eligible is always False.

Owner decision A2 (#303, 2026-10-24): for synthetic images only, guarded_format()
preserves bm.format_image() recovery of an invalid signature when allow_destructive is
exactly True. Formatting returns a new image with the synthetic signature and zero-filled
remainder; the input is never changed. No real save, RAM, device or BIOS is accessed.
This is NOT evidence of the original System Card BM_FORMAT ABI or permission to format
real user data. Fail-closed verdict validation remains separate from this model behavior.
"""
import bm_format_model as bm

PASS, FAIL, NOT_RUN, BLOCKED_ABI = "PASS", "FAIL", "NOT_RUN", "BLOCKED_ABI"
# Only these statuses may be expected or observed; anything else (even a matching bogus value) is FAIL.
ALLOWED_STATUSES = frozenset({bm.OK, bm.REFUSED_UNCONFIRMED, bm.INSUFFICIENT_CAPACITY,
                              bm.ALREADY_FORMATTED})


def guarded_format(image, *, allow_destructive=False):
    """Delegate the synthetic-only A2 recovery policy to the model.

    No extra permission or real I/O. The model alone enforces exact-True
    destructive confirmation, capacity limits, and double-initialization refusal.
    Return a new bytes object on successful recovery; never mutate the input.
    """
    return bm.format_image(image, allow_destructive=allow_destructive)

def judge(expected, status, image, out):
    """Per-case verdict. Never raises: missing, unexpected, or non-conforming results are FAIL.

    OK must write the signature (classify == VALID) and zero-fill the body after it, and keep
    the capacity. Refused paths must return the input bytes unchanged.
    """
    try:
        # Strict type + allowlist validation to prevent Python type/coercion tricks
        if type(status) is not str or status not in ALLOWED_STATUSES:
            return FAIL
        # Output must be exactly bytes/bytearray: subclasses may override slicing and hide bytes.
        if type(out) not in (bytes, bytearray):
            return FAIL
        if expected is None:
            return NOT_RUN
        if type(expected) is not str or expected not in ALLOWED_STATUSES:
            return FAIL
        if status != expected:
            return FAIL
        if status != bm.OK:
            return PASS if bytes(out) == bytes(image) else FAIL
        # OK is only valid for an invalid-signature image: never overwrite a valid (possibly user) image.
        if bm.classify(bytes(image)) != bm.INVALID:
            return FAIL
        if len(out) != len(image) or bm.classify(out) != bm.VALID or any(out[len(bm.SIGNATURE):]):
            return FAIL
        return PASS
    except Exception:  # fail-closed: a judging error is a FAIL, never a PASS
        return FAIL


def _formatted(size=bm.MIN_CAPACITY):
    image = bytearray(size)
    image[:len(bm.SIGNATURE)] = bm.SIGNATURE
    return bytes(image)


def _corrupt():
    image = bytearray(_formatted())
    image[0] ^= 0xFF
    return bytes(image)


# (name, input image, allow_destructive, expected status)
CASES = [
    ("normal_blank_64", bytes(bm.MIN_CAPACITY), True, bm.OK),
    ("normal_blank_256", bytes(256), True, bm.OK),
    ("normal_bytearray_input_kept", bytearray(bm.MIN_CAPACITY), True, bm.OK),
    ("invalid_sig_one_bit_recovered", _corrupt(), True, bm.OK),
    ("invalid_sig_dirty_recovered", b"\xAA" * bm.MIN_CAPACITY, True, bm.OK),
    ("capacity_one_below_min", bytes(bm.MIN_CAPACITY - 1), True, bm.INSUFFICIENT_CAPACITY),
    ("capacity_empty", b"", True, bm.INSUFFICIENT_CAPACITY),
    ("capacity_one_below_min_valid_sig", _formatted(bm.MIN_CAPACITY - 1), True,
     bm.INSUFFICIENT_CAPACITY),
    ("double_init_refused", _formatted(), True, bm.ALREADY_FORMATTED),
    ("unconfirmed_refused", bytes(bm.MIN_CAPACITY), False, bm.REFUSED_UNCONFIRMED),
]


def run_case(name, image, allow, expected):
    try:
        snapshot = bytes(image)
    except Exception:  # fail-closed: an image that cannot be snapshotted is a FAIL
        return {"case": name, "verdict": FAIL, "status": None}
    try:
        result = guarded_format(image, allow_destructive=allow)
        if type(result) is not tuple or len(result) != 2:  # invalid return shape is never a success
            return {"case": name, "verdict": FAIL, "status": None}
        status, out = result
    except Exception:  # fail-closed: any exception is a FAIL, even when the expectation is None (NOT_RUN otherwise)
        return {"case": name, "verdict": FAIL, "status": None}
    # The input buffer must never be written, whatever the verdict (any writable buffer type).
    try:
        mutated = bytes(image) != snapshot
    except Exception:
        mutated = True
    if mutated:
        return {"case": name, "verdict": FAIL, "status": status}
    verdict = judge(expected, status, snapshot, out)
    if type(verdict) is not str or verdict not in (PASS, FAIL, NOT_RUN):
        verdict = FAIL
    return {"case": name, "verdict": verdict, "status": status}


def run_all(cases=CASES):
    results = [run_case(*case) for case in cases]
    ran = [r for r in results if r["verdict"] != NOT_RUN]
    if any(r["verdict"] == FAIL for r in results):
        overall = FAIL
    elif not ran:
        overall = NOT_RUN
    else:
        overall = BLOCKED_ABI  # synthetic passes cannot promote while the ABI is UNKNOWN
    # Literals, not module globals: no mutable flag can turn a report into an approval.
    return {"overall": overall, "abi": "UNKNOWN",
            "release_eligible": False, "cases": results}


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), ensure_ascii=False, indent=2))
