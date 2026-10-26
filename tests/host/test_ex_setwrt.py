"""Host checks for the project-own EX_SETWRT (slot $3A) VDC MAWR contract (Issue #68, DEV only).

Scope: a Python model of the documented contract plus static checks of src/overrides/slot_3A.s.
No HuC6280 execution happens here; real-core evidence is NOT_RUN (see the PR body).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "overrides" / "slot_3A.s"


def setwrt_model(a, x, mawr=None):
    """Return (ok, mawr_after, a_after, x_after). Out-of-range X leaves everything unchanged."""
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
        self.code = [line.split(";", 1)[0].strip() for line in self.text.splitlines()]
        self.code = [line for line in self.code if line and not line.startswith(".") and not line.endswith(":")]

    def test_entry_and_segment(self):
        self.assertIn(".segment \"OVERRIDES\"", self.text)
        self.assertIn(".export api_slot_3A", self.text)
        self.assertIn("api_slot_3A:", self.text)

    def test_range_check_precedes_any_io(self):
        first_io = next(i for i, line in enumerate(self.code) if line.startswith(("stz", "sta $0", "tam")))
        self.assertEqual(self.code[0], "cpx #$80")
        self.assertTrue(self.code[1].startswith("bcs"))
        self.assertLess(1, first_io)
        self.assertIn("setwrt_reject:", self.text)

    def test_mawr_write_sequence(self):
        seq = [line for line in self.code if line.startswith(("stz $0000", "sta $0002", "sta $0003"))]
        self.assertEqual(seq, ["stz $0000", "sta $0002", "sta $0003"])

    def test_stack_pushes_and_pulls_balance(self):
        def count(prefix):
            return sum(1 for line in self.code if line.split()[0] == prefix)
        self.assertEqual(count("php") + count("phy") + count("phx") + count("pha"),
                         count("plp") + count("ply") + count("plx") + count("pla"))
        self.assertEqual(count("tma"), 1)
        self.assertEqual(count("tam"), 2)

    def test_no_zero_page_or_wram_access(self):
        # Only the VDC ports $0000-$0003 may be addressed in $0000-$00FF; other absolute operands must be >= $0100.
        for line in self.code:
            for hexaddr in re.findall(r"\$([0-9A-Fa-f]{4})\b", line):
                self.assertTrue(int(hexaddr, 16) >= 0x0100 or int(hexaddr, 16) <= 0x0003, line)


if __name__ == "__main__":
    unittest.main()
