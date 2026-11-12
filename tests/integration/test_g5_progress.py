"""Focused integration checks for the G5 harness (Agent C, parent #635).

Public and reproducible without private media:
- variant listing + evidence skeleton need no core/cc65/media;
- the synthetic A/B/C measurement runs only when the pinned cc65 tools,
  g++, the Geargrafx core sources, and the DEV ROM build inputs are all
  present; otherwise it SKIPs with the actual reason (never PASS).
- commercial media is never required: absence is reported as NOT_RUN.

Measurement/diagnosis is the deliverable; boot failure classification is
success. This test never claims compatibility and never touches private
./rom/ inputs.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
import g5_progress_model as M  # noqa: E402


class G5HarnessStaticTests(unittest.TestCase):
    def test_list_variants(self):
        specs = {k: M.variant_spec(k) for k in ("A", "B", "C", "D")}
        self.assertEqual(specs["A"]["return_kind"], "NONE")
        self.assertEqual(specs["B"]["return_kind"], "FAKE_RTS")
        self.assertEqual(specs["C"]["return_kind"], "FAKE_RTS")
        self.assertEqual(specs["D"]["return_kind"], "REAL_SERVICE")

    def test_harness_module_imports(self):
        import g5_harness as H  # noqa: E402
        self.assertEqual(H.entry_of(0x02), 0xE006)
        self.assertEqual(H.entry_of(0x33), 0xE099)
        self.assertEqual(list(H.list_variants()), ["A", "B", "C", "D"])

    def test_evidence_skeleton_pins(self):
        e = M.evidence_skeleton(head="0" * 40, mode="debug")
        self.assertEqual(e["pinned_geargrafx"], M.PINNED_GEARGRAFX)
        self.assertEqual(e["pinned_cc65"], M.PINNED_CC65)
        self.assertIs(e["release_eligible"], False)


class G5SyntheticMeasurementTests(unittest.TestCase):
    """Pinned real-core A/B/C differential; SKIP with reason when unavailable."""

    def _skip_reason(self):
        import shutil
        try:
            import environment as env2  # noqa: E402 (tools/ is on sys.path)
            reason = env2.cc65_skip_reason()
        except Exception:
            reason = "environment unavailable"
        if reason is not None:
            return reason
        if not shutil.which("g++"):
            return "g++ unavailable"
        # Pinned core sources (debugger build inputs).
        try:
            from tools.environment import dependency_dir  # noqa: E402
            core = dependency_dir("geargrafx").with_name(
                dependency_dir("geargrafx").name + "-debugger")
            if not (core / "platforms" / "libretro").is_dir():
                return "pinned Geargrafx sources absent (run make setup)"
        except Exception as e:
            return f"core path unresolved ({type(e).__name__})"
        for mode in ("debug",):
            rom = ROOT / "build" / mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
            if not rom.is_file():
                return f"DEV ROM absent ({rom.relative_to(ROOT)}; run make dev-rom)"
        return None

    def test_synthetic_abc_differential(self):
        reason = self._skip_reason()
        if reason:
            self.skipTest(f"SKIP real-core G5 synthetic (not attempted): {reason}")
        import subprocess
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "g5_harness.py"),
             "--mode", "debug", "--variants", "A", "B", "C"],
            capture_output=True, text=True, timeout=1500)
        self.assertEqual(
            completed.returncode, 0,
            f"g5 harness synthetic failed:\n{completed.stdout}\n{completed.stderr}")
        out = ROOT / "build" / "debug" / "g5-harness" / "g5-evidence.json"
        self.assertTrue(out.is_file())
        evidence = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(evidence["schema"], M.SCHEMA)
        self.assertEqual(evidence["pinned_geargrafx"], M.PINNED_GEARGRAFX)
        abc = evidence.get("comparison_abc", {})
        # The instrument differential: each fake return advances one more API.
        self.assertEqual((abc.get("A") or {}).get("apis"), 1)
        self.assertEqual((abc.get("B") or {}).get("apis"), 2)
        self.assertEqual((abc.get("C") or {}).get("apis"), 3)


if __name__ == "__main__":
    unittest.main()
