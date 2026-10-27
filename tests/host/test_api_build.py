"""Real pinned assembler and diagnostic-only binary contract tests."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from tools.environment import requires_cc65
from pathlib import Path
from unittest.mock import patch

import subprocess

from tools.build_rom import (ROOT, BANK_BYTES, IMAGE_BYTES, build, load_slots, overrides, parse_dbg,
                             release_gate, source_provenance, verify_bank, verify_dev_rom,
                             verify_dev_rom_layout, verify_dev_rom_structure, verify_image)


@requires_cc65
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
        NOT API compatibility. The guest-visible behaviour (X=3, Y=0, A preserved,
        Z=1/N=0/I=1 with carry preserved) is verified on the real Geargrafx core in
        tests/integration/geargrafx_contracts.py; the absolute P byte is not fixed.
        """
        self.assertEqual(set(overrides()), {0x1E, 0x3C, 0x3D, 0x3E, 0x3F, 0x40, 0x41, 0x42, 0x43, 0x44, 0x46, 0x4F})  # 1E measured; 3C/3D/3E/3F/40/41/42/43/44/46/4F project-own DEV routines
        at = 0x1E * 3
        entry = int.from_bytes(self.rom[at+1:at+3], 'little')
        self.assertEqual(self.rom[entry-0xe000:entry-0xe000+5], bytes.fromhex('a2 03 a0 00 60'))

    def test_api_implementation_list_matches_overrides(self):
        """The API_IMPLEMENTATION string in build_rom.py must list every override slot exactly once."""
        import tools.build_rom as br
        ov = br.overrides()
        impl = br._API_IMPLEMENTATION
        for slot in sorted(ov):
            self.assertIn(f"slot_{slot:02X}", impl,
                          f"API_IMPLEMENTATION missing slot_{slot:02X}")
        # Count of slots in the string must match the override count
        slot_count = impl.count("slot_")
        self.assertEqual(slot_count, len(ov),
                         f"API_IMPLEMENTATION has {slot_count} slot references, expected {len(ov)}")
        # Unimplemented count must match
        unimplemented = 81 - len(ov)
        self.assertIn(f"{unimplemented} slots unimplemented", impl,
                      f"API_IMPLEMENTATION should say {unimplemented} slots unimplemented")

    def test_debug_release_and_rebuild_match(self):
        for name, mode in [('release', 'release'), ('again', 'debug')]:
            self.assertEqual(build(self.folder / name, mode).read_bytes(), self.rom)

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

    def test_manifest_never_claims_release(self):
        manifest = json.loads((self.folder/'debug/manifest.json').read_text())
        self.assertFalse(manifest['release_eligible'])
        self.assertEqual(manifest["overridden_slots"], [0x1E, 0x3C, 0x3D, 0x3E, 0x3F, 0x40, 0x41, 0x42, 0x43, 0x44, 0x4F])
        self.assertEqual(len(manifest["unimplemented_slots"]), 70)
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
        self.assertEqual(int.from_bytes(data[10:12], 'little'), 0xe300)
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
            'jumps mid-instruction': (' ldx #$03\n jmp api_slot_03+1\n', r'jumps to \$E301'),
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
        data[0x300 + offset] = value
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
        at = 0x300 + data[0x300:0x320].index(0x4C)          # the override's JMP api_slot_00
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

    # --- verify_dev_rom trust boundary and mutation matrix ---
    #
    # verify_dev_rom_layout()   layout only (table/stubs/vectors/padding), no override byte claim
    # verify_dev_rom_structure() layout + ld65/da65 structural check; blind to same-length changes
    # verify_dev_rom()          layout + byte identity against an independent rebuild of trusted
    #                           sources; the only acceptance gate for a loaded ROM

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

    def _tamper(self, suffix, offset, value):
        """Build the slot_1E ROM, change one byte inside the override body, keep its dbg."""
        rom, dbg, root = self._build_slot_1e_rom(suffix)
        data = bytearray(rom)
        at = self._find_override_body(data)
        self.assertEqual(data[at:at+5], self.SLOT_1E_BODY_BYTES)
        data[at+offset] = value
        tampered = bytes(data)
        self.assertNotEqual(tampered, rom)
        return rom, tampered, dbg, root

    def test_identity_accepts_untampered_slot_1e(self):
        """The normal ROM passes every layer; no false positive from identity."""
        rom, dbg, root = self._build_slot_1e_rom('accept')
        verify_dev_rom_layout(rom, root)
        verify_dev_rom_structure(rom, dbg, root)
        verify_dev_rom(rom, root)
        verify_dev_rom(rom, root, manifest=self.folder / 'slot1e-accept' / 'manifest.json')

    def test_same_length_substitutions_pass_structure_but_fail_identity(self):
        """RED: structural verification cannot see equal-length substitutions.

        LDX #3 -> LDX #4 and LDY #0 -> LDY #1 change only an operand; RTS -> RTI swaps a
        one-byte terminator for another. Every instruction boundary, length and target
        still checks out, so the ld65/da65 structure check accepts all three. GREEN:
        verify_dev_rom() rejects them because the bytes no longer match an independent
        rebuild from the trusted sources.
        """
        mutations = {'ldx3-ldx4': (1, 0x04), 'ldy0-ldy1': (3, 0x01), 'rts-rti': (4, 0x40)}
        for name, (offset, value) in mutations.items():
            with self.subTest(mutation=name):
                rom, tampered, dbg, root = self._tamper('same-' + name, offset, value)
                verify_dev_rom_structure(tampered, dbg, root)  # documented structural blind spot
                with self.assertRaisesRegex(ValueError, 'not byte-identical'):
                    verify_dev_rom(tampered, root)

    def test_length_changing_or_falling_through_mutations_are_rejected_by_structure_and_identity(self):
        """LDX #3 -> NOP and LDY #0 -> NOP shorten the body (instruction length mismatch);
        RTS -> NOP makes the last instruction fall out of the override. Structure and
        identity both reject all three."""
        for name, offset, value, message in [('ldx-nop', 0, 0xEA, 'instruction length'),
                                             ('ldy-nop', 2, 0xEA, 'instruction length'),
                                             ('rts-nop', 4, 0xEA, 'falls through')]:
            with self.subTest(mutation=name):
                rom, tampered, dbg, root = self._tamper(name, offset, value)
                with self.assertRaisesRegex(ValueError, message):
                    verify_dev_rom_structure(tampered, dbg, root)
                with self.assertRaisesRegex(ValueError, 'not byte-identical'):
                    verify_dev_rom(tampered, root)

    def test_table_target_change_evades_layout_but_not_identity(self):
        """A same-length retarget of the slot_1E table JMP stays in range, so the
        layout-only check accepts it; structure and identity reject it because the
        rebuild's table still points at the linked override."""
        rom, _, root = self._build_slot_1e_rom('table-change')
        data = bytearray(rom)
        at = 0x1E * 3
        self.assertEqual(data[at], 0x4C)
        self.assertEqual(int.from_bytes(data[at+1:at+3], 'little'), 0xE300)
        data[at+1:at+3] = (0xE700).to_bytes(2, 'little')  # within $E100-$FFF6, so layout passes
        tampered = bytes(data)
        dbg = parse_dbg((self.folder / 'slot1e-table-change' / 'dev.dbg').read_text())
        verify_dev_rom_layout(tampered, root)               # layout alone cannot see this
        with self.assertRaises(ValueError):
            verify_dev_rom_structure(tampered, dbg, root)
        with self.assertRaisesRegex(ValueError, 'not byte-identical'):
            verify_dev_rom(tampered, root)

    def test_identity_needs_no_debug_info(self):
        """Byte identity is decided by the rebuild, so it rejects tampering even when
        no ld65 debug info is available."""
        rom, tampered, _, root = self._tamper('no-dbg', 1, 0x04)
        with self.assertRaisesRegex(ValueError, 'not byte-identical'):
            verify_dev_rom(tampered, root)
        verify_dev_rom(rom, root)

    def test_manifest_alone_cannot_make_a_rom_trusted(self):
        """Manifest cross-check: the manifest is only compared against the rebuild."""
        rom, _, root = self._build_slot_1e_rom('manifest-only')
        out = self.folder / 'slot1e-manifest-only'
        manifest = out / 'manifest.json'
        trusted_manifest = json.loads(manifest.read_text())
        # 1. ROM tampered, manifest untouched -> rejected by ROM byte identity.
        data = bytearray(rom); data[self._find_override_body(data)] = 0x04  # LDX #4
        with self.assertRaisesRegex(ValueError, 'not byte-identical'):
            verify_dev_rom(bytes(data), root, manifest)
        # 2. ROM untouched, manifest rom_sha256 rewritten -> rejected as inconsistent.
        manifest.write_text(json.dumps({**trusted_manifest, 'rom_sha256': '0' * 64}, indent=2) + '\n')
        with self.assertRaisesRegex(ValueError, 'manifest rom_sha256 disagrees'):
            verify_dev_rom(rom, root, manifest)
        # 3. ROM + manifest both rewritten to be self-consistent -> still rejected.
        tampered = bytearray(rom); tampered[self._find_override_body(tampered)] = 0x04
        manifest.write_text(json.dumps({**trusted_manifest,
                                        'rom_sha256': hashlib.sha256(bytes(tampered)).hexdigest()},
                                       indent=2) + '\n')
        with self.assertRaisesRegex(ValueError, 'not byte-identical'):
            verify_dev_rom(bytes(tampered), root, manifest)
        # 4. Restored manifest + normal ROM -> accepted.
        manifest.write_text(json.dumps(trusted_manifest, indent=2) + '\n')
        verify_dev_rom(rom, root, manifest)

    def test_overlay_region_override_is_refused_by_probe_builder(self):
        """The probe overlay is copied over $E800-$EFFF, so an override linked there would
        be silently discarded. build_probe_images() must refuse such a trusted ROM up front."""
        from tools.diagnostics import build_probe_images
        root = self.folder / 'overlay-root'
        (root / 'src/overrides').mkdir(parents=True)
        (root / 'src' / 'boot.s').write_bytes((ROOT / 'src' / 'boot.s').read_bytes())
        # Same bank0 geometry, but OVERRIDES starts at $E900 (inside the probe overlay window).
        (root / 'src' / 'bank0.cfg').write_text(
            (ROOT / 'src' / 'bank0.cfg').read_text().replace('start = $E300', 'start = $E900'))
        (root / 'src/overrides' / 'slot_1E.s').write_text(
            '.setcpu "huc6280"\n.segment "OVERRIDES"\n.export api_slot_1E\napi_slot_1E:\n'
            ' ldx #$03\n ldy #$00\n rts\n')
        rom = build(self.folder / 'overlay-out', root=root).read_bytes()
        self.assertEqual(int.from_bytes(rom[0x1E*3+1:0x1E*3+3], 'little'), 0xE900)
        verify_dev_rom(rom, root)  # genuinely built, so identity accepts it
        with self.assertRaisesRegex(ValueError, 'probe overlay'):
            build_probe_images(rom, self.folder / 'overlay-probe', root=root)

class ApiContractTests(unittest.TestCase):
    """cc65-independent API checks: no built ROM is needed, so these always run."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_names_match_documented_inventory(self):
        text = (ROOT / 'docs/api-inventory.md').read_text(encoding='utf-8')
        rows = re.findall(r'^\| `\$([0-9A-F]{2})` \| `\$[0-9A-F]+` \| `([^`]+)`', text, re.M)
        self.assertEqual([(s['id'], s['candidate_name']) for s in load_slots()],
                         [(int(i, 16), name) for i, name in rows])

    def test_psg_is_one_main_slot_with_nine_subcommands(self):
        self.assertEqual(load_slots()[0x48]['candidate_name'], 'PSG_BIOS')
        children = re.findall(r'^\| `48/([0-9A-F]{2})`', (ROOT/'docs/api-inventory.md').read_text(encoding='utf-8'), re.M)
        self.assertEqual(children, ['00', '01', '02', '03', '04', '0B', '0C', '10', '13'])

    def test_missing_tool(self):
        with patch.dict(os.environ, {'CA65': '/missing/ca65'}):
            with self.assertRaisesRegex(ValueError, 'missing tool'): build(self.folder/'missing')

    def test_invalid_mode(self):
        with self.assertRaisesRegex(ValueError, 'MODE'): build(self.folder/'invalid', 'invalid')

    def test_release_gate_missing_and_all_overrides(self):
        with self.assertRaisesRegex(ValueError, '81 unimplemented'): release_gate(set())
        with self.assertRaisesRegex(ValueError, '80 unimplemented'): release_gate({3})
        with self.assertRaisesRegex(ValueError, 'does not prove implementation'): release_gate(set(range(81)))

    def test_subdirectory_of_unrelated_repo_is_not_attributed(self):
        outer = self.folder/'outer'; outer.mkdir()
        subprocess.run(['git', '-C', str(outer), 'init', '-q'], check=True, capture_output=True)
        root = self.folder/'outer/tree'; (root/'src/overrides').mkdir(parents=True)
        for item in ('boot.s', 'bank0.cfg'):
            shutil.copy(ROOT / 'src' / item, root / 'src' / item)
        self.assertIsNone(source_provenance(root, {})['source_sha'])

    def test_bad_override_filename(self):
        root = self.folder/'bad-root'; (root/'src/overrides').mkdir(parents=True)
        (root/'src/overrides/slot_51.s').write_text('')
        with self.assertRaisesRegex(ValueError, 'Invalid override'): overrides(root)
