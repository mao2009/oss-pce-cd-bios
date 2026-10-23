"""Issue #306: READ-ONLY synthetic fixture for the BM_FREE judge (tools/buram_free_judge.py). Pure stdlib.

SYNTHETIC bytes only. No file is opened, no real backup RAM or user save is read or written. ReadOnlyImage is an
immutable snapshot: every write path raises, so a judge run cannot alter the image it was given. Not a BIOS routine;
the BM_FREE call ABI is UNKNOWN (docs/305-bm-free-contract.md), so the API stays BLOCKED_ABI and release_eligible=false.

  python3 tools/bm_free_fixture.py   # prints the judged verdict of each synthetic scenario
"""
import buram_free_judge as bj

# Literal frames, not built by bj.make_image or bj._checksum. Header sum 0x53+0x42+0x52+0x31 = 0x118, so a version-1
# frame with an empty table has checksum 0x19; each used block adds 1, each table value 0x02 adds 2.
# A checksum change in the judge therefore flips these scenarios to a non-OK verdict.
def _frame(checksum, table=bytes(15), version=1):
    return b'SBR1' + bytes([version]) + bytes(10) + bytes([checksum]) + bytes(table) + bytes(2017)  # 2048 - 31 header and table bytes


class ReadOnlyImage:
    """Immutable snapshot of a synthetic image. The caller's buffer is copied, never aliased."""

    __slots__ = ('_data',)

    def __init__(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("image must be bytes or bytearray")
        self._data = bytes(data)

    def __bytes__(self):
        return self._data

    def __len__(self):
        return len(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > len(self._data):
            raise IndexError(f"read {offset}+{length} outside {len(self._data)} bytes")
        return self._data[offset:offset + length]

    def __setitem__(self, key, value):
        raise PermissionError("synthetic fixture is read-only")

    def __delitem__(self, key):
        raise PermissionError("synthetic fixture is read-only")


def scenarios():
    """Name -> (fixture, expected_verdict). Positive, negative (uninitialized, invalid) and boundary images."""
    fresh = _frame(0x19)
    used_0_7 = _frame(0x1B, bytes([1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]))
    table_two = _frame(0x1B, bytes([2]) + bytes(14))
    version_two = _frame(0x1A, version=2)
    return {
        'positive: normal header, all free': (ReadOnlyImage(fresh), bj.OK),
        'positive: normal header, some used': (ReadOnlyImage(used_0_7), bj.OK),
        'negative: uninitialized (erased FF)': (ReadOnlyImage(bj.ERASED), bj.UNFORMATTED),
        'negative: uninitialized (blank 00)': (ReadOnlyImage(bj.BLANK), bj.UNFORMATTED),
        'negative: invalid magic': (ReadOnlyImage(b'XXXX' + fresh[4:]), bj.INVALID_HEADER),
        'negative: invalid version': (ReadOnlyImage(version_two), bj.INVALID_HEADER),
        'negative: table value 0x02': (ReadOnlyImage(table_two), bj.INVALID_HEADER),
        'negative: wrong size 2047': (ReadOnlyImage(fresh[:-1]), bj.BAD_SIZE),
    }


def main():
    for name, (image, expected) in scenarios().items():
        verdict, free, reason = bj.judge_free(bytes(image))
        print(f"{verdict:16} free={free!s:5} {name} ({reason})")
        if verdict != expected:
            raise ValueError(f"{name}: expected {expected}, got {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
