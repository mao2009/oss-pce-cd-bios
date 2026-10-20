"""Host checks for the project-own EX_BINBCD packed-BCD model and probe build (Issue #70)."""
import tempfile
import unittest
from tools.environment import requires_cc65

from tools.ex_binbcd import binbcd_packed, build_probe


class ExBinBcdPackedModelTests(unittest.TestCase):
    def test_boundary_vectors(self):
        # (input, packed A, C): 0/9/10/99 in range; 100 and the 8-bit maximum 255 out of range.
        for value, packed, carry in ((0, 0x00, 0), (9, 0x09, 0), (10, 0x10, 0), (99, 0x99, 0),
                                     (100, 100, 1), (255, 255, 1)):
            self.assertEqual(binbcd_packed(value), (packed, carry), value)

    def test_packed_is_one_byte_not_unpacked_digits(self):
        # Unpacked BCD of 10 would be two bytes 01 00; packed is the single byte $10.
        self.assertEqual(binbcd_packed(10)[0], 0x10)
        self.assertNotEqual(binbcd_packed(10)[0], 0x01)
        for value in range(100):
            packed, _ = binbcd_packed(value)
            self.assertEqual(((packed >> 4) * 10) + (packed & 0x0F), value)
            self.assertLessEqual(max(packed >> 4, packed & 0x0F), 9)

    def test_rejects_non_byte_input(self):
        for value in (-1, 256):
            with self.assertRaises(ValueError):
                binbcd_packed(value)

    @requires_cc65
    def test_probe_assembles_with_exported_mutation_point(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, symbols = build_probe(directory)
            data = rom.read_bytes()
        offset = symbols["ex_binbcd_tens_step"] - 0xE000
        self.assertEqual(data[offset:offset + 2], b"\x69\x10")  # ADC #$10
        start = symbols["ex_binbcd_packed"] - 0xE000
        self.assertEqual(data[start:start + 2], b"\xc9\x64")  # CMP #100: >=100 is out of range
        self.assertEqual(data[0x1FFE:], b"\x00\xe0")  # reset vector -> probe_reset


if __name__ == "__main__":
    unittest.main()
