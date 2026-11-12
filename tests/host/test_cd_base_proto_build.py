"""Host tests for the experimental slot-$02 patch helper.

Proves the 5-byte fallback->JMP replacement is fail-closed and never touches
the normal ROM (normal build does not link the proto; only the experimental
builder patches its own copy).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from build_experimental_bios_boot import _patch_slot02_to_proto


def _bank(slot02=0xE10A, unimpl=0xE245, proto=0xF000):
    bank = bytearray(b"\xFF" * 8192)
    off = slot02 - 0xE000
    bank[off:off + 5] = bytes((0xA2, 0x02, 0x4C, unimpl & 0xFF, (unimpl >> 8) & 0xFF))
    return bytes(bank)


class PatchTests(unittest.TestCase):
    def test_patch_replaces_exactly_five_bytes(self):
        bank = _bank()
        patched = _patch_slot02_to_proto(bank, 0xE10A, 0xE245, 0xF000)
        off = 0xE10A - 0xE000
        self.assertEqual(patched[off:off + 5], bytes((0x4C, 0x00, 0xF0, 0xEA, 0xEA)))
        diffs = [i for i in range(len(bank)) if bank[i] != patched[i]]
        self.assertEqual(diffs, list(range(off, off + 5)))

    def test_patch_refuses_wrong_prebytes(self):
        bank = bytearray(_bank())
        bank[0x10A] = 0x00
        with self.assertRaises(ValueError):
            _patch_slot02_to_proto(bytes(bank), 0xE10A, 0xE245, 0xF000)

    def test_patch_refuses_bad_addresses(self):
        bank = _bank()
        with self.assertRaises(ValueError):
            _patch_slot02_to_proto(bank, 0xD000, 0xE245, 0xF000)
        with self.assertRaises(ValueError):
            _patch_slot02_to_proto(bank, 0xE10A, 0xE245, 0xE050)
        with self.assertRaises(ValueError):
            _patch_slot02_to_proto(bank, 0xE10A, 0x1000, 0xF000)


if __name__ == "__main__":
    unittest.main()
