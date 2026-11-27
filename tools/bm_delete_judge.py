"""Issue #38: HOST MODEL of a BM_DELETE delete judge on a SYNTHETIC backup-RAM image. Pure stdlib.

This is NOT a BIOS routine and NOT the System Card backup-RAM format. The layout below is invented for this
model so the judge's branches (single delete, order preservation, not-found, unformatted, invalid input,
corrupt image, double delete, capacity boundary) can be exercised on the host. The BM_DELETE call ABI is
UNKNOWN (docs/38-bm-delete-contract.md), so the API stays BLOCKED_ABI and release_eligible=false. The module
does no file I/O at all: it only reads the structures it is handed, so it cannot touch a real user save.

Synthetic model (PROJECT-INVENTED, never a System Card claim):
  MockBram = {entries: list[{name: bytes(12), data: bytes}], formatted: bool, capacity: int, end: int}.
  end is the PROJECT-INVENTED terminator (usage). Entry order is the on-media order.
  MOCK_ENTRY_OVERHEAD = 18 (0x10 header + 0x02 terminator arithmetic borrowed from the HuC direct
  implementation as a mock-internal constant; NOT a BIOS value).
  mock_used(image) = sum(18 + len(data) for each entry).
  mock_free(image) = capacity - end (mock-internal arithmetic; NOT the BIOS BM_FREE value).

  Layers (separate types, never mixed):
    MockStatus: the mock_delete return domain {OK, NOT_FOUND, NOT_FORMATTED}.
      CALLER-EXPECTED-derived PROJECT-INVENTED choice; NOT a genuine BIOS return-set claim.
    HarnessOutcome: the judge-layer result domain {REFUSED, CORRUPTED} for input-validation
      and audit failures. PROJECT-INVENTED; never a MockStatus value.
  Precedence: CORRUPTED wins over REFUSED. The judge runs the structure gate and the CLEAN
  audit BEFORE validate_input, so a doubly-bad call (bad name + bad image) returns CORRUPTED.
  Un-snapshotable images (gate failure) return the empty-image convention; snapshot-able
  abnormal images return a byte-identical snapshot. Abnormal paths never call mock_delete.
    judge(image, name): structure gate -> audit_image -> validate_input -> mock_delete.
"""

OK, NOT_FOUND, NOT_FORMATTED = "OK", "NOT_FOUND", "NOT_FORMATTED"
REFUSED, CORRUPTED = "REFUSED", "CORRUPTED"

MOCK_ENTRY_OVERHEAD = 18


def mock_used(entries):
    """PROJECT-INVENTED usage arithmetic over an entry list."""
    return sum(MOCK_ENTRY_OVERHEAD + len(e["data"]) for e in entries)


def make_image(entries=(), formatted=True, capacity=2048):
    """Build a synthetic clean image. entries is an iterable of (name, data); name must be 12 bytes."""
    lst = []
    for name, data in entries:
        if not isinstance(name, (bytes, bytearray)) or len(name) != 12:
            raise ValueError("mock entry name must be 12 bytes")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("mock entry data must be bytes")
        lst.append({"name": bytes(name), "data": bytes(data)})
    names = [e["name"] for e in lst]
    if len(set(names)) != len(names):
        raise ValueError("duplicate mock entry name")
    if not isinstance(capacity, int) or capacity <= 0:
        raise ValueError("capacity must be a positive int")
    end = mock_used(lst)
    if end > capacity:
        raise ValueError(f"mock image over capacity: used {end} > {capacity}")
    return {"entries": lst, "formatted": bool(formatted), "capacity": capacity, "end": end}


def _empty_image():
    """Fresh empty-image convention for un-snapshotable inputs. New dict per call (no aliasing)."""
    return {"entries": [], "formatted": False, "capacity": 0, "end": 0}


def _is_snapshotable(image):
    """Structure gate: True iff _snapshot(image) cannot fail. Explicit checks only (no try/except).

    Covers isinstance-dict, required keys, entries-is-list, and each entry being a dict
    with bytes/bytearray name+data. Scalar sanity (formatted/capacity/end types, name length,
    duplicates, end consistency) is snapshot-safe and left to _clean_errors, which runs before
    any _snapshot call. The gate never mutates its input and performs no I/O.
    """
    if not isinstance(image, dict):
        return False
    if "entries" not in image or "formatted" not in image or "capacity" not in image or "end" not in image:
        return False
    if not isinstance(image["entries"], list):
        return False
    for entry in image["entries"]:
        if not isinstance(entry, dict):
            return False
        if "name" not in entry or "data" not in entry:
            return False
        if not isinstance(entry["name"], (bytes, bytearray)):
            return False
        if not isinstance(entry["data"], (bytes, bytearray)):
            return False
    return True


def _snapshot(image):
    """Deep-copy a mock image so the judge never aliases caller state."""
    return {
        "entries": [{"name": bytes(e["name"]), "data": bytes(e["data"])} for e in image["entries"]],
        "formatted": image["formatted"],
        "capacity": image["capacity"],
        "end": image["end"],
    }


def _clean_errors(image):
    """Return a reason string when the PROJECT-INVENTED CLEAN invariants fail, else None."""
    if not isinstance(image, dict):
        return "image must be a dict"
    for key in ("entries", "formatted", "capacity", "end"):
        if key not in image:
            return f"missing key {key}"
    if not isinstance(image["formatted"], bool):
        return "formatted must be bool"
    if not isinstance(image["capacity"], int) or image["capacity"] <= 0:
        return "capacity must be a positive int"
    if not isinstance(image["end"], int) or not 0 <= image["end"] <= image["capacity"]:
        return "end out of range"
    if not isinstance(image["entries"], list):
        return "entries must be a list"
    names = []
    for entry in image["entries"]:
        if not isinstance(entry, dict) or "name" not in entry or "data" not in entry:
            return "entry must be {name, data}"
        if not isinstance(entry["name"], (bytes, bytearray)) or len(entry["name"]) != 12:
            return "entry name must be 12 bytes"
        if not isinstance(entry["data"], (bytes, bytearray)):
            return "entry data must be bytes"
        names.append(bytes(entry["name"]))
    if len(set(names)) != len(names):
        return "duplicate entry name"
    if image["end"] != mock_used(image["entries"]):
        return "end != mock_used(entries)"
    return None


def validate_input(name):
    """Input layer: name must be bytes of exactly length 12, else REFUSED (mock_delete is not called)."""
    if not isinstance(name, (bytes, bytearray)) or len(name) != 12:
        return False
    return True


def mock_free(image):
    """PROJECT-INVENTED free-space arithmetic (mock internal; NOT a BIOS BM_FREE value)."""
    return image["capacity"] - image["end"]


def mock_add(image, name, data):
    """PROJECT-INVENTED preparation op: append (name, data); capacity/duplicate overflow is a test error."""
    image = _snapshot(image)
    if not validate_input(name):
        raise ValueError("mock_add name must be 12 bytes")
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("mock_add data must be bytes")
    blob = bytes(name)
    if any(bytes(e["name"]) == blob for e in image["entries"]):
        raise ValueError("mock_add duplicate name")
    image["entries"].append({"name": blob, "data": bytes(data)})
    image["end"] = mock_used(image["entries"])
    if image["end"] > image["capacity"]:
        raise ValueError("mock_add over capacity")
    return image


def mock_delete(image, name):
    """PROJECT-INVENTED delete on a CLEAN image. Returns (MockStatus, new_image); input image never mutated.

    Callers must run validate_input and _clean_errors first (judge does so); this function re-checks
    fail-closed and returns HarnessOutcome-layer values only via judge, never as MockStatus.
    """
    before = _snapshot(image)
    if not validate_input(name):
        raise ValueError("mock_delete refused: name must be 12 bytes")
    err = _clean_errors(image)
    if err is not None:
        raise ValueError(f"mock_delete refused on non-clean image: {err}")
    if not image["formatted"]:
        return NOT_FORMATTED, before
    blob = bytes(name)
    hits = [i for i, e in enumerate(image["entries"]) if bytes(e["name"]) == blob]
    if not hits:
        return NOT_FOUND, before
    out = _snapshot(image)
    del out["entries"][hits[0]]
    out["end"] = mock_used(out["entries"])
    return OK, out


def judge(image, name):
    """Full harness: (outcome, image). CORRUPTED wins over REFUSED on doubly-bad calls.

    Order: structure gate -> CLEAN audit -> input validation -> mock_delete domain.
    Un-snapshotable images return (CORRUPTED, empty-image convention). Snapshot-able
    abnormal images return (outcome, _snapshot(image)) byte-identical to the input.
    Abnormal paths never call mock_delete; the returned dicts are always new objects.
    """
    if not _is_snapshotable(image):
        return CORRUPTED, _empty_image()
    err = _clean_errors(image)
    if err is not None:
        return CORRUPTED, _snapshot(image)
    if not validate_input(name):
        return REFUSED, _snapshot(image)
    if not image["formatted"]:
        return NOT_FORMATTED, _snapshot(image)
    blob = bytes(name)
    if not any(bytes(e["name"]) == blob for e in image["entries"]):
        return NOT_FOUND, _snapshot(image)
    return mock_delete(image, blob)


def _images_equal(a, b):
    """Byte-identity of the abstract image (names, data, order, formatted, capacity, end)."""
    if a["formatted"] != b["formatted"] or a["capacity"] != b["capacity"] or a["end"] != b["end"]:
        return False
    if len(a["entries"]) != len(b["entries"]):
        return False
    return all(
        bytes(x["name"]) == bytes(y["name"]) and bytes(x["data"]) == bytes(y["data"])
        for x, y in zip(a["entries"], b["entries"])
    )


def mutation_self_check(base_image, base_name):
    """Flip every bit of every name byte; none may come back OK. Returns flip count.

    The single-entry base image holds exactly one name, so any single-bit flip of that name
    addresses a different (absent) entry and must yield NOT_FOUND with a byte-identical image.
    """
    checked = 0
    for idx in range(12):
        for bit in range(8):
            broken = bytearray(base_name)
            broken[idx] ^= 1 << bit
            outcome, out = judge(base_image, bytes(broken))
            if outcome == OK:
                raise ValueError(f"name bit flip at byte {idx} bit {bit} accepted as OK")
            if not _images_equal(out, base_image):
                raise ValueError(f"name bit flip at byte {idx} bit {bit} mutated the image")
            checked += 1
    return checked


def main():
    image = make_image(entries=[(b"TESTFILE    ", b"hello")])
    outcome, out = judge(image, b"TESTFILE    ")
    if outcome != OK or len(out["entries"]) != 0:
        raise ValueError(f"fresh delete not OK: {outcome}")
    flips = mutation_self_check(make_image(entries=[(b"TESTFILE    ", b"hello")]), b"TESTFILE    ")
    print(f"HOST MODEL (not a BIOS routine): fresh OK, 0 entries left; {flips} name bit flips rejected")
    print(f"MISSING {judge(image, b'NO SUCH FILE')[0]}, UNFORMATTED {judge(make_image(entries=[], formatted=False), b'TESTFILE    ')[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
