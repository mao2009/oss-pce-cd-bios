"""Real pinned assembler and diagnostic-only binary contract tests."""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_rom import (ROOT, BANK_BYTES, IMAGE_BYTES, build, load_slots,
                             overrides, release_gate, verify_bank, verify_image)


class ApiBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        cls.rom = build(cls.folder / 'debug').read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_actual_image_structure(self):
        verify_image(self.rom)
        self.assertEqual(len(self.rom), IMAGE_BYTES)
        self.assertEqual(self.rom[BANK_BYTES:], b'\xff' * (31 * BANK_BYTES))

    def test_actual_all_81_jmps_and_ids(self):
        for slot in range(81):
            with self.subTest(slot=slot):
                at = slot * 3
                self.assertEqual(self.rom[at], 0x4c)
                address = int.from_bytes(self.rom[at+1:at+3], 'little')
                self.assertEqual(self.rom[address-0xe000:address-0xe000+2], bytes((0xa2, slot)))
        self.assertEqual(0xe000+80*3, 0xe0f0)

    def test_debug_release_and_rebuild_match(self):
        for name, mode in [('release', 'release'), ('again', 'debug')]:
            self.assertEqual(build(self.folder / name, mode).read_bytes(), self.rom)

    def test_names_match_documented_inventory(self):
        text = (ROOT / 'docs/api-inventory.md').read_text()
        rows = re.findall(r'^\| `\$([0-9A-F]{2})` \| `\$[0-9A-F]+` \| `([^`]+)`', text, re.M)
        self.assertEqual([(s['id'], s['candidate_name']) for s in load_slots()],
                         [(int(i, 16), name) for i, name in rows])

    def test_psg_is_one_main_slot_with_nine_subcommands(self):
        self.assertEqual(load_slots()[0x48]['candidate_name'], 'PSG_BIOS')
        children = re.findall(r'^\| `48/([0-9A-F]{2})`', (ROOT/'docs/api-inventory.md').read_text(), re.M)
        self.assertEqual(children, ['00', '01', '02', '03', '04', '0B', '0C', '10', '13'])

    def test_wrong_size(self):
        for data in (self.rom[:-1], self.rom+b'\xff', self.rom[:8192]):
            with self.assertRaises(ValueError): verify_image(data)

    def test_headered_image(self):
        with self.assertRaises(ValueError): verify_image(bytes(512)+self.rom)

    def test_wrong_bank_order(self):
        with self.assertRaises(ValueError): verify_image(self.rom[8192:16384]+self.rom[:8192]+self.rom[16384:])

    def test_non_padding_bank(self):
        data = bytearray(self.rom); data[8192] = 0
        with self.assertRaises(ValueError): verify_image(data)

    def test_target_crosses_bank(self):
        data = bytearray(self.rom); data[1:3] = b'\x00\xc0'
        with self.assertRaises(ValueError): verify_image(data)

    def test_handler_outside_bank(self):
        data = bytearray(self.rom); data[0x103:0x105] = b'\x00\xc0'
        with self.assertRaises(ValueError): verify_image(data)

    def test_returning_halt_rejected(self):
        data = bytearray(self.rom)
        target = int.from_bytes(data[0x103:0x105], 'little') - 0xe000
        data[target] = 0x60
        with self.assertRaises(ValueError): verify_image(data)

    def test_bad_reset_vector(self):
        data = bytearray(self.rom); data[0x1ffe:0x2000] = b'\x00\xe0'
        with self.assertRaises(ValueError): verify_image(data)

    def test_bad_irq_vector(self):
        data = bytearray(self.rom); data[0x1ff6:0x1ff8] = b'\x00\x00'
        with self.assertRaises(ValueError): verify_image(data)

    def test_reset_success_return_rejected(self):
        data = bytearray(self.rom); data[0x1003] = 0x60
        with self.assertRaises(ValueError): verify_image(data)

    def test_missing_tool(self):
        with patch.dict(os.environ, {'CA65': '/missing/ca65'}):
            with self.assertRaisesRegex(ValueError, 'missing tool'): build(self.folder/'missing')

    def test_invalid_mode(self):
        with self.assertRaisesRegex(ValueError, 'MODE'): build(self.folder/'invalid', 'invalid')

    def test_release_gate_missing_and_all_overrides(self):
        with self.assertRaisesRegex(ValueError, '81 unimplemented'): release_gate(set())
        with self.assertRaisesRegex(ValueError, '80 unimplemented'): release_gate({3})
        with self.assertRaisesRegex(ValueError, 'does not prove implementation'): release_gate(set(range(81)))

    def test_manifest_never_claims_release(self):
        manifest = json.loads((self.folder/'debug/manifest.json').read_text())
        self.assertFalse(manifest['release_eligible'])
        self.assertEqual(len(manifest['unimplemented_slots']), 81)
        self.assertEqual(manifest['abi'], 'UNVERIFIED')

    def test_override_assembles_without_disabling_other_stubs(self):
        root = self.folder/'override-root'; (root/'src/overrides').mkdir(parents=True)
        for name in ('boot.s', 'bank0.cfg'): shutil.copy(ROOT/'src'/name, root/'src'/name)
        (root/'src/overrides/slot_03.s').write_text('.setcpu "huc6280"\n.segment "OVERRIDES"\n.export api_slot_03\napi_slot_03:\n ldx #$03\n@halt:\n bra @halt\n')
        data = build(self.folder/'override-output', root=root).read_bytes()
        verify_image(data, {3})
        self.assertEqual(int.from_bytes(data[10:12], 'little'), 0xe600)
        # An override is a mechanism test, not an implemented API or approval.
        with self.assertRaises(ValueError): release_gate({3})

    def test_bad_override_filename(self):
        root = self.folder/'bad-root'; (root/'src/overrides').mkdir(parents=True)
        (root/'src/overrides/slot_51.s').write_text('')
        with self.assertRaisesRegex(ValueError, 'Invalid override'): overrides(root)
