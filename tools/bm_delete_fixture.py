"""Issue #38: READ-ONLY synthetic fixtures for the BM_DELETE delete judge (tools/bm_delete_judge.py).

Pure stdlib. SYNTHETIC bytes only. No file is opened, no real backup RAM or user save is read or written.
ReadOnlyImage is an immutable snapshot: every write path raises, so a judge run cannot alter the image it
was given. Not a BIOS routine; the BM_DELETE call ABI is UNKNOWN (docs/38-bm-delete-contract.md), so the
API stays BLOCKED_ABI and release_eligible=false.

  python3 tools/bm_delete_fixture.py   # prints the judged outcome of each synthetic scenario
"""
import bm_delete_judge as bj

# Hand-built 12-byte names (first two bytes are the caller-side uniqueness field; the mock compares
# all 12 bytes exactly and never treats the prefix specially beyond what the bytes say).
N_A = b"\x00\x00FILE_A    "
N_B = b"\x00\x00FILE_B    "
N_C = b"\x00\x00FILE_C    "
N_MISSING = b"\x00\x00NOPE      "
N_EMPTY_FILE = b"\x00\x00EMPTY     "


class ReadOnlyImage:
    """Immutable snapshot of a synthetic mock image. The caller's structure is deep-copied, never aliased."""

    __slots__ = ("_data",)

    def __init__(self, image):
        if not isinstance(image, dict):
            raise TypeError("image must be a mock-image dict")
        self._data = {
            "entries": [{"name": bytes(e["name"]), "data": bytes(e["data"])} for e in image["entries"]],
            "formatted": image["formatted"],
            "capacity": image["capacity"],
            "end": image["end"],
        }

    def to_image(self):
        return {
            "entries": [{"name": bytes(e["name"]), "data": bytes(e["data"])} for e in self._data["entries"]],
            "formatted": self._data["formatted"],
            "capacity": self._data["capacity"],
            "end": self._data["end"],
        }

    def __setitem__(self, key, value):
        raise PermissionError("synthetic fixture is read-only")

    def __delitem__(self, key):
        raise PermissionError("synthetic fixture is read-only")


def scenarios():
    """Name -> (fixture, target name, expected outcome). Positive, negative and boundary images."""
    one = ReadOnlyImage(bj.make_image(entries=[(N_A, b"hello")]))
    two = ReadOnlyImage(bj.make_image(entries=[(N_A, b"a"), (N_B, b"bb"), (N_C, b"ccc")]))
    unformatted = ReadOnlyImage(bj.make_image(entries=[(N_A, b"hello")], formatted=False))
    empty = ReadOnlyImage(bj.make_image(entries=[]))
    empty_file = ReadOnlyImage(bj.make_image(entries=[(N_EMPTY_FILE, b"")]))
    full = ReadOnlyImage(bj.make_image(entries=[(N_A, b"x" * (2048 - 18))]))
    padded = ReadOnlyImage(bj.make_image(entries=[(b"\x00\x00PAD       ", b"d")]))
    nul_name = ReadOnlyImage(bj.make_image(entries=[(b"\x00\x00A\x00B       ", b"d")]))
    case_pair = ReadOnlyImage(bj.make_image(entries=[(b"\x00\x00FOO       ", b"upper"), (b"\x00\x00foo       ", b"lower")]))
    nonzero_uniq = ReadOnlyImage(bj.make_image(entries=[(b"\x01\x02FILE_A    ", b"d")]))
    return {
        # positive: single / head / middle / tail deletes preserve survivors in order
        "positive: delete sole entry": (one, N_A, bj.OK),
        "positive: delete head entry": (two, N_A, bj.OK),
        "positive: delete middle entry": (two, N_B, bj.OK),
        "positive: delete tail entry": (two, N_C, bj.OK),
        "positive: delete empty-data entry (mock OK; genuine FILE_EMPTY handling stays UNKNOWN)": (
            empty_file, N_EMPTY_FILE, bj.OK),
        # negative: missing / unformatted / empty image
        "negative: missing name": (two, N_MISSING, bj.NOT_FOUND),
        "negative: unformatted image": (unformatted, N_A, bj.NOT_FORMATTED),
        "negative: empty formatted image": (empty, N_A, bj.NOT_FOUND),
        # negative: input layer refusals (judge never calls mock_delete)
        "negative: short name refused": (two, b"short", bj.REFUSED),
        "negative: long name refused": (two, b"this name is too long", bj.REFUSED),
        "negative: empty name refused": (two, b"", bj.REFUSED),
        "negative: non-bytes name refused": (two, "FILE_A", bj.REFUSED),
        "negative: none name refused": (two, None, bj.REFUSED),
        # negative: corrupt images (audit layer, mock_delete never called)
        "negative: end mismatch corrupt": (_corrupt_end(two), N_A, bj.CORRUPTED),
        "negative: duplicate name corrupt": (_corrupt_dup(), N_A, bj.CORRUPTED),
        "negative: bad name length corrupt": (_corrupt_name_len(), N_A, bj.CORRUPTED),
        "negative: end over capacity corrupt": (_corrupt_over(), N_A, bj.CORRUPTED),
        # boundary: exact-12 accepted; near-miss lengths refused; padding/NUL/case/uniq exact-match
        "boundary: exactly 12 bytes accepted": (padded, b"\x00\x00PAD       ", bj.OK),
        "boundary: 11 bytes refused": (padded, b"\x00\x00PAD      ", bj.REFUSED),
        "boundary: 13 bytes refused": (padded, b"\x00\x00PAD        ", bj.REFUSED),
        "boundary: NUL-containing name exact-matches": (nul_name, b"\x00\x00A\x00B       ", bj.OK),
        "boundary: case differs is a different name": (case_pair, b"\x00\x00FOO       ", bj.OK),
        "boundary: nonzero uniqueness prefix is a different name": (nonzero_uniq, b"\x01\x02FILE_A    ", bj.OK),
        "boundary: zero-prefix does not match nonzero stored": (nonzero_uniq, b"\x00\x00FILE_A    ", bj.NOT_FOUND),
        "boundary: delete tail of full image": (full, N_A, bj.OK),
    }


def _corrupt_end(fixture):
    img = fixture.to_image()
    img["end"] = img["end"] + 1 if img["end"] + 1 <= img["capacity"] else img["end"] - 1
    if img["end"] == bj.mock_used(img["entries"]):
        img["capacity"] = img["capacity"] + 64
    return ReadOnlyImage(img)


def _corrupt_dup():
    return ReadOnlyImage({
        "entries": [{"name": N_A, "data": b"a"}, {"name": N_A, "data": b"b"}],
        "formatted": True,
        "capacity": 2048,
        "end": 18 + 1 + 18 + 1,
    })


def _corrupt_name_len():
    return ReadOnlyImage({
        "entries": [{"name": b"short", "data": b"a"}],
        "formatted": True,
        "capacity": 2048,
        "end": 18 + 1,
    })


def _corrupt_over():
    return ReadOnlyImage({
        "entries": [{"name": N_A, "data": b"a"}],
        "formatted": True,
        "capacity": 2048,
        "end": 2049,
    })


def main():
    for name, (fixture, target, expected) in scenarios().items():
        outcome, _ = bj.judge(fixture.to_image(), target)
        mark = "ok" if outcome == expected else "MISMATCH"
        print(f"{mark}: {name} -> {outcome} (expected {expected})")
        if outcome != expected:
            raise SystemExit(f"fixture {name}: got {outcome}, expected {expected}")
    print(f"fixtures GREEN: {len(scenarios())} scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
