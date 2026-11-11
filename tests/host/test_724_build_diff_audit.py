"""Regression test for Issue #724: debug/release diff audit and release isolation.

Static only: no cc65, no Geargrafx core, no private media. Verifies that the
fail-closed gate in tools/verify_build_diff.py keeps passing on HEAD and that
it fails closed on injected verification-only/GPL markers and on new
mode-dependent branches.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import verify_build_diff as VBD  # noqa: E402
from build_rom import ReleaseBlocked, release_gate  # noqa: E402


def _copy_into(tmp: Path, rel: str) -> None:
    src = ROOT / rel
    dst = tmp / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _minimal_scan_root(tmp: Path) -> Path:
    for rel in ("src/boot.s", "src/bank0.cfg",
                "tests/fixtures/smoke.s", "tests/fixtures/smoke.cfg",
                "tools/build.py", "tools/build_rom.py",
                "tools/environment.py", "tools/rom.py",
                "spec/api_slots.json"):
        _copy_into(tmp, rel)
    return tmp


class BuildDiffAuditGate(unittest.TestCase):
    def test_head_release_inputs_clean(self):
        self.assertEqual(VBD.scan_release_inputs(ROOT), [])
        self.assertEqual(VBD.check_build_imports(ROOT), [])
        self.assertEqual(VBD.check_mode_branches(ROOT), [])

    def test_head_release_gates_intact(self):
        problems = VBD.check_release_gates(ROOT)
        self.assertEqual(problems, [], f"release gates regressed: {problems}")

    def test_head_patch_isolation_intact(self):
        problems = VBD.check_patch_isolation(ROOT)
        self.assertEqual(problems, [], f"patch isolation regressed: {problems}")

    def test_release_gate_refuses_even_fully_overridden(self):
        for slots in (set(), set(range(81))):
            with self.assertRaises(ReleaseBlocked):
                release_gate(slots)
        self.assertTrue(issubclass(ReleaseBlocked, ValueError))

    def test_mutation_hook_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            target = tmp / "src/boot.s"
            target.write_text(target.read_text(encoding="utf-8") + "\n; gg_verify hook must not ship\n",
                              encoding="utf-8")
            problems = VBD.scan_release_inputs(tmp)
            self.assertTrue(any("boot.s" in p and "gg_verify" in p for p in problems),
                            f"injected hook marker not detected: {problems}")

    def test_mutation_gpl_text_fails_closed(self):
        marker = "GNU General " "Public License"
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            target = tmp / "tools/build.py"
            target.write_text(target.read_text(encoding="utf-8") + f"\n# {marker}\n",
                              encoding="utf-8")
            problems = VBD.scan_release_inputs(tmp)
            self.assertTrue(any("build.py" in p for p in problems),
                            f"injected GPL text not detected: {problems}")

    def test_mutation_new_mode_branch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/build.py", tmp / "tools/build.py")
            shutil.copyfile(ROOT / "tools/build_rom.py", tmp / "tools/build_rom.py")
            poisoned = (tmp / "tools/build.py").read_text(encoding="utf-8")
            poisoned += '\nif mode == "release":\n    source = source.with_name("stock-core.pce")\n'
            (tmp / "tools/build.py").write_text(poisoned, encoding="utf-8")
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("build.py" in p and "mode" in p for p in problems),
                            f"new mode branch not detected: {problems}")

    def test_mutation_probe_import_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "tools").mkdir(parents=True)
            for name in ("build.py", "build_rom.py", "environment.py", "rom.py"):
                shutil.copyfile(ROOT / f"tools/{name}", tmp / f"tools/{name}")
            text = (tmp / "tools/build_rom.py").read_text(encoding="utf-8")
            text += "\nfrom geargrafx_api import compile_probe\n"
            (tmp / "tools/build_rom.py").write_text(text, encoding="utf-8")
            problems = VBD.check_build_imports(tmp)
            self.assertTrue(any("compile_probe" in p for p in problems),
                            f"probe import not detected: {problems}")

    def test_report_keeps_unknowns_and_release_eligible_false(self):
        report = VBD.build_report(ROOT)
        self.assertEqual(report["verdict"], "PASS", f"gate fails on HEAD: {report['problems']}")
        self.assertEqual(report["release_gates"]["release_eligible"], False)
        unknowns = report["unmeasured_UNKNOWN"]
        for key in ("free_run_equivalence", "libretro_equivalence",
                    "release_equivalence", "production_safety"):
            self.assertIn("UNKNOWN" if key != "production_safety" else "NOT PASS",
                          unknowns[key])
        self.assertTrue(unknowns["production_safety"].startswith("NOT PASS"),
                        unknowns["production_safety"])
        for rel in ("spec/dev_implementations.json", "spec/abi1_reference_review.json",
                    "spec/hardware_observations.json"):
            payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            self.assertIs(payload.get("release_eligible"), False)

    def test_cli_check_passes_on_head(self):
        self.assertEqual(VBD.main([]), 0)


if __name__ == "__main__":
    unittest.main()
