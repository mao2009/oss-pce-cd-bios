"""MODEL-ONLY host check of the DEV EX_JOYSNS contract (slot $21, Issue #43).
Not the System Card ABI.

DEV_EXPERIMENTAL, project-own contract. Genuine ABI (inputs/outputs, work-RAM layout,
multi-pad scan, trigger/delta state, timing, flag semantics) is UNKNOWN/UNVERIFIED.
release_eligible: false. This must not be treated as reference compatibility evidence.

The routine performs real joypad I/O reads on port $1000 (SEL=1 directions, SEL=0
buttons) and returns A = (dir_nibble<<4) | (btn_nibble & $0F) with active-low bits.
X,Y and MPR0 are preserved; no VDC/work-RAM writes. Real-core execution evidence is
in tests/integration/geargrafx_dev_joysns.py (pinned Geargrafx).
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "overrides" / "slot_21.s"
SPEC = ROOT / "spec" / "dev_implementations.json"


def expected_a(dir_nibble, btn_nibble):
    """DEV model: combine active-low nibbles as read from $1000."""
    return ((dir_nibble & 0x0F) << 4) | (btn_nibble & 0x0F)


class ModelContractTests(unittest.TestCase):
    def test_no_input_returns_ff(self):
        self.assertEqual(expected_a(0x0F, 0x0F), 0xFF)

    def test_button_bits_active_low(self):
        self.assertEqual(expected_a(0x0F, 0x07), 0xF7)  # RUN pressed
        self.assertEqual(expected_a(0x0F, 0x0E), 0xFE)  # I pressed
        self.assertEqual(expected_a(0x0F, 0x0D), 0xFD)  # II pressed
        self.assertEqual(expected_a(0x0F, 0x0B), 0xFB)  # SELECT pressed

    def test_direction_bits_active_low(self):
        self.assertEqual(expected_a(0x0E, 0x0F), 0xEF)  # UP
        self.assertEqual(expected_a(0x0D, 0x0F), 0xDF)  # RIGHT
        self.assertEqual(expected_a(0x0B, 0x0F), 0xBF)  # DOWN
        self.assertEqual(expected_a(0x07, 0x0F), 0x7F)  # LEFT

    def test_combinations(self):
        self.assertEqual(expected_a(0x06, 0x07), 0x67)  # UP+LEFT + RUN
        self.assertEqual(expected_a(0x00, 0x00), 0x00)  # all pressed


class SourceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SRC.read_text()

    def test_exports_slot_symbol(self):
        self.assertIn(".export api_slot_21", self.text)
        self.assertIn("api_slot_21:", self.text)

    def test_declares_dev_experimental_not_abi(self):
        self.assertIn("DEV_EXPERIMENTAL", self.text)
        self.assertIn("NOT the reference", self.text)

    def test_performs_real_io(self):
        self.assertIn("sta a:$1000", self.text)
        self.assertIn("lda a:$1000", self.text)

    def test_preserves_registers_and_mpr0(self):
        self.assertIn("tma #$01", self.text)
        self.assertIn("tam #$01", self.text)
        self.assertIn("ply", self.text)
        self.assertIn("plx", self.text)

    def test_uses_stack_page_addressing_for_temp(self):
        # must not read stack temp via MPR0-mapped $01xx while MPR0=$FF
        self.assertIn("$2101,x", self.text)


class SpecTests(unittest.TestCase):
    def test_dev_implementation_entry(self):
        spec = json.loads(SPEC.read_text())
        slot = next(s for s in spec["slots"] if s["id"] == 33)
        self.assertEqual(slot["candidate_name"], "EX_JOYSNS")
        self.assertEqual(slot["state"], "NOT_READY")
        self.assertEqual(slot["abi_contract"]["status"], "PROJECT_SPEC_ONLY")
        self.assertEqual(slot["implementation"]["source"], "src/overrides/slot_21.s")
        self.assertIn("tests/host/test_api_joysns_dev.py", slot["host_evidence"])
        self.assertIn("tests/integration/geargrafx_dev_joysns.py", slot["real_core_evidence"])


if __name__ == "__main__":
    unittest.main()
