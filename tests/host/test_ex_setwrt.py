"""Host checks for the project-own EX_SETWRT (slot $3A) VDC MAWR contract (Issue #68, DEV only).

Scope: a Python model of the documented contract (not the assembly) plus an exact instruction-sequence
check of src/overrides/slot_3A.s. No HuC6280 execution happens here. These checks are NOT evidence of
behaviour on hardware or on the Geargrafx core; real-core verification is a separate, verifier-run step.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "overrides" / "slot_3A.s"

# Exact instruction sequence (comments and directives stripped). Any change must update this on purpose.
EXPECTED = [
    "cpx #$80", "bcs setwrt_reject",
    "php", "sei", "phy", "phx", "pha",
    "tma #$01", "pha", "lda #$FF", "tam #$01",
    "stz a:$0000", "pla", "tay", "pla", "sta a:$0002", "plx", "stx a:$0003",
    "pha", "tya", "tam #$01", "pla", "ply", "plp", "clc", "rts",
    "sec", "rts",
]


def setwrt_model(a, x, mawr=None):
    """Model of the contract: return (ok, mawr_after, a_after, x_after). X >= $80 changes nothing."""
    if not (0 <= a <= 255 and 0 <= x <= 255):
        raise ValueError("A and X must be bytes")
    if x >= 0x80:
        return False, mawr, a, x
    return True, (x << 8) | a, a, x


class SetwrtModelTests(unittest.TestCase):
    def test_boundary_addresses(self):
        self.assertEqual(setwrt_model(0x00, 0x00), (True, 0x0000, 0x00, 0x00))
        self.assertEqual(setwrt_model(0xFF, 0x7F), (True, 0x7FFF, 0xFF, 0x7F))
        self.assertEqual(setwrt_model(0x34, 0x12), (True, 0x1234, 0x34, 0x12))

    def test_out_of_range_is_rejected_without_change(self):
        self.assertEqual(setwrt_model(0x00, 0x80, mawr=0x2222), (False, 0x2222, 0x00, 0x80))
        self.assertEqual(setwrt_model(0xAB, 0xFF, mawr=0x1111), (False, 0x1111, 0xAB, 0xFF))

    def test_non_byte_input_is_refused(self):
        for a, x in ((256, 0), (0, 256), (-1, 0), (0, -1)):
            with self.assertRaises(ValueError):
                setwrt_model(a, x)


class SetwrtSourceTests(unittest.TestCase):
    def setUp(self):
        self.text = SOURCE.read_text(encoding="utf-8")
        code = [line.split(";", 1)[0].strip() for line in self.text.splitlines()]
        self.code = [line for line in code if line and not line.startswith(".") and not line.endswith(":")]

    def test_entry_and_segment(self):
        self.assertIn('.segment "OVERRIDES"', self.text)
        self.assertIn(".export api_slot_3A", self.text)
        self.assertIn("api_slot_3A:", self.text)

    def test_instruction_sequence_is_exact(self):
        self.assertEqual(self.code, EXPECTED)

    def test_only_allowed_absolute_operands(self):
        # Only the VDC ports at $0000-$0003 (MPR0 window) may be addressed by absolute operands.
        for line in self.code:
            for hexaddr in re.findall(r"\$([0-9A-Fa-f]{4})\b", line):
                self.assertIn(hexaddr.upper(), {"0000", "0002", "0003"}, line)

    def test_no_zero_page_addressing(self):
        # (b) Zero page is $2000 on the pinned core, but a bare 1-2 digit operand (no #) is encoded as zero-page
        # by ca65 and would hit $20xx. Every VDC access must use the a: (absolute) prefix.
        # The detector is first checked on known inputs, so it cannot pass silently.
        bare = re.compile(r"(?<![#\w:])\$[0-9A-Fa-f]{1,2}(?![0-9A-Fa-f])")
        self.assertTrue(bare.search("    sta $02"))
        self.assertTrue(bare.search("    stz $00"))
        self.assertFalse(bare.search("    sta a:$0002"))
        self.assertFalse(bare.search("    lda #$02"))
        for line in self.code:
            self.assertIsNone(bare.search(line), line)
        for prefix in ("stz a:$0000", "sta a:$0002", "stx a:$0003"):
            self.assertIn(prefix, self.code)

    def test_no_access_to_the_stack_or_zero_page_window(self):
        # (c) The stack ($2100-$21FF) and zero page ($2000) stay in MPR1. No operand may name them.
        for line in self.code:
            for hexaddr in re.findall(r"\$([0-9A-Fa-f]{4})\b", line):
                self.assertFalse(0x2000 <= int(hexaddr, 16) <= 0x21FF, line)

    def test_routine_is_linked_in_overrides_mpr7(self):
        # (a) The routine is linked into OVERRIDES, which bank0.cfg places at $E300 (MPR7, not the I/O window).
        cfg = (ROOT / "src" / "bank0.cfg").read_text(encoding="utf-8")
        self.assertRegex(cfg, r"OVERRIDES:\s*load\s*=\s*ROM0,.*start\s*=\s*\$E[0-9A-Fa-f]{3}")
        start = int(re.search(r"OVERRIDES:.*start\s*=\s*\$([0-9A-Fa-f]{4})", cfg).group(1), 16)
        self.assertGreaterEqual(start, 0xE000)

    def test_window_is_mpr0_not_the_stack_page(self):
        # RED on the MPR1 window: tam #$02 maps $2000-$3FFF, which holds the stack ($2100-$21FF).
        # GREEN with the MPR0 window: $0000-$1FFF does not include the stack page.
        self.assertNotIn("tam #$02", self.code)
        self.assertNotIn("tma #$02", self.code)
        self.assertEqual(self.code.count("tam #$01"), 2)
        self.assertEqual(self.code.count("tma #$01"), 1)

    def test_push_pull_balance(self):
        pushes = sum(1 for line in self.code if line.split()[0] in ("php", "phy", "phx", "pha"))
        pulls = sum(1 for line in self.code if line.split()[0] in ("plp", "ply", "plx", "pla"))
        self.assertEqual(pushes, pulls)


if __name__ == "__main__":
    unittest.main()
