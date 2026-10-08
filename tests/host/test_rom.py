"""Structure/content separation and symbol-based negative fixture generation."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from build import RESET, verify_fixture
from rom import Placement, validate_rom_structure
from geargrafx_smoke import missing_marker_rom


class RomTests(unittest.TestCase):
    def structure(self, data, **changes):
        args = {'size': 8192, 'bank_size': 8192, 'cpu_base': 0xe000,
                'placements': (Placement('code', 0, 32, 0xe000),)}
        args.update(changes)
        return validate_rom_structure(data, **args)

    def test_structure_is_not_a_content_or_bios_validity_claim(self):
        self.structure(bytes(8192))
        with self.assertRaisesRegex(ValueError, 'fixture layout'):
            verify_fixture(bytes(8192))

    def test_truncated_headered_and_wrong_size_images_fail_structure(self):
        for size in (0, 8191, 8193, 8192 + 512):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.structure(bytes(size))

    def test_invalid_format_bank_size_and_cpu_window_fail(self):
        for changes in ({'image_format': 'system-card'}, {'bank_size': 0}, {'bank_size': 3},
                        {'size': -1}, {'size': 8193}, {'cpu_base': -1}, {'cpu_base': 0xf000}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.structure(bytes(8192), **changes)

    def test_overlap_outside_bank_duplicate_or_wrong_cpu_address_fail(self):
        for placements in ((Placement('code', 8190, 8, 0xfffe),),
                           (Placement('code', 0, 32, 0xe001),),
                           (Placement('code', 0, 32, 0xe000), Placement('data', 16, 4, 0xe010)),
                           (Placement('same', 0, 1, 0xe000), Placement('same', 32, 1, 0xe020)),
                           (Placement('empty', 0, 0, 0xe000),),
                           (Placement('negative', -1, 1, 0xdfff),)):
            with self.subTest(placements=placements), self.assertRaises(ValueError):
                self.structure(bytes(8192), placements=placements)
        with self.assertRaises(ValueError):
            self.structure(bytes(16384), size=16384, placements=(Placement('crossing', 8190, 4, 0xfffe),))

    def test_fixture_strict_regression_and_corrupt_content(self):
        data = bytearray(b'\xff' * 8192)
        data[:len(RESET)] = RESET
        data[0x30:0x32] = b'\x80\xfe'
        data[0x40:0x44] = b'PCE!'
        data[-10:] = struct.pack('<5H', 0xe030, 0xe030, 0xe030, 0xe030, 0xe000)
        self.assertEqual(verify_fixture(data), '8d659d6ffe040a10cde4fc53ae833f7705fa2526d254978783598def6072f64c')
        for offset in (0, 0x30, 0x40, 0x100, 0x1ffe):
            damaged = bytearray(data)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                verify_fixture(damaged)

    def transfer(self, offset=90):
        data = bytearray(8192)
        data[offset:offset + 7] = b'\x73' + struct.pack('<HHH', 0xe040, 0x2200, 4)
        labels = f'al 00E000 .reset\nal 00E040 .marker\nal {0xe000 + offset:06X} .marker_transfer\n'
        return data, labels

    def test_transfer_mutation_follows_symbol_instead_of_fixed_offset(self):
        for offset in (14, 90):
            data, labels = self.transfer(offset)
            mutated = missing_marker_rom(data, labels)
            self.assertEqual(mutated[offset:offset + 7], b'\xea' * 7)
            self.assertEqual(mutated[:offset], data[:offset])
            self.assertEqual(mutated[offset + 7:], data[offset + 7:])

    def test_wrong_transfer_opcode_or_operands_fail(self):
        for delta in range(7):
            data, labels = self.transfer()
            data[90 + delta] ^= 1
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, 'expected TII'):
                missing_marker_rom(data, labels)

    def test_missing_conflicting_or_outside_symbols_fail(self):
        data, labels = self.transfer()
        for changed in ('', labels + 'al 00EFFF .marker_transfer\n',
                        labels.replace('00E05A', '00D000'), labels.replace('00E000', '00E001')):
            with self.subTest(labels=changed), self.assertRaises(ValueError):
                missing_marker_rom(data, changed)


if __name__ == '__main__':
    unittest.main()
