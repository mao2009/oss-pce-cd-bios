"""Host tests for the CD_SEEK ($04 / $E00C) fail-closed safety gate (Issue #217).

Synthetic only: exercises ``tools/cdseek_safety_gate.py`` (pure decision + CLI).
No emulator is launched, no reference BIOS is read and no reference JSR runs, so the JSR count
is 0 by construction. The real-core negative evidence for the hazards is the existing
``tests/integration/geargrafx_st_bypass.py`` and ``geargrafx_mmio1804_preblock.py`` (see docs).
"""
import importlib.util
import inspect
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
        self.assertEqual(len(self.gate.HAZARDS), 8)
        self.assertEqual(len(record["reasons"]), 8)
        for (hid, name, why, control), reason in zip(self.gate.HAZARDS, record["reasons"]):
            self.assertIn(f"{hid} {name}", reason)
            self.assertIn(why, reason)
            self.assertIn(control, reason)

    def test_no_general_preblock_control_exists(self):
        # Fail-closed premise: the exact per-hazard controls are fixed; none is a general pre-side-effect control.
        expected = {
            "H1": self.gate.NONE, "H2": self.gate.KNOWN_PC_ONLY, "H3": self.gate.NONE,
            "H4": self.gate.DETECT_ONLY, "H5": self.gate.TAM_BOUNDARY_SYNTHETIC,
            "H6": self.gate.PREVENT_CPU_ONLY, "H7": self.gate.NONE, "H8": self.gate.POST_HOC,
        }
        actual = {hid: control for hid, _, _, control in self.gate.HAZARDS}
        self.assertEqual(actual, expected)

    def test_decide_takes_no_input(self):
        # No unlock parameter exists: the gate cannot be steered by a posture argument.
        self.assertEqual(list(inspect.signature(self.gate.decide).parameters), [])

    def test_vdc_st_bypass_and_device_effect_have_no_control(self):
        by_id = {hid: control for hid, _, _, control in self.gate.HAZARDS}
        self.assertEqual(by_id["H1"], self.gate.NONE)
        self.assertEqual(by_id["H3"], self.gate.NONE)
        self.assertEqual(by_id["H7"], self.gate.NONE)

    def test_cli_exits_blocked_and_reports_zero_jsr(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdseek_safety_gate.py")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, self.gate.EXIT_BLOCKED)
        self.assertIn('"verdict": "BLOCKED"', result.stdout)
        self.assertIn('"jsr_count": 0', result.stdout)


if __name__ == "__main__":
    unittest.main()
