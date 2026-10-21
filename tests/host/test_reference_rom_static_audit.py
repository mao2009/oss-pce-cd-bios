#!/usr/bin/env python3
"""Host tests for the local-only reference ROM static auditor.

The auditor never runs in CI, because CI has no private BIOS image. These
tests therefore drive it with a **synthetic** image that the test itself
builds, so the decoder, the jump table parser and the hazard classifier are
covered without any private input.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import reference_rom_static_audit as audit  # noqa: E402
from environment import requires_cc65  # noqa: E402


def synthetic_image(callee=b"\xa2\x03\xa0\x00\x60"):
    """A headerless 8 KiB bank with an 81 entry JMP table and one tiny callee.

    Slot 0x1E points at the callee, the other slots point at distinct stubs.
    The callee is the project-own EX_GETVER shape (load X, load Y, return):
    it is a test fixture written for this repository, not reference code.
    """
    bank = bytearray(b"\xff" * audit.BANK_BYTES)
    base = 0xE000 - 0xE000  # the table starts at the first byte of the bank
    for index in range(audit.ENTRY_COUNT):
        offset = base + index * audit.ENTRY_STRIDE
        if index == 0x1E:
            target = 0xF000
        else:
            target = 0xF100 + index * 3
        bank[offset] = audit.JMP_ABS
        bank[offset + 1] = target & 0xFF
        bank[offset + 2] = target >> 8
    bank[0xF000 - 0xE000:0xF000 - 0xE000 + len(callee)] = callee
    for index in range(audit.ENTRY_COUNT):
        if index == 0x1E:
            continue
        offset = 0xF100 + index * 3 - 0xE000
        bank[offset] = 0x60  # RTS
    return bytes(bank)


@requires_cc65
class SyntheticImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        self.image = self.directory / "synthetic.pce"
        self.image.write_bytes(synthetic_image())

    def test_jump_table_is_parsed_as_three_byte_jump_entries(self):
        result = audit.audit_image(self.image, [0x1E])
        table = result["jump_table"]
        self.assertEqual(table["entry_count"], 81)
        self.assertEqual(table["entry_stride_bytes"], 3)
        self.assertTrue(table["all_entries_are_jmp_abs"])
        self.assertTrue(table["all_targets_distinct"])
        self.assertEqual(result["apis"][0]["candidate_entry"], "0xE05A")

    def test_a_side_effect_free_callee_reports_no_hazard(self):
        result = audit.audit_image(self.image, [0x1E])
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["returns_observed"], ["rts"])
        self.assertEqual(profile["instructions_decoded"], 3)
        self.assertEqual([value for value in profile["hazard_counts"].values() if value], [])
        self.assertEqual(profile["store_target_classes"], {})
        self.assertEqual(profile["unresolved"], [])

    def test_a_block_transfer_is_reported_as_a_hazard(self):
        image = self.directory / "block.pce"
        # TII $204D,$205F,$0003 followed by RTS: a block transfer in the callee.
        image.write_bytes(synthetic_image(b"\x73\x4d\x20\x5f\x20\x03\x00\x60"))
        result = audit.audit_image(image, [0x1E])
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["BLOCK_TRANSFER"], 1)
        self.assertEqual(profile["hazard_counts"]["OTHER_STORE"], 1)

    def test_a_zero_page_store_is_reported_in_page_one(self):
        image = self.directory / "zp.pce"
        # STX $EC then RTS: the HuC6280 zero page lives at $2000.
        image.write_bytes(synthetic_image(b"\x86\xec\x60"))
        result = audit.audit_image(image, [0x1E])
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["PAGE1_STORE"], 1)
        self.assertEqual(profile["store_target_classes"], {"PAGE1_ZERO_PAGE": 1})
        self.assertEqual(profile["zero_page_operand_stores"], 1)

    def test_an_interrupt_enable_is_reported(self):
        image = self.directory / "cli.pce"
        image.write_bytes(synthetic_image(b"\x58\x60"))
        result = audit.audit_image(image, [0x1E])
        self.assertEqual(result["apis"][0]["callee"]["hazard_counts"]["IRQ_ENABLE"], 1)

    def test_a_mpr_switch_is_reported(self):
        image = self.directory / "tam.pce"
        # LDA #$03 / TAM #$40 (MPR6) then RTS.
        image.write_bytes(synthetic_image(b"\xa9\x03\x53\x40\x60"))
        result = audit.audit_image(image, [0x1E])
        profile = result["apis"][0]["callee"]
        self.assertEqual(profile["hazard_counts"]["MPR_WRITE"], 1)

    def test_the_projection_never_carries_bytes_or_addresses(self):
        result = audit.audit_image(self.image, [0x1E])
        text = repr(result)
        self.assertNotIn("0x4c", text.lower())
        self.assertNotIn("synthetic", text.lower())


class GuardTests(unittest.TestCase):
    def test_a_partial_bank_image_is_refused(self):
        directory = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        image = directory / "short.pce"
        image.write_bytes(b"\x00" * 1234)
        with self.assertRaises(audit.AuditError):
            audit.audit_image(image, [0x1E])

    def test_a_slot_outside_the_table_is_refused(self):
        directory = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        image = directory / "synthetic.pce"
        image.write_bytes(synthetic_image())
        with self.assertRaises(audit.AuditError):
            audit.audit_image(image, [0x99])


if __name__ == "__main__":
    unittest.main()
