import json
import tempfile
import unittest
from pathlib import Path

from tools.build_rom import BANK_BYTES, LAST_SLOT, load_slots, render_table, verify_bank


class ApiTableTests(unittest.TestCase):
    def setUp(self):
        self.slots = load_slots()

    def test_slot_inventory_is_contiguous(self):
        self.assertEqual(len(self.slots), 81)
        self.assertEqual([s['id'] for s in self.slots], list(range(81)))

    def test_generator_has_all_entries_and_fail_closed_handlers(self):
        text = render_table(self.slots, set())
        self.assertEqual(sum(line.startswith('    jmp api_slot_') for line in text.splitlines()), 81)
        self.assertEqual(text.count('    ldx #$'), 81)
        self.assertIn('    bra @halt', text)
        self.assertNotIn('    rts', text.lower())

    def test_override_omits_only_selected_stub(self):
        text = render_table(self.slots, {3})
        self.assertEqual(sum(line.startswith('    jmp api_slot_') for line in text.splitlines()), 81)
        self.assertIn('.import api_slot_03', text)
        self.assertNotIn('.export api_slot_03', text)
        self.assertIn('.export api_slot_02', text)

    def test_invalid_inventory_order(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'slots.json'
            bad = {'schema_version': 1, 'slots': [dict(s) for s in self.slots]}
            bad['slots'][3]['id'] = 42
            p.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                load_slots(p)

    def test_fixture_accepts_valid_debug_structure(self):
        self.assertIsNone(verify_bank(self.build_fake_bank()))

    def test_fixture_rejects_non_jmp(self):
        data = bytearray(self.build_fake_bank())
        data[0] = 0x60  # RTS is forbidden at a fixed API entry
        with self.assertRaisesRegex(ValueError, 'not JMP'):
            verify_bank(data)

    def test_fixture_rejects_shared_jump_targets(self):
        data = bytearray(self.build_fake_bank())
        data[4:6] = data[1:3]
        with self.assertRaisesRegex(ValueError, 'share implementation'):
            verify_bank(data)

    def test_fixture_rejects_false_success_stub(self):
        data = bytearray(self.build_fake_bank())
        data[0x100] = 0x60  # RTS stub
        with self.assertRaisesRegex(ValueError, 'debug marker'):
            verify_bank(data)

    def test_override_does_not_bypass_other_fallbacks(self):
        data = bytearray(self.build_fake_bank())
        data[0x100] = 0x60  # Corrupt slot 00 while slot 03 is overridden
        with self.assertRaisesRegex(ValueError, 'debug marker'):
            verify_bank(data, overridden={3})

    @staticmethod
    def build_fake_bank():
        """Artificial structural test fixture, NOT an emulator or assembled BIOS."""
        bank = bytearray(b'\xFF' * BANK_BYTES)
        handler = 0xE100 + 81 * 5
        at = handler - 0xE000
        bank[at:at+2] = bytes((0x80, 0xFE))
        for i in range(LAST_SLOT + 1):
            stub = 0xE100 + 5 * i
            bank[3*i:3*i+3] = bytes((0x4C, stub & 255, stub >> 8))
            pos = stub - 0xE000
            bank[pos:pos+5] = bytes((0xA2, i, 0x4C, handler & 255, handler >> 8))
        bank[0x1000:0x1005] = bytes.fromhex('78 a2 ff 80 fe')
        bank[-10:] = bytes((0x00, 0xF0)) * 5
        return bytes(bank)


if __name__ == '__main__':
    unittest.main()
