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

    def test_mutation_helper_indirection_fails_closed(self):
        # MAJOR allowlist: a bare helper call under a mode branch must RED even
        # though the callee name is not in any denylist (e.g. modify_rom()
        # writing ROM bytes under `if mode == "release"`). Aliases, nested
        # closures, methods, and indirect/dynamic callees are unprovable too.
        mutants = [
            ("tools/build.py",
             '\ndef modify_rom():\n    pass\nif mode == "release":\n    modify_rom()\n',
             "modify_rom"),
            ("tools/build.py",
             '\nif mode == "release":\n    f()\n',
             "unprovable"),
            ("tools/build.py",
             '\nif mode == "release":\n    if True:\n        helper()\n',
             "unprovable"),
            ("tools/build.py",
             '\nif mode == "release":\n    obj.write_rom(data)\n',
             "write_rom"),
            ("tools/build.py",
             '\nif mode == "release":\n    source.with_name("x.s")\n',
             "with_name"),
            ("tools/build.py",
             '\nif mode == "release":\n    handlers[mode]()\n',
             "indirect"),
            ("tools/build_rom.py",
             '\nif mode == "release":\n    getattr(obj, "write")(data)\n',
             "indirect"),
        ]
        for rel, extra, hint in mutants:
            with self.subTest(extra=extra.strip().splitlines()[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    tmp = _mode_mutation_root(directory, rel, extra)
                    problems = VBD.check_mode_branches(tmp)
                    self.assertTrue(any(hint in p for p in problems),
                                    f"helper-indirection {hint!r} not detected: {problems}")

    def test_allowed_debug_only_ops_stay_green(self):
        # The allowlist is debug flags/listings, validation, logging, and the
        # manifest echo: these must not fail closed.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nif mode == "debug":\n    command += ["-g"]\n    print("debug mode")\n')
            self.assertEqual(VBD.check_mode_branches(tmp), [])
        # MAJOR-2: strict output-path shapes stay GREEN -- a `-l` listing
        # rooted at the output directory, an extend() of the same shape,
        # and a ternary selecting between two pure output paths.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _mode_mutation_root(
                directory, "tools/build.py",
                '\nif mode == "debug":\n    command += ["-g", "-l", str(output / "smoke.lst")]\n'
                '    command.extend(["-l", str(output / "extra.lst")])\n'
                'listing = str(output / "a.lst") if mode == "debug" else str(output / "b.lst")\n')
            self.assertEqual(VBD.check_mode_branches(tmp), [])

    def test_mutation_output_path_expr_calls_fail_closed(self):
        # MAJOR-2: the output-path check is a structural allowlist -- name
        # presence alone never allows. A call smuggling an output name
        # (`modify_rom(output)`) must RED even where a bare output path
        # would pass: in `-l` command operands, in ternaries on either
        # side, nested inside another conditional, or mixed with ROM inputs.
        mutants = [
            # function/alias calls hiding behind the `output` name
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-l", modify_rom(output)]\n',
             "output"),
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-l", helper(output)]\n',
             "output"),
            # method/object calls with output args
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-g", obj.write(output)]\n',
             "output"),
            # indirect/dynamic callee with an output arg
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-l", handlers[mode](output)]\n',
             "output"),
            # mixed safe-name operand plus a dangerous call
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-g", str(output / "a.lst"), evil(output)]\n',
             "output"),
            # release-side-only ternary feeding the command (outside an
            # `if` region, so no region call-walk covers it)
            ("tools/build.py",
             '\ncommand += (["-g"] if mode == "debug" else ["-l", evil(output)])\n',
             "command"),
            ("tools/build.py",
             '\ncommand = command + (["-g"] if mode == "debug" else ["-l", evil(output)])\n',
             "command"),
            # ternary values selecting a call on either side
            ("tools/build.py",
             '\nlisting = str(output / "a.lst") if mode == "debug" else modify_rom(output)\n',
             "ternary"),
            ("tools/build.py",
             '\nlisting = evil(output) if mode == "debug" else str(output / "a.lst")\n',
             "ternary"),
            ("tools/build.py",
             '\nlisting = str(output / "a.lst") if mode == "debug" else handlers[mode](output)\n',
             "ternary"),
            # nested conditional inside a would-be path operand
            ("tools/build.py",
             '\nlisting = (str(output / "a.lst") if flag else evil(output))'
             ' if mode == "debug" else str(output / "b.lst")\n',
             "ternary"),
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-l",'
             ' (str(output / "a.lst") if mode == "debug" else evil(output))]\n',
             "output"),
            # unapproved ROM inputs in command args (bare or mixed in)
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-l", str(source)]\n',
             "output"),
            ("tools/build.py",
             '\nlisting = str(output / "a.lst") if mode == "debug" else str(output / source)\n',
             "ternary"),
            # wrong flag where a listing path belongs
            ("tools/build.py",
             '\nif mode == "release":\n    command += ["-o", str(output / "x.o")]\n',
             "command"),
        ]
        for rel, extra, hint in mutants:
            with self.subTest(extra=extra.strip().splitlines()[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    tmp = _mode_mutation_root(directory, rel, extra)
                    problems = VBD.check_mode_branches(tmp)
                    self.assertTrue(any(hint in p for p in problems),
                                    f"output-path-expr {hint!r} bypass not detected: {problems}")

    def test_release_recipe_decoy_outside_target_fails_closed(self):
        # check_release_gates must parse the exact `release:` recipe, not a
        # blacklist over BLOCKED/exit-1 substrings anywhere in the file. A
        # gutted release recipe plus a decoy comment elsewhere must still RED.
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools/build.py", tmp / "tools/build.py")
            shutil.copyfile(ROOT / "tools/build_rom.py", tmp / "tools/build_rom.py")
            text = (ROOT / "Makefile").read_text(encoding="utf-8")
            gutted = text.replace(
                "@echo 'BLOCKED: BIOS APIs and System Card boot are unimplemented;"
                " no BIOS release is available.' >&2\n\t@exit 1",
                "@echo hi")
            # Decoy strings outside the release target (old blacklist PASSes).
            gutted += "\n# decoy BLOCKED @exit 1 (not the release recipe)\n"
            (tmp / "Makefile").write_text(gutted, encoding="utf-8")
            problems = VBD.check_release_gates(tmp)
            self.assertTrue(any("release" in p for p in problems),
                            f"decoy BLOCKED/exit-1 outside release target not refused: {problems}")

    def _gate_tmp_root(self, directory: str, makefile_text: str | None = None) -> Path:
        # Temp root for check_release_gates delegation tests: HEAD Makefile
        # (or the given text) plus the tools the gate loads (shared guard +
        # build_rom). Static only, nothing executed.
        from release_makefile_guard import check_release_makefile as _shared  # noqa: E402,F401
        tmp = Path(directory)
        (tmp / "tools").mkdir(parents=True)
        shutil.copyfile(ROOT / "tools/build_rom.py", tmp / "tools/build_rom.py")
        shutil.copyfile(ROOT / "tools/release_makefile_guard.py",
                        tmp / "tools/release_makefile_guard.py")
        if makefile_text is None:
            makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
        (tmp / "Makefile").write_text(makefile_text, encoding="utf-8")
        return tmp

    def test_release_gate_delegates_to_shared_guard(self):
        # MAJOR-3: both gates share one contract. Legit tree => both empty;
        # every #769-class mutant => both non-empty (no判定差).
        from release_makefile_guard import check_release_makefile as shared
        self.assertEqual(VBD.check_release_gates(ROOT), [])
        self.assertEqual(shared(ROOT), [])
        mutants = [
            "include extras.mk\n",
            "-include extras.mk\n",
            "sinclude extras.mk\n",
            "$(eval release: preflight)\n",
            "release: preflight\n",
            "release::\n\t@exit 1\n",
            ".RECIPEPREFIX = >\n",
            "release:\n\t@echo 'BLOCKED: $(shell printf MAKE_EXPANDED)' >&2\n\t@exit 1\n",
        ]
        base = (ROOT / "Makefile").read_text(encoding="utf-8")
        for mutant in mutants:
            with tempfile.TemporaryDirectory() as directory:
                tmp = self._gate_tmp_root(directory, base + mutant)
                gate_problems = VBD.check_release_gates(tmp)
                shared_problems = shared(tmp)
                self.assertTrue(gate_problems,
                                f"shared-guard mutant not refused by gate: {mutant!r}")
                self.assertTrue(shared_problems,
                                f"shared guard missed its own mutant: {mutant!r}")

    def test_release_gate_external_include_prereq_add_fails_closed(self):
        # MAJOR-3 headline: release stub intact but an included file adds a
        # `release: preflight` prerequisite, so `preflight` would run before
        # the refusal. Nonzero exit alone proves nothing => RED.
        base = (ROOT / "Makefile").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            tmp = self._gate_tmp_root(directory, base + "include extras.mk\n")
            (tmp / "extras.mk").write_text(
                "release: preflight\n\npreflight:\n\t@printf 'SYNTHETIC_PREFLIGHT\\n'\n",
                encoding="utf-8")
            problems = VBD.check_release_gates(tmp)
            self.assertTrue(any("include" in p.lower() or "release" in p.lower()
                                for p in problems),
                            f"external-include prereq add not refused: {problems}")

    def test_release_gate_eval_redefinition_fails_closed(self):
        base = (ROOT / "Makefile").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            tmp = self._gate_tmp_root(directory, base + "$(eval release: preflight)\n")
            problems = VBD.check_release_gates(tmp)
            self.assertTrue(any("eval" in p.lower() or "release" in p.lower()
                                for p in problems),
                            f"$(eval) redefinition not refused: {problems}")

    def test_artifact_identical_outputs_checked(self):
        # Byte evidence comes from a fresh rebuild, never from stale build/
        # files: identical rebuilt bytes CHECK with matching sha256.
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            blob = b"PCE!" + b"\xff" * 8188
            with mock.patch.object(VBD, "_rebuild_mode_roms",
                                    return_value=(blob, blob, "mocked rebuild OK")):
                problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertEqual(problems, [])
            self.assertTrue(disposition.startswith("CHECKED"), disposition)
            self.assertTrue(evidence["compared"])
            self.assertEqual(evidence["debug_sha256"], evidence["release_sha256"])
            self.assertIn("rebuilt", evidence["source"])

    def test_artifact_differing_outputs_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            debug_blob = b"PCE!" + b"\xff" * 8188
            release_blob = b"PCE!" + b"\x00" * 8188
            with mock.patch.object(VBD, "_rebuild_mode_roms",
                                    return_value=(debug_blob, release_blob, "mocked rebuild OK")):
                problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertTrue(any("differs" in p for p in problems),
                            f"differing ROMs not detected: {problems} / {disposition}")
            self.assertTrue(evidence["compared"])
            self.assertNotEqual(evidence["debug_sha256"], evidence["release_sha256"])

    def test_artifact_stale_identical_roms_ignored(self):
        # MEDIUM: pre-existing identical build/debug+build/release ROMs must
        # never PASS as current evidence. Stale blobs are ignored; the verdict
        # follows the fresh rebuild (FAIL when rebuilt bytes differ, SKIPPED
        # with toolchain status when unbuildable).
        with tempfile.TemporaryDirectory() as directory:
            tmp = _minimal_scan_root(Path(directory))
            stale = b"PCE!" + b"\xff" * 8188
            for mode in ("debug", "release"):
                out = tmp / f"build/{mode}"
                out.mkdir(parents=True)
                (out / "smoke-test-not-bios.pce").write_bytes(stale)
            # Rebuild differs -> FAIL despite stale-identical files.
            with mock.patch.object(VBD, "_rebuild_mode_roms",
                                    return_value=(b"A" * 8192, b"B" * 8192,
                                                  "mocked: current sources differ")):
                problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertTrue(any("differs" in p for p in problems),
                            f"stale-identical ROMs masked a real rebuild diff: {disposition}")
            self.assertTrue(evidence["compared"])
            self.assertIn("rebuilt", evidence["source"])
            # Rebuild unbuildable -> SKIPPED (never PASS from stale).
            with mock.patch.object(VBD, "_rebuild_mode_roms",
                                    return_value=(None, None,
                                                  "mocked: pinned cc65 unavailable")):
                problems, disposition, evidence = VBD.scan_release_artifacts(tmp)
            self.assertEqual(problems, [])
            self.assertTrue(disposition.startswith("SKIPPED"), disposition)
            self.assertFalse(evidence["compared"])

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
        self.assertIn("rebuilt", evidence["source"])
        self.assertTrue(evidence.get("cc65_revision"), f"missing toolchain evidence: {evidence}")
        self.assertTrue(evidence.get("rebuild_status"), f"missing rebuild status: {evidence}")
        report = VBD.build_report(ROOT)
        self.assertEqual(report["byte_identity"]["verdict"], "PASS")
        self.assertEqual(report["byte_identity"]["debug_sha256"],
                          report["byte_identity"]["release_sha256"])
        self.assertTrue(report["byte_identity"].get("cc65_revision"))
        self.assertTrue(report["byte_identity"].get("rebuild_status"))

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
