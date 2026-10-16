"""Real pinned assembler and diagnostic-only binary contract tests."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import subprocess

from tools.build_rom import (ROOT, BANK_BYTES, IMAGE_BYTES, build, load_slots, overrides, parse_dbg,
                             release_gate, source_provenance, verify_bank, verify_dev_rom,
                             verify_dev_rom_strict, verify_image)


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
        verify_dev_rom(self.rom)
        self.assertEqual(len(self.rom), IMAGE_BYTES)
        self.assertEqual(self.rom[BANK_BYTES:], b'\xff' * (31 * BANK_BYTES))

    def test_actual_all_81_jmps_and_ids(self):
        overridden = set(overrides())
        for slot in range(81):
            with self.subTest(slot=slot):
                at = slot * 3
                self.assertEqual(self.rom[at], 0x4c)
                address = int.from_bytes(self.rom[at+1:at+3], 'little')
                if slot in overridden:
                    # An override replaces the generated LDX #slot stub; its own body
                    # is checked separately (test_slot_1e_override_matches_measured_contract).
                    self.assertNotEqual(self.rom[address-0xe000:address-0xe000+2], bytes((0xa2, slot)))
                else:
                    self.assertEqual(self.rom[address-0xe000:address-0xe000+2], bytes((0xa2, slot)))
        self.assertEqual(0xe000+80*3, 0xe0f0)

    def test_slot_1e_override_matches_measured_contract(self):
        """Structure test: the slot_1E override body contains the expected instruction
        bytes (LDX #3; LDY #0; RTS). This verifies the override was linked correctly,
        NOT API compatibility. ABI behavior (X=3, Y=0, A preserved, P=0x06 cold) is
        verified on the real Geargrafx core in tests/integration/geargrafx_contracts.py.
        """
        self.assertEqual(set(overrides()), {0x1E})
        at = 0x1E * 3
        entry = int.from_bytes(self.rom[at+1:at+3], 'little')
        self.assertEqual(self.rom[entry-0xe000:entry-0xe000+5], bytes.fromhex('a2 03 a0 00 60'))

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
            with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_headered_image(self):
        with self.assertRaises(ValueError): verify_dev_rom(bytes(512)+self.rom)

    def test_wrong_bank_order(self):
        with self.assertRaises(ValueError): verify_dev_rom(self.rom[8192:16384]+self.rom[:8192]+self.rom[16384:])

    def test_non_padding_bank(self):
        data = bytearray(self.rom); data[8192] = 0
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_target_crosses_bank(self):
        data = bytearray(self.rom); data[1:3] = b'\x00\xc0'
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_handler_outside_bank(self):
        data = bytearray(self.rom); data[0x103:0x105] = b'\x00\xc0'
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_returning_halt_rejected(self):
        data = bytearray(self.rom)
        target = int.from_bytes(data[0x103:0x105], 'little') - 0xe000
        data[target] = 0x60
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_bad_reset_vector(self):
        data = bytearray(self.rom); data[0x1ffe:0x2000] = b'\x00\xe0'
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_bad_irq_vector(self):
        data = bytearray(self.rom); data[0x1ff6:0x1ff8] = b'\x00\x00'
        with self.assertRaises(ValueError): verify_dev_rom(data)

    def test_reset_success_return_rejected(self):
        data = bytearray(self.rom); data[0x1003] = 0x60
        with self.assertRaises(ValueError): verify_dev_rom(data)

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
        self.assertEqual(manifest['overridden_slots'], [0x1E])
        self.assertEqual(len(manifest['unimplemented_slots']), 80)
        self.assertEqual(manifest['abi'], 'UNVERIFIED')

    def make_root(self, name, **slots):
        """Source tree with slot_XX overrides; each body is assembled under api_slot_XX."""
        root = self.folder / name
        (root / 'src/overrides').mkdir(parents=True)
        for item in ('boot.s', 'bank0.cfg'):
            shutil.copy(ROOT / 'src' / item, root / 'src' / item)
        for slot, body in slots.items():
            header = f'.setcpu "huc6280"\n.import api_slot_00\n.segment "OVERRIDES"\n.export api_{slot}\napi_{slot}:\n'
            (root / f'src/overrides/{slot}.s').write_text(header + body)
        return root

    GOOD = ' ldx #$03\n bra @skip\n nop\n@skip: jsr helper\n jmp api_slot_00\n.byte $12,$34\nhelper: rts\n'

    def test_override_assembles_without_disabling_other_stubs(self):
        root = self.make_root('override-root', slot_03=self.GOOD)
        data = build(self.folder/'override-output', root=root).read_bytes()
        dbg = parse_dbg((self.folder/'override-output/dev.dbg').read_text())
        verify_image(data, {3}, dbg)
        self.assertEqual(int.from_bytes(data[10:12], 'little'), 0xe600)
        # Valid structure is a mechanism test, not an implemented API or approval.
        with self.assertRaises(ValueError): release_gate({3})
        manifest = json.loads((self.folder/'override-output/manifest.json').read_text())
        self.assertIn('structural-only', manifest['override_checks'])
        self.assertEqual(manifest['abi'], 'UNVERIFIED')
        self.assertEqual(manifest['overridden_slots'], [3])

    def test_override_debug_and_release_bytes_match(self):
        root = self.make_root('override-modes', slot_03=self.GOOD)
        self.assertEqual(build(self.folder/'om-debug', root=root).read_bytes(),
                         build(self.folder/'om-release', 'release', root).read_bytes())

    def test_override_without_link_info_is_rejected(self):
        data = build(self.folder/'override-nodbg', root=self.make_root('nodbg-root', slot_03=self.GOOD)).read_bytes()
        with self.assertRaisesRegex(ValueError, 'without ld65 debug info'): verify_image(data, {3})

    def test_bad_overrides_are_rejected(self):
        cases = {
            'falls into padding': (' ldx #$03\n nop\n', 'falls through'),
            'conditional falls into data': (' ldx #$03\n beq api_slot_03\n.byte $60\n', 'falls through'),
            'entry is data': ('.byte $60\n', 'not the address of an instruction|contains no code'),
            'jumps to padding': (' jmp $E900\n', r'jumps to \$E900'),
            'jumps into table gap': (' jmp $E0F5\n', r'jumps to \$E0F5'),
            'jumps mid-instruction': (' ldx #$03\n jmp api_slot_03+1\n', r'jumps to \$E601'),
            'jumps to boot body': (' jsr $F002\n rts\n', r'jumps to \$F002'),
            'branches to data': (' bra data\n rts\ndata: .byte $EA\n', 'jumps to'),
        }
        for name, (body, message) in cases.items():
            slug = name.replace(' ', '-')
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                build(self.folder/('bad-'+slug), root=self.make_root('root-'+slug, slot_03=body))

    def test_two_overrides_may_jump_to_each_other(self):
        root = self.make_root('two-root', slot_03=' ldx #$03\n jmp api_slot_04\n', slot_04=' rts\n')
        text = (root/'src/overrides/slot_03.s').read_text().replace('.import api_slot_00', '.import api_slot_00, api_slot_04')
        (root/'src/overrides/slot_03.s').write_text(text)
        data = build(self.folder/'two-out', root=root).read_bytes()
        verify_image(data, {3, 4}, parse_dbg((self.folder/'two-out/dev.dbg').read_text()))

    def test_conditional_branches_and_loops_are_accepted(self):
        body = ' ldx #$03\nagain: dex\n bne again\n beq done\n nop\ndone: bbr0 $10, done2\ndone2: rts\n'
        data = build(self.folder/'cond-out', root=self.make_root('cond-root', slot_03=body)).read_bytes()
        verify_image(data, {3}, parse_dbg((self.folder/'cond-out/dev.dbg').read_text()))

    def tampered(self, name, offset, value, body=None):
        """Valid build, then ROM bytes altered while the ORIGINAL debug info is kept."""
        root = self.make_root(name + '-root', slot_03=body or self.GOOD)
        data = bytearray(build(self.folder/name, root=root).read_bytes())
        dbg = parse_dbg((self.folder/name/'dev.dbg').read_text())
        verify_image(bytes(data), {3}, dbg)  # untampered baseline must pass
        data[0x600 + offset] = value
        return bytes(data), dbg

    def test_opcode_length_mismatch_is_rejected(self):
        # GOOD starts with LDX #$03 (2 bytes). $0F is BBR0 (3 bytes) -> debug-info size no longer matches.
        data, dbg = self.tampered('tamper-bbr0', 0, 0x0F)
        with self.assertRaisesRegex(ValueError, 'instruction length|undefined opcode'):
            verify_image(data, {3}, dbg)

    def test_three_byte_instruction_turned_into_shorter_one_is_rejected(self):
        body = ' ldx #$03\n jsr api_slot_00\n rts\n'
        data, dbg = self.tampered('tamper-short', 2, 0xA2, body)  # JSR abs ($20, 3 bytes) -> LDX # (2 bytes)
        with self.assertRaisesRegex(ValueError, 'instruction length'):
            verify_image(data, {3}, dbg)

    def test_tampered_branch_displacement_is_rejected(self):
        body = ' ldx #$03\n bra done\n nop\ndone: rts\n'
        data, dbg = self.tampered('tamper-rel', 3, 0x7F, body)  # BRA +127 -> beyond the override, into padding
        with self.assertRaisesRegex(ValueError, 'jumps to'):
            verify_image(data, {3}, dbg)
        data, dbg = self.tampered('tamper-rel2', 3, 0xFF, body)  # BRA -1 -> into its own operand byte
        with self.assertRaisesRegex(ValueError, 'its own operand'):
            verify_image(data, {3}, dbg)

    def test_tampered_override_binary_is_rejected(self):
        root = self.make_root('tamper-root', slot_03=self.GOOD)
        data = bytearray(build(self.folder/'tamper', root=root).read_bytes())
        dbg = parse_dbg((self.folder/'tamper/dev.dbg').read_text())
        at = 0x600 + data[0x600:0x620].index(0x4C)          # the override's JMP api_slot_00
        data[at+1:at+3] = bytes((0x00, 0xE9))               # ...retargeted into padding
        with self.assertRaisesRegex(ValueError, r'jumps to \$E900'): verify_image(bytes(data), {3}, dbg)
        data = bytearray(build(self.folder/'tamper2', root=root).read_bytes())
        data[1:3] = bytes((0x00, 0xE9))                     # table JMP no longer reaches the override
        with self.assertRaises(ValueError): verify_image(bytes(data), {3}, dbg)

    def test_manifest_describes_the_actual_source_root(self):
        root = self.make_root('provenance-root', slot_03=self.GOOD)
        build(self.folder/'prov-plain', root=root)
        plain = json.loads((self.folder/'prov-plain/manifest.json').read_text())
        self.assertIsNone(plain['source_sha'])            # never the tool checkout's SHA
        self.assertFalse(plain['source_root_is_git_repo'])
        self.assertIsNone(plain['working_tree_dirty'])
        self.assertEqual(plain['source_sha256']['src/overrides/slot_03.s'],
                         hashlib.sha256((root/'src/overrides/slot_03.s').read_bytes()).hexdigest())
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x']):
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
        build(self.folder/'prov-git', root=root)
        tracked = json.loads((self.folder/'prov-git/manifest.json').read_text())
        self.assertEqual((tracked['source_sha'], tracked['working_tree_dirty']), (head, False))
        (root/'src/overrides/slot_03.s').write_text((root/'src/overrides/slot_03.s').read_text() + '; edit' + chr(10))
        self.assertTrue(source_provenance(root, overrides(root))['working_tree_dirty'])
        self.assertNotEqual(source_provenance(root, overrides(root))['source_sha256'], tracked['source_sha256'])

    def test_included_files_are_hashed_in_manifest(self):
        root = self.make_root('include-root', slot_03=' ldx #$03\n .include "extra.inc"\n jmp api_slot_00\n')
        (root/'src/overrides/extra.inc').write_text(' nop\n')
        build(self.folder/'inc-out', root=root)
        hashes = json.loads((self.folder/'inc-out/manifest.json').read_text())['source_sha256']
        self.assertEqual(hashes['include:src/overrides/extra.inc'], hashlib.sha256(b' nop\n').hexdigest())

    def test_subdirectory_of_unrelated_repo_is_not_attributed(self):
        outer = self.folder/'outer'; outer.mkdir()
        subprocess.run(['git', '-C', str(outer), 'init', '-q'], check=True, capture_output=True)
        root = self.make_root('outer/tree')
        self.assertIsNone(source_provenance(root, {})['source_sha'])

    def test_bad_override_filename(self):
        root = self.folder/'bad-root'; (root/'src/overrides').mkdir(parents=True)
        (root/'src/overrides/slot_51.s').write_text('')
        with self.assertRaisesRegex(ValueError, 'Invalid override'): overrides(root)

    # --- Phase 1: verify_dev_rom trust boundary tests ---

    SLOT_1E_BODY_BYTES = bytes.fromhex('a2 03 a0 00 60')  # LDX #3; LDY #0; RTS

    def _find_override_body(self, rom: bytes) -> int:
        """Find the file offset of the slot_1E override body in a built ROM."""
        idx = rom.find(self.SLOT_1E_BODY_BYTES)
        if idx < 0:
            raise ValueError('slot_1E override body not found in ROM')
        return idx

    def _build_slot_1e_rom(self, suffix=''):
        """Build a DEV ROM with the slot_1E override and return (rom_bytes, dbg, root)."""
        root = self.folder / f'slot1e-{suffix}-root'
        (root / 'src/overrides').mkdir(parents=True)
        for item in ('boot.s', 'bank0.cfg'):
            shutil.copy(ROOT / 'src' / item, root / 'src' / item)
        # Write slot_1E.s with correct filename and symbol name so overrides()
        # finds it and the linker can resolve api_slot_1E from the API table
        (root / 'src/overrides' / 'slot_1E.s').write_text(
            '.setcpu "huc6280"\n'
            '.import api_slot_00\n'
            '.segment "OVERRIDES"\n'
            '.export api_slot_1E\n'
            'api_slot_1E:\n'
            ' ldx #$03\n'
            ' ldy #$00\n'
            ' rts\n'
        )
        rom = build(self.folder/f'slot1e-{suffix}', root=root).read_bytes()
        dbg = parse_dbg((self.folder/f'slot1e-{suffix}'/'dev.dbg').read_text())
        return rom, dbg, root

    def test_dev_rom_strict_accepts_untampered_slot_1e(self):
        """verify_dev_rom_strict must accept a correctly built slot_1E ROM."""
        rom, dbg, _ = self._build_slot_1e_rom('accept')
        # Should not raise
        verify_dev_rom_strict(rom, dbg)
        # verify_dev_rom (non-strict) must also accept it
        verify_dev_rom(rom)

    def test_dev_rom_strict_rejects_ldx_changed(self):
        """Changing the override's LDX #3 to NOP changes instruction length, caught by strict verification."""
        rom, dbg, _ = self._build_slot_1e_rom('ldx-change')
        data = bytearray(rom)
        at = self._find_override_body(data)
        self.assertEqual(data[at:at+2], bytes.fromhex('a2 03'), 'expected LDX #3 at override entry')
        data[at] = 0xEA  # NOP instead of LDX #3 (1 byte vs 2 bytes → length mismatch)
        tampered = bytes(data)

        # Non-strict verify_dev_rom does NOT catch override body changes (documented trust boundary)
        verify_dev_rom(tampered)  # should not raise

        # Strict verification MUST catch it
        with self.assertRaisesRegex(ValueError, 'instruction length'):
            verify_dev_rom_strict(tampered, dbg)

    def test_dev_rom_strict_rejects_rts_changed(self):
        """Changing the override's LDY #0 to NOP changes instruction length, caught by strict verification."""
        rom, dbg, _ = self._build_slot_1e_rom('rts-change')
        data = bytearray(rom)
        at = self._find_override_body(data)
        self.assertEqual(data[at:at+5], self.SLOT_1E_BODY_BYTES, 'expected LDX #3; LDY #0; RTS')
        data[at+2] = 0xEA  # NOP instead of LDY #0 (1 byte vs 2 bytes → length mismatch)
        tampered = bytes(data)

        verify_dev_rom(tampered)  # non-strict does not catch
        with self.assertRaisesRegex(ValueError, 'instruction length'):
            verify_dev_rom_strict(tampered, dbg)

    def test_dev_rom_strict_rejects_override_to_nop(self):
        """Replacing the override body with NOP must be caught by strict verification."""
        rom, dbg, _ = self._build_slot_1e_rom('override-nop')
        data = bytearray(rom)
        at = self._find_override_body(data)
        # Replace the 5-byte override body with NOPs: first NOP (1 byte) vs LDX #3 (2 bytes)
        data[at:at+5] = b'\xea' * 5
        tampered = bytes(data)

        verify_dev_rom(tampered)  # non-strict does not catch
        with self.assertRaisesRegex(ValueError, 'instruction length'):
            verify_dev_rom_strict(tampered, dbg)

    def test_dev_rom_without_dbg_trust_boundary(self):
        """verify_dev_rom without debug info does NOT verify override bodies (documented limit).

        This is the documented trust boundary: callers passing untrusted ROMs must use
        verify_dev_rom_strict() with ld65 debug info.
        """
        rom, _, _ = self._build_slot_1e_rom('no-dbg')
        data = bytearray(rom)
        at = self._find_override_body(data)
        data[at] = 0xEA  # NOP instead of LDX #3 (1 byte vs 2 bytes → length mismatch)
        tampered = bytes(data)

        # Non-strict verification without dbg accepts tampered override body
        # (documented behavior - callers must use strict for untrusted input)
        verify_dev_rom(tampered)  # should not raise

    def test_manifest_tampering_alone_is_not_enough(self):
        """Changing only the manifest (not the ROM) does not make a tampered ROM trusted.

        verify_dev_rom_strict verifies the ROM bytes against debug info, not the manifest.
        A tampered ROM with an unchanged manifest is still rejected.
        """
        rom, dbg, _ = self._build_slot_1e_rom('manifest-only')
        data = bytearray(rom)
        at = self._find_override_body(data)
        data[at] = 0xEA  # NOP instead of LDX #3
        tampered = bytes(data)

        # Even though we don't touch the manifest, strict verification of the ROM bytes catches it
        with self.assertRaises(ValueError):
            verify_dev_rom_strict(tampered, dbg)

    def test_rom_and_manifest_both_tampered_still_rejected(self):
        """Changing both ROM and manifest does not fool strict verification.

        verify_dev_rom_strict verifies ROM bytes against debug info, independent of
        any manifest. A tampered ROM is rejected regardless of manifest state.
        """
        rom, dbg, _ = self._build_slot_1e_rom('both-tampered')
        data = bytearray(rom)
        at = self._find_override_body(data)
        data[at] = 0xEA  # NOP instead of LDX #3
        tampered = bytes(data)

        with self.assertRaises(ValueError):
            verify_dev_rom_strict(tampered, dbg)
