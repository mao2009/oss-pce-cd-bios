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
from unittest import mock

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
                "src/overrides/slot_3C.s", "src/overrides/slot_3D.s",
                "src/ex_bcdbin.s", "src/ex_binbcd.s",
                "tests/fixtures/smoke.s", "tests/fixtures/smoke.cfg",
                "tools/build.py", "tools/build_rom.py",
                "tools/environment.py", "tools/rom.py",
                "spec/api_slots.json"):
        _copy_into(tmp, rel)
    return tmp


def _mode_mutation_root(directory: str, rel: str, extra: str) -> Path:
    """Tmp root with HEAD build files plus `extra` appended to `rel` (a mode-fork mutant)."""
    tmp = Path(directory)
    (tmp / "tools").mkdir(parents=True)
    shutil.copyfile(ROOT / "tools/build.py", tmp / "tools/build.py")
    shutil.copyfile(ROOT / "tools/build_rom.py", tmp / "tools/build_rom.py")
    target = tmp / rel
    target.write_text(target.read_text(encoding="utf-8") + extra, encoding="utf-8")
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

    def test_mutation_unaudited_include_target_fails_closed(self):
        # MAJOR-1: src/ex_bcdbin.s reaches ROM bytes via slot_3D.s `.include`.
        # A hook/GPL marker there must RED even though the file is not named
        # directly by the builder command line (include-closure coverage).
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            target = tmp / "src/ex_bcdbin.s"
            target.write_text(target.read_text(encoding="utf-8") + "\n; gg_verify hook must not ship\n",
                              encoding="utf-8")
            problems = VBD.scan_release_inputs(tmp)
            self.assertTrue(any("ex_bcdbin.s" in p and "gg_verify" in p for p in problems),
                            f"marker in .include target not detected: {problems}")

    def test_mutation_new_include_file_fails_closed(self):
        # A newly added .include pointing at a fresh file must be pulled into
        # the scan: a marker in the new file REDs, so coverage cannot be
        # dodged by adding includes the gate never read.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            evil = tmp / "src/ex_evil.s"
            evil.write_text("; unaudited include body\n; gg_verify hook must not ship\n",
                            encoding="utf-8")
            host = tmp / "src/overrides/slot_3D.s"
            host.write_text(host.read_text(encoding="utf-8") + '\n.include "../ex_evil.s"\n',
                            encoding="utf-8")
            problems = VBD.scan_release_inputs(tmp)
            self.assertTrue(any("ex_evil.s" in p and "gg_verify" in p for p in problems),
                            f"new include target not scanned: {problems}")

    def test_mutation_unresolvable_include_fails_closed(self):
        # An .include whose target cannot be resolved (missing file) is an
        # unaudited byte source and must refuse fail-closed, never pass.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            host = tmp / "src/overrides/slot_3D.s"
            host.write_text(host.read_text(encoding="utf-8") + '\n.include "../does_not_exist.s"\n',
                            encoding="utf-8")
            problems = VBD.scan_release_inputs(tmp)
            self.assertTrue(any("does_not_exist.s" in p for p in problems),
                            f"unresolvable include not refused: {problems}")

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

    def test_mutation_args_mode_source_swap_fails_closed(self):
        # MAJOR-2 bypass class: the pre-fix line-regex allowed any line
        # containing `args.mode`, so a mode-gated ROM-input swap was missed.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nif args.mode == "release":\n    source = source.with_name("stock-core.pce")\n')
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("build.py" in p and "source" in p for p in problems),
                            f"args.mode source swap not detected: {problems}")

    def test_mutation_debug_alias_fork_fails_closed(self):
        # MAJOR-2 bypass class: `debug` is assigned from a mode test, so a
        # branch on the alias with no `mode` name on its line was unchecked.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build_rom.py",
                '\nif debug:\n    objects = objects[:1]\n')
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("build_rom.py" in p and "objects" in p for p in problems),
                            f"debug-alias link-input fork not detected: {problems}")

    def test_mutation_environ_mode_fork_fails_closed(self):
        # MAJOR-2 bypass class: the pre-fix line-regex allowed any line
        # containing `os.environ.get(`, so a MODE-environ fork was missed.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nimport os\nif os.environ.get("MODE", "debug") == "release":\n'
                '    config = config.with_name("other.cfg")\n')
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("build.py" in p and "config" in p for p in problems),
                            f"MODE-environ config fork not detected: {problems}")

    def test_mutation_ternary_source_fork_fails_closed(self):
        # MAJOR-2 bypass class: a ternary selects a ROM input by mode without
        # any `if mode` line, so the line-regex never saw it.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nsource = source if mode == "debug" else source.with_name("x.s")\n')
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("ternary" in p for p in problems),
                            f"mode ternary source fork not detected: {problems}")

    def test_mutation_conditional_import_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build_rom.py",
                '\nif mode == "debug":\n    import geargrafx_api\n')
            problems = VBD.check_mode_branches(tmp)
            self.assertTrue(any("import" in p for p in problems),
                            f"mode-gated import not detected: {problems}")

    def test_mutation_conditional_side_effects_fail_closed(self):
        mutants = [
            ("tools/build.py",
             '\nif mode == "debug":\n    subprocess.run(["echo", "probe"], check=True)\n',
             "side-effect"),
            ("tools/build.py",
             '\nif mode == "debug":\n    command += ["-D", "RELEASE=1"]\n',
             "command"),
            ("tools/build_rom.py",
             '\nif mode == "release":\n    objects.append(obj)\n',
             "append"),
            ("tools/build.py",
             '\nif mode == "release":\n    metadata["rom_sha256"] = "00" * 32\n',
             "write"),
            ("tools/build.py",
             '\nif mode == "release":\n    return "evil"\n',
             "control-flow"),
        ]
        for rel, extra, hint in mutants:
            with self.subTest(extra=extra.strip().splitlines()[0]):
                with tempfile.TemporaryDirectory() as directory:
                    tmp = _mode_mutation_root(directory, rel, extra)
                    problems = VBD.check_mode_branches(tmp)
                    self.assertTrue(any(hint in p for p in problems),
                                    f"mode-gated {hint} not detected: {problems}")

    def test_allowed_debug_only_ops_stay_green(self):
        # The allowlist is debug flags/listings, validation, logging, and the
        # manifest echo: these must not fail closed.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nif mode == "debug":\n    command += ["-g"]\n    print("debug mode")\n')
            self.assertEqual(VBD.check_mode_branches(tmp), [])

    def test_artifact_identical_outputs_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            blob = b"PCE!" + b"\xff" * 8188
            for mode in ("debug", "release"):
                out = tmp / f"build/{mode}"
                out.mkdir(parents=True)
                (out / "smoke-test-not-bios.pce").write_bytes(blob)
            problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertEqual(problems, [])
            self.assertTrue(disposition.startswith("CHECKED"), disposition)
            self.assertTrue(evidence["compared"])
            self.assertEqual(evidence["debug_sha256"], evidence["release_sha256"])

    def test_artifact_differing_outputs_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            for mode, fill in (("debug", b"\xff"), ("release", b"\x00")):
                out = tmp / f"build/{mode}"
                out.mkdir(parents=True)
                (out / "smoke-test-not-bios.pce").write_bytes(b"PCE!" + fill * 8188)
            problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertTrue(any("differs" in p for p in problems),
                            f"differing ROMs not detected: {problems} / {disposition}")
            self.assertTrue(evidence["compared"])
            self.assertNotEqual(evidence["debug_sha256"], evidence["release_sha256"])

    def test_artifact_skipped_is_never_pass(self):
        # No build outputs and no rebuildable sources: the comparison is
        # SKIPPED, and the byte-identity verdict must not read PASS.
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/build.py", tmp / "tools/build.py")
            shutil.copyfile(ROOT / "tools/build_rom.py", tmp / "tools/build_rom.py")
            problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertEqual(problems, [])
            self.assertTrue(disposition.startswith("SKIPPED"), disposition)
            self.assertFalse(evidence["compared"])
        # build_report wiring with an unavailable toolchain (simulated): a
        # skipped comparison never yields byte_identity PASS.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            with mock.patch.object(VBD, "_rebuild_mode_roms",
                                    return_value=(None, None, "simulated: pinned cc65 unavailable")):
                report = VBD.build_report(tmp)
            self.assertTrue(report["release_artifact_scan"].startswith("SKIPPED"),
                            report["release_artifact_scan"])
            self.assertEqual(report["byte_identity"]["verdict"], "SKIPPED")
            self.assertNotEqual(report["byte_identity"]["verdict"], "PASS")

    def test_head_byte_identity_compared_when_buildable(self):
        try:
            from environment import cc65_skip_reason  # noqa: E402
        except Exception as error:  # noqa: BLE001
            self.skipTest(f"toolchain check unavailable: {error}")
        reason = cc65_skip_reason()
        if reason is not None:
            self.skipTest(f"SKIP cc65-dependent: {reason}")
        problems, disposition, evidence = VBD.scan_release_artifacts(ROOT)
        self.assertTrue(evidence["compared"], disposition)
        self.assertEqual(problems, [], f"HEAD rebuilt ROMs differ: {problems}")
        self.assertEqual(evidence["debug_sha256"], evidence["release_sha256"])
        report = VBD.build_report(ROOT)
        self.assertEqual(report["byte_identity"]["verdict"], "PASS")

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

    def test_report_byte_identity_never_pass_when_skipped(self):
        report = VBD.build_report(ROOT)
        verdict = report["byte_identity"]["verdict"]
        self.assertIn(verdict, ("PASS", "FAIL", "SKIPPED"))
        if verdict == "PASS":
            self.assertTrue(report["byte_identity"]["detail"].startswith("CHECKED"))
            self.assertEqual(report["byte_identity"]["debug_sha256"],
                              report["byte_identity"]["release_sha256"])
        else:
            self.assertFalse(report["byte_identity"]["detail"].startswith("CHECKED (byte-identical"))

    def test_cli_check_passes_on_head(self):
        self.assertEqual(VBD.main([]), 0)


if __name__ == "__main__":
    unittest.main()
