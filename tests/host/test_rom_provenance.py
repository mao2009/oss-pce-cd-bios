"""Issue #335: PCE ROM provenance record and same-input reproducibility checks.

Host-only: no cc65 and no real build. Builders are injected fakes, so these tests run without private inputs.
Each positive case has a negative counterpart, and each negative case is a tampered copy of a passing record.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import rom_provenance as rp  # noqa: E402
from environment import DEPENDENCIES  # noqa: E402

COMMIT = "a" * 40
OTHER = "b" * 40
ROM = bytes(range(256)) * 4


def rom_builder(out: Path) -> Path:
    """A fake builder that writes the ROM and a matching manifest into out (no cc65)."""
    out.mkdir(parents=True, exist_ok=True)
    rom_path = out / rp.BUILDERS["smoke"][1]
    rom_path.write_bytes(ROM)
    (out / "manifest.json").write_text(json.dumps(record()), encoding="utf-8")
    return rom_path


def make_link(link: Path, target: Path) -> None:
    """Create a Windows junction (no admin needed) or a symlink; skip the test if neither can be made."""
    if os.name == "nt" and subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                          capture_output=True).returncode == 0:
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        raise unittest.SkipTest(f"junction and symlink unavailable on this OS/account: {error}")


def record(rom=ROM, **overrides):
    manifest = {"schema_version": 1, "status": "development-diagnostic-not-compatible-bios",
                "source_sha": COMMIT, "working_tree_dirty": False, "source_root_is_git_repo": True,
                "rom_bytes": len(rom), "rom_sha256": rp.sha256(rom),
                "cc65_revision": DEPENDENCIES["cc65"]["revision"], "release_eligible": False}
    manifest.update(overrides)
    return manifest


class RecordTests(unittest.TestCase):
    def test_matching_record_passes(self):
        self.assertEqual([], rp.check_record(ROM, record(), COMMIT))

    def test_tampered_rom_byte_fails(self):
        tampered = bytearray(ROM)
        tampered[17] ^= 0x01
        problems = rp.check_record(bytes(tampered), record(), COMMIT)
        self.assertTrue(any("rom_sha256" in p for p in problems))

    def test_manifest_hash_edited_to_match_other_rom_fails(self):
        tampered = bytearray(ROM)
        tampered[0] ^= 0xFF
        forged = record(rom=bytes(tampered))  # a manifest written for the tampered bytes
        problems = rp.check_record(ROM, forged, COMMIT)
        self.assertTrue(any("rom_sha256" in p for p in problems))

    def test_size_mismatch_fails(self):
        problems = rp.check_record(ROM + b"\xff", record(), COMMIT)
        self.assertTrue(any("rom_bytes" in p for p in problems))

    def test_wrong_source_commit_fails(self):
        problems = rp.check_record(ROM, record(source_sha=OTHER), COMMIT)
        self.assertTrue(any("expected commit" in p for p in problems))

    def test_missing_or_short_source_sha_fails(self):
        for value in (None, "abc123", "A" * 40):
            with self.subTest(value=value):
                problems = rp.check_record(ROM, record(source_sha=value), COMMIT)
                self.assertTrue(any("source_sha" in p for p in problems))

    def test_dirty_tree_fails(self):
        problems = rp.check_record(ROM, record(working_tree_dirty=True), COMMIT)
        self.assertTrue(any("dirty" in p for p in problems))

    def test_unknown_dirty_state_fails(self):
        manifest = record()
        del manifest["working_tree_dirty"]
        self.assertTrue(rp.check_record(ROM, manifest, COMMIT))

    def test_source_not_git_repo_fails_via_source_sha(self):
        # A non-git build has no source_sha (null), which the source_sha check rejects.
        problems = rp.check_record(ROM, record(source_sha=None), COMMIT)
        self.assertTrue(any("source_sha" in p for p in problems))

    def test_unpinned_cc65_fails(self):
        problems = rp.check_record(ROM, record(cc65_revision="0" * 40), COMMIT)
        self.assertTrue(any("cc65" in p for p in problems))

    def test_release_claim_fails(self):
        problems = rp.check_record(ROM, record(release_eligible=True), COMMIT)
        self.assertTrue(any("release_eligible" in p for p in problems))

    def test_non_object_manifest_fails(self):
        self.assertEqual(["manifest is not a JSON object"], rp.check_record(ROM, ["x"], COMMIT))

    def test_record_is_not_mutated_by_check(self):
        manifest = record()
        before = copy.deepcopy(manifest)
        rp.check_record(ROM, manifest, COMMIT)
        self.assertEqual(before, manifest)


class ReproducibilityTests(unittest.TestCase):
    def test_identical_builds_pass(self):
        self.assertEqual([], rp.check_reproducible(ROM, bytes(ROM)))

    def test_differing_builds_fail(self):
        self.assertTrue(rp.check_reproducible(ROM, ROM[:-1] + b"\x00"))

    def test_reproduce_runs_two_fresh_builds(self):
        calls = []

        def fake(out):
            calls.append(out.name)
            out.mkdir(parents=True, exist_ok=True)
            (out / "x.pce").write_bytes(ROM)
            return out / "x.pce"

        with tempfile.TemporaryDirectory() as directory:
            first, second = rp.reproduce(fake, Path(directory))
            self.assertEqual(["a", "b"], calls)
            self.assertEqual(ROM, first.read_bytes())
            self.assertEqual(ROM, second.read_bytes())

    def test_copied_marker_in_existing_a_is_refused_and_user_data_survives(self):
        def fake(out):
            raise AssertionError("builder must not run over existing data")

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / rp.OWNER_MARKER).write_text(rp.MARKER_TEXT, encoding="utf-8")  # work is marked
            (work / "a").mkdir()
            keep = work / "a" / "notes.txt"
            keep.write_text("user data", encoding="utf-8")
            (work / "a" / rp.OWNER_MARKER).write_text(rp.MARKER_TEXT, encoding="utf-8")  # copied marker
            with self.assertRaisesRegex(rp.ToolError, "refusing to overwrite existing"):
                rp.reproduce(fake, work)
            self.assertEqual("user data", keep.read_text(encoding="utf-8"))

    def test_reproduce_refuses_nonempty_work_without_marker(self):
        def fake(out):
            raise AssertionError("builder must not run in a foreign work directory")

        with tempfile.TemporaryDirectory() as directory:
            keep = Path(directory) / "notes.txt"
            keep.write_text("user data", encoding="utf-8")
            with self.assertRaises(rp.ToolError):
                rp.reproduce(fake, Path(directory))
            self.assertEqual("user data", keep.read_text(encoding="utf-8"))

    def test_repeat_run_into_same_work_is_refused_and_outputs_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            rp.reproduce(rom_builder, Path(directory))
            kept = Path(directory) / "a" / rp.BUILDERS["smoke"][1]
            with self.assertRaisesRegex(rp.ToolError, "refusing to overwrite existing"):
                rp.reproduce(rom_builder, Path(directory))  # a new --work is required for a repeat run
            self.assertEqual(ROM, kept.read_bytes())

    def test_junction_or_symlink_as_a_is_refused_for_the_link_not_the_contents(self):
        def fake(out):
            raise AssertionError("builder must not write through a junction or symlink")

        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as directory:
            keep = Path(outside) / "keep.txt"
            keep.write_text("outside", encoding="utf-8")
            (Path(outside) / rp.OWNER_MARKER).write_text(rp.MARKER_TEXT, encoding="utf-8")  # copied marker too
            (Path(directory) / rp.OWNER_MARKER).write_text(rp.MARKER_TEXT, encoding="utf-8")
            make_link(Path(directory) / "a", Path(outside))
            # Matches the link message only: without the reparse-point check this reports "refusing to overwrite existing".
            with self.assertRaisesRegex(rp.ToolError, "symlink or junction"):
                rp.reproduce(fake, Path(directory))
            self.assertEqual("outside", keep.read_text(encoding="utf-8"))

    def test_junction_as_work_itself_is_refused_and_target_survives(self):
        def fake(out):
            raise AssertionError("builder must not run in a junction")

        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as parent:
            keep = Path(outside) / "keep.txt"
            keep.write_text("outside", encoding="utf-8")
            work = Path(parent) / "work"
            make_link(work, Path(outside))
            with self.assertRaisesRegex(rp.ToolError, "symlink or junction"):
                rp.reproduce(fake, work)
            self.assertEqual("outside", keep.read_text(encoding="utf-8"))

    def test_reproduce_refuses_symlink_out_of_work(self):
        def fake(out):
            raise AssertionError("builder must not write through a link")

        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as directory:
            keep = Path(outside) / "keep.txt"
            keep.write_text("outside", encoding="utf-8")
            try:
                os.symlink(outside, Path(directory) / "a", target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            with self.assertRaises(rp.ToolError):
                rp.reproduce(fake, Path(directory))
            self.assertEqual("outside", keep.read_text(encoding="utf-8"))

    def test_reproduce_refuses_symlinked_or_broad_work(self):
        def fake(out):
            raise AssertionError("builder must not run for a refused work directory")

        for broad in (rp.ROOT, rp.ROOT.parent):
            with self.subTest(work=broad), self.assertRaises(rp.ToolError):
                rp.reproduce(fake, broad)
        with tempfile.TemporaryDirectory() as target, tempfile.TemporaryDirectory() as parent:
            link = Path(parent) / "work"
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")
            with self.assertRaises(rp.ToolError):
                rp.reproduce(fake, link)


class CliTests(unittest.TestCase):
    def write_pair(self, directory, rom=ROM, manifest=None):
        rom_path = Path(directory) / "x.pce"
        manifest_path = Path(directory) / "manifest.json"
        rom_path.write_bytes(rom)
        manifest_path.write_text(json.dumps(manifest if manifest is not None else record()), encoding="utf-8")
        return rom_path, manifest_path

    def test_rom_pass_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, manifest = self.write_pair(directory)
            self.assertEqual(rp.EXIT_PASS, rp.main(["--rom", str(rom), "--manifest", str(manifest), "--commit", COMMIT]))

    def test_tampered_rom_exit_one(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, manifest = self.write_pair(directory)
            rom.write_bytes(ROM[:-1] + b"\x00")
            self.assertEqual(rp.EXIT_FAIL, rp.main(["--rom", str(rom), "--manifest", str(manifest), "--commit", COMMIT]))

    def test_missing_manifest_is_error_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, _ = self.write_pair(directory)
            missing = Path(directory) / "nope.json"
            self.assertEqual(rp.EXIT_ERROR, rp.main(["--rom", str(rom), "--manifest", str(missing), "--commit", COMMIT]))

    def test_unreadable_manifest_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, manifest = self.write_pair(directory)
            manifest.write_text("{not json", encoding="utf-8")
            self.assertEqual(rp.EXIT_ERROR, rp.main(["--rom", str(rom), "--manifest", str(manifest), "--commit", COMMIT]))

    def test_rom_and_reproduce_together_is_usage_error(self):
        with self.assertRaises(SystemExit):
            rp.main(["--rom", "x.pce", "--manifest", "m.json", "--reproduce", "smoke"])

    def test_short_commit_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, manifest = self.write_pair(directory)
            self.assertEqual(rp.EXIT_ERROR, rp.main(["--rom", str(rom), "--manifest", str(manifest), "--commit", "abc"]))

    def run_reproduce(self, build_once, work):
        with patch.object(rp, "_subprocess_builder", return_value=build_once):
            return rp.main(["--reproduce", "smoke", "--work", str(work), "--commit", COMMIT])

    def fake_builder(self, data_for, manifest=None):
        def build_once(out):
            out.mkdir(parents=True, exist_ok=True)
            rom_name = rp.BUILDERS["smoke"][1]
            (out / rom_name).write_bytes(data_for(out))
            (out / "manifest.json").write_text(json.dumps(manifest or record(rom=data_for(out))), encoding="utf-8")
            return out / rom_name
        return build_once

    def test_reproduce_identical_builds_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(rp.EXIT_PASS, self.run_reproduce(rom_builder, Path(directory)))

    def test_reproduce_divergent_builds_fail_on_reproducibility_alone(self):
        # Each build's manifest matches its own bytes, so only the byte comparison can fail this run.
        def data(out):
            return ROM if out.name == "a" else ROM[:-1] + b"\x01"

        def manifest_for(out):
            return record(rom=data(out))

        def build_once(out):
            out.mkdir(parents=True, exist_ok=True)
            rom_name = rp.BUILDERS["smoke"][1]
            (out / rom_name).write_bytes(data(out))
            (out / "manifest.json").write_text(json.dumps(manifest_for(out)), encoding="utf-8")
            return out / rom_name

        with tempfile.TemporaryDirectory() as directory, patch("sys.stderr") as stderr:
            self.assertEqual(rp.EXIT_FAIL, self.run_reproduce(build_once, Path(directory)))
            text = "".join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn("two builds from the same input differ", text)
            self.assertNotIn("rom_sha256", text)

    def test_reproduce_build_failure_is_error(self):
        def broken(out):
            raise rp.ToolError("build.py failed (exit 1)")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(rp.EXIT_ERROR, self.run_reproduce(broken, Path(directory)))

    def test_reproduce_dirty_manifest_fails_even_when_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            build = self.fake_builder(lambda out: ROM, manifest=record(working_tree_dirty=True))
            self.assertEqual(rp.EXIT_FAIL, self.run_reproduce(build, Path(directory)))


class WiringTests(unittest.TestCase):
    def test_ci_runs_reproducibility_before_handoff(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        reproduce = text.index("rom_provenance.py --reproduce smoke")
        compare = text.index("cmp build/debug/smoke-test-not-bios.pce build/rom-provenance/a/smoke-test-not-bios.pce")
        record = text.index("rom_provenance.py --rom build/debug/smoke-test-not-bios.pce")
        handoff = text.index("Hand off the test fixture")
        self.assertLess(reproduce, compare)
        self.assertLess(compare, record)
        self.assertLess(record, handoff)
        self.assertNotIn("continue-on-error", text[reproduce:handoff])


if __name__ == "__main__":
    unittest.main()
