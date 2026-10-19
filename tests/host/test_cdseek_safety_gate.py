"""Host tests for the CD_SEEK ($04 / $E00C) fail-closed safety gate (Issue #217).

Synthetic only: exercises ``tools/cdseek_safety_gate.py`` (pure decision + ``--check`` CLI).
No emulator is launched, no reference BIOS is read and no reference JSR runs, so the JSR count
is 0 by construction. The real-core negative evidence for the hazards is the existing
``tests/integration/geargrafx_st_bypass.py`` and ``geargrafx_mmio1804_preblock.py`` (see docs).
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_gate():
    path = ROOT / "tools" / "cdseek_safety_gate.py"
    spec = importlib.util.spec_from_file_location("cdseek_safety_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CdSeekSafetyGateTest(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()

    def test_verdict_is_blocked_with_zero_jsr(self):
        record = self.gate.decide()
        self.assertEqual(record["verdict"], "BLOCKED")
        self.assertEqual(record["jsr_count"], 0)
        self.assertEqual((record["slot"], record["candidate"]), ("$04", "$E00C"))

    def test_every_hazard_is_reported(self):
        record = self.gate.decide()
        self.assertEqual(len(record["reasons"]), len(self.gate.HAZARDS))
        self.assertEqual(len(self.gate.HAZARDS), 8)

    def test_no_general_preblock_control_exists(self):
        # Fail-closed premise: no hazard has a general pre-side-effect control on the pinned core.
        controls = {control for _, _, _, control in self.gate.HAZARDS}
        self.assertTrue(controls <= {self.gate.NONE, self.gate.DETECT_ONLY, self.gate.POST_HOC,
                                     self.gate.KNOWN_PC_ONLY})
        self.assertNotIn("PREBLOCK", controls)

    def test_vdc_st_bypass_and_device_effect_have_no_control(self):
        by_id = {hid: control for hid, _, _, control in self.gate.HAZARDS}
        self.assertEqual(by_id["H1"], self.gate.NONE)
        self.assertEqual(by_id["H3"], self.gate.NONE)
        self.assertEqual(by_id["H7"], self.gate.NONE)

    def test_cli_check_exits_blocked(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdseek_safety_gate.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, self.gate.EXIT_BLOCKED)
        self.assertIn('"verdict": "BLOCKED"', result.stdout)
        self.assertIn('"jsr_count": 0', result.stdout)

    def test_cli_without_check_exits_zero_and_still_blocked(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdseek_safety_gate.py")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('"verdict": "BLOCKED"', result.stdout)


if __name__ == "__main__":
    unittest.main()
