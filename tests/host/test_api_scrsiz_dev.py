"""MODEL-ONLY host check of the DEV EX_SCRSIZ contract (slot $23, Issue #45).
Not the System Card ABI.

DEV_EXPERIMENTAL, project-own contract. Genuine ABI is UNKNOWN/UNVERIFIED.
release_eligible: false. This does not prove reference compatibility.

DEV contract (real VDC I/O): programs VDC MWR (register $09) low byte from A (real VDC I/O; MWR mask 0x00FF).
Real-core execution evidence: tests/integration/geargrafx_dev_scrsiz.py.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "overrides" / "slot_23.s"
SPEC = ROOT / "spec" / "dev_implementations.json"


class SourceStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SRC.read_text()

    def test_exports_slot_symbol(self):
        self.assertIn(".export api_slot_23", self.text)
        self.assertIn("api_slot_23:", self.text)

    def test_declares_dev_experimental_not_abi(self):
        self.assertIn("DEV_EXPERIMENTAL", self.text)
        self.assertIn("NOT the reference", self.text)

    def test_performs_real_vdc_io(self):
        self.assertIn("sta a:$0000", self.text)  # select AR
        self.assertIn("sta a:$0002", self.text)  # data low
        self.assertIn("stz a:$0003", self.text)  # high byte write (masked register)
        self.assertIn("lda #$09", self.text)     # MWR register

    def test_preserves_registers_and_mpr0(self):
        self.assertIn("tma #$01", self.text)
        self.assertIn("tam #$01", self.text)
        self.assertIn("ply", self.text)
        self.assertIn("plx", self.text)


class SpecTests(unittest.TestCase):
    def test_dev_implementation_entry(self):
        spec = json.loads(SPEC.read_text())
        slot = next(s for s in spec["slots"] if s["id"] == 35)
        self.assertEqual(slot["candidate_name"], "EX_SCRSIZ")
        self.assertEqual(slot["state"], "NOT_READY")
        self.assertEqual(slot["abi_contract"]["status"], "PROJECT_SPEC_ONLY")
        self.assertEqual(slot["implementation"]["source"], "src/overrides/slot_23.s")
        self.assertIn("tests/host/test_api_scrsiz_dev.py", slot["host_evidence"])
        self.assertIn("tests/integration/geargrafx_dev_scrsiz.py", slot["real_core_evidence"])


if __name__ == "__main__":
    unittest.main()
