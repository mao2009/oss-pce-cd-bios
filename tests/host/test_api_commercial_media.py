"""Host tests for ./rom/ commercial-media selection (Issue #635, no real media).

Uses temporary directories and synthetic CUE/BIN names only. No commercial
bytes, names, paths or hashes are committed; secret sentinel names below exist
solely to prove sanitized errors never leak them.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
import commercial_media  # noqa: E402
from commercial_media import (detect_cue_files, parse_cue_file_refs, resolve_rom_dir,  # noqa: E402
                              select_cue, validate_cue)
import geargrafx_api_entry as api_entry  # noqa: E402


CUE_SECRET = "SECRET_CUE_7X9.cue"
BIN_SECRET = "SECRET_TRACK_8Y3.bin"
VALID_CUE = 'FILE "track.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'


def make_rom(tmp: Path, cues: dict[str, str] | None = None, bins: dict[str, bytes] | None = None) -> Path:
    rom = tmp / "rom"
    rom.mkdir()
    for name, data in (bins or {}).items():
        path = rom / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, text in (cues or {}).items():
        path = rom / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return rom


class DetectTests(unittest.TestCase):
    def test_missing_dir_yields_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_cue_files(Path(tmp) / "rom"), [])

    def test_single_cue_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            found = detect_cue_files(rom)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].name, "a.cue")

    def test_multiple_sorted_recursive(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"b.cue": VALID_CUE, "a/x.cue": VALID_CUE},
                           {"track.bin": b"x", "a/track.bin": b"y"})
            found = detect_cue_files(rom)
            self.assertEqual([p.relative_to(rom).as_posix() for p in found], ["a/x.cue", "b.cue"])

    def test_only_cue_suffix_considered(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x", "bios.pce": b"\x00" * 10})
            found = detect_cue_files(rom)
            self.assertEqual([p.name for p in found], ["a.cue"])

    def test_cue_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"A.CUE": VALID_CUE}, {"track.bin": b"x"})
            self.assertEqual(len(detect_cue_files(rom)), 1)

    def test_symlinked_cue_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"real.cue": VALID_CUE}, {"track.bin": b"x"})
            os.symlink(str(rom / "real.cue"), str(rom / "link.cue"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                detect_cue_files(rom)

    def test_symlinked_dir_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "b.cue").write_text(VALID_CUE, encoding="utf-8")
            os.symlink(str(outside), str(rom / "linkdir"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                detect_cue_files(rom)

    def test_rom_dir_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            (real / "a.cue").write_text(VALID_CUE, encoding="utf-8")
            (real / "track.bin").write_bytes(b"x")
            link = Path(tmp) / "rom"
            os.symlink(str(real), str(link))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                resolve_rom_dir(Path(tmp)) if False else detect_cue_files(link)


class SelectTests(unittest.TestCase):
    def test_zero_is_not_run(self):
        with self.assertRaisesRegex(ValueError, "NOT_RUN"):
            select_cue([], None)

    def test_single_no_index_needed(self):
        p = Path("/fake/a.cue")
        self.assertEqual(select_cue([p], None), p)

    def test_single_with_index_zero(self):
        p = Path("/fake/a.cue")
        self.assertEqual(select_cue([p], 0), p)

    def test_multiple_needs_index_not_first(self):
        cands = [Path("/fake/a.cue"), Path("/fake/b.cue")]
        with self.assertRaisesRegex(ValueError, "BLOCKED: multiple"):
            select_cue(cands, None)

    def test_multiple_with_index(self):
        cands = [Path("/fake/a.cue"), Path("/fake/b.cue")]
        self.assertEqual(select_cue(cands, 1), cands[1])

    def test_index_out_of_range_and_negative(self):
        cands = [Path("/fake/a.cue"), Path("/fake/b.cue")]
        for bad in (-1, 2, 99):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "BLOCKED"):
                select_cue(cands, bad)


class ValidateTests(unittest.TestCase):
    def test_valid_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            out = validate_cue(rom / "a.cue", rom)
            self.assertEqual(len(out), 1)

    def test_valid_quoted_spaces_and_dot_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = 'FILE "./my track.bin" BINARY\n  TRACK 01 MODE1/2352\n'
            rom = make_rom(Path(tmp), {"a.cue": cue}, {"my track.bin": b"x"})
            self.assertEqual(len(validate_cue(rom / "a.cue", rom)), 1)

    def test_valid_subdir_ref_stays_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "sub/track.bin" BINARY\n  TRACK 01 MODE1/2352\n'},
                           {"sub/track.bin": b"x"})
            self.assertEqual(len(validate_cue(rom / "a.cue", rom)), 1)

    def test_cue_outside_rom_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            outside = Path(tmp) / "evil.cue"
            outside.write_text(VALID_CUE, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(outside, rom)

    def test_absolute_file_ref_rejected(self):
        for ref in ('FILE "/etc/passwd" BINARY\n', 'FILE "C:\\\\evil.bin" BINARY\n'):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                rom = make_rom(Path(tmp), {"a.cue": ref}, {"track.bin": b"x"})
                with self.assertRaisesRegex(ValueError, "BLOCKED"):
                    validate_cue(rom / "a.cue", rom)

    def test_traversal_ref_rejected(self):
        for ref in ('FILE "../evil.bin" BINARY\n', 'FILE "a/../../evil.bin" BINARY\n',
                    'FILE "a/../b.bin" BINARY\n'):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                rom = make_rom(Path(tmp), {"a.cue": ref}, {"track.bin": b"x", "b.bin": b"y"})
                with self.assertRaisesRegex(ValueError, "BLOCKED"):
                    validate_cue(rom / "a.cue", rom)

    def test_backslash_ref_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "track\\\\bin" BINARY\n'}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_missing_bin_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "missing.bin" BINARY\n'}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_bin_is_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "subdir" BINARY\n'}, {"subdir/track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_bin_symlink_inside_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "link.bin" BINARY\n'}, {"real.bin": b"x"})
            os.symlink(str(rom / "real.bin"), str(rom / "link.bin"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_bin_symlink_outside_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "link.bin" BINARY\n'}, {})
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"x")
            os.symlink(str(outside), str(rom / "link.bin"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_cue_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"real.cue": VALID_CUE}, {"track.bin": b"x"})
            os.symlink(str(rom / "real.cue"), str(rom / "link.cue"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "link.cue", rom)

    def test_no_file_ref_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": "REM no files\n  TRACK 01 MODE1/2352\n"}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_non_utf8_cue_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom"
            rom.mkdir()
            (rom / "a.cue").write_bytes(b"\xff\xfe FILE \"track.bin\" BINARY\n")
            (rom / "track.bin").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_validation_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"payload"})
            before = {p: (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
                      for p in (rom / "a.cue", rom / "track.bin")}
            validate_cue(rom / "a.cue", rom)
            detect_cue_files(rom)
            after = {p: (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
                     for p in (rom / "a.cue", rom / "track.bin")}
            self.assertEqual(before, after)
            self.assertEqual(sorted(p.name for p in rom.iterdir()), ["a.cue", "track.bin"])


class ParseTests(unittest.TestCase):
    def test_parse_quoted_and_unquoted(self):
        refs = parse_cue_file_refs('FILE "a b.bin" BINARY\nFILE plain.bin BINARY\nREM FILE "ignored.bin" BINARY\n')
        self.assertEqual(refs, ["a b.bin", "plain.bin"])

    def test_parse_case_insensitive(self):
        self.assertEqual(parse_cue_file_refs('file "x.bin" binary\n'), ["x.bin"])


class DisclosureTests(unittest.TestCase):
    """Secret sentinels must never appear in any error message."""

    def _check_secret_free(self, func, *args):
        try:
            func(*args)
        except ValueError as error:
            msg = str(error)
            self.assertNotIn(CUE_SECRET, msg)
            self.assertNotIn(BIN_SECRET, msg)
            self.assertNotIn("SECRET", msg)
            return
        self.fail("expected ValueError")

    def test_secret_names_never_leak_on_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_cue = f'FILE "{BIN_SECRET}" BINARY\n'
            rom = make_rom(Path(tmp), {CUE_SECRET: secret_cue}, {})
            # Missing BIN -> BLOCKED must not name either secret.
            self._check_secret_free(validate_cue, rom / CUE_SECRET, rom)

    def test_multiple_selection_error_hides_names(self):
        cands = [Path(CUE_SECRET), Path(BIN_SECRET)]
        try:
            select_cue(cands, None)
        except ValueError as error:
            self.assertNotIn(CUE_SECRET, str(error))
            self.assertNotIn(BIN_SECRET, str(error))
            # Count is an identifier-adjacent detail and must not be exposed.
            self.assertNotIn("(2", str(error))
            self.assertNotIn("2 found", str(error))
        else:
            self.fail("expected error")

    def test_resolve_errors_hide_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {CUE_SECRET: VALID_CUE}, {"track.bin": b"x"})
            # Explicit override that does not match any candidate.
            try:
                api_entry.resolve_commercial_cue(rom, Path(tmp) / "other.cue", None)
            except ValueError as error:
                self.assertNotIn(CUE_SECRET, str(error))
                self.assertNotIn(str(tmp), str(error))
            else:
                self.fail("expected error")


class ResolveTests(unittest.TestCase):
    def test_resolve_zero_is_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {}, {})
            with self.assertRaisesRegex(ValueError, "NOT_RUN"):
                api_entry.resolve_commercial_cue(rom, None, None)

    def test_resolve_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            self.assertEqual(api_entry.resolve_commercial_cue(rom, None, None), rom / "a.cue")

    def test_resolve_multiple_requires_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE, "b.cue": VALID_CUE},
                           {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED: multiple"):
                api_entry.resolve_commercial_cue(rom, None, None)
            self.assertEqual(api_entry.resolve_commercial_cue(rom, None, 1).name, "b.cue")

    def test_explicit_override_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE, "b.cue": VALID_CUE},
                           {"track.bin": b"x"})
            # Relative override matching candidate 0.
            self.assertEqual(api_entry.resolve_commercial_cue(rom, Path("a.cue"), 0), rom / "a.cue")
            # Mismatch index.
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                api_entry.resolve_commercial_cue(rom, Path("a.cue"), 1)
            # Unknown name.
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                api_entry.resolve_commercial_cue(rom, Path("unknown.cue"), None)

    def test_auto_labels_matches_loader_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            variant = "main"
            rom_name = f"dev-ipl-loader-bios-not-compatible-{variant}.pce"
            (out / rom_name).write_bytes(b"x" * 16)
            lbl = out / f"ipl-loader-bios-{variant}.lbl"
            lbl.write_text(
                "al 00E24F .api_unimplemented\n"
                "al 00F42C .cd_halt_fail\n"
                "al 00F488 .cd_unexpected_interrupt\n"
                "al 00F000 .reset\n", encoding="ascii")
            labels = api_entry.auto_labels_for_rom(out / rom_name, out)
            body = labels.read_text(encoding="ascii")
            self.assertIn("cd_halt_ok f000", body)
            self.assertIn("cd_halt_fail f42c", body)
            self.assertIn("api_unimplemented e24f", body)

    def test_auto_labels_rejects_non_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "auto labels"):
                api_entry.auto_labels_for_rom(Path(tmp) / "other.pce", Path(tmp))

    def test_auto_labels_missing_reset_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            variant = "main"
            rom_name = f"dev-ipl-loader-bios-not-compatible-{variant}.pce"
            (out / rom_name).write_bytes(b"x" * 16)
            lbl = out / f"ipl-loader-bios-{variant}.lbl"
            lbl.write_text(
                "al 00E24F .api_unimplemented\n"
                "al 00F42C .cd_halt_fail\n"
                "al 00F488 .cd_unexpected_interrupt\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "auto labels"):
                api_entry.auto_labels_for_rom(out / rom_name, out)

    def test_auto_labels_collision_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            variant = "main"
            rom_name = f"dev-ipl-loader-bios-not-compatible-{variant}.pce"
            (out / rom_name).write_bytes(b"x" * 16)
            lbl = out / f"ipl-loader-bios-{variant}.lbl"
            # reset collides with cd_halt_fail.
            lbl.write_text(
                "al 00E24F .api_unimplemented\n"
                "al 00F42C .cd_halt_fail\n"
                "al 00F488 .cd_unexpected_interrupt\n"
                "al 00F42C .reset\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "auto labels"):
                api_entry.auto_labels_for_rom(out / rom_name, out)

    def test_snapshot_detects_media_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            snap = api_entry.snapshot_media(rom / "a.cue", rom)
            api_entry.verify_media_unchanged(snap)  # unchanged: no error
            # Mutate BIN size.
            (rom / "track.bin").write_bytes(b"changed-bytes")
            with self.assertRaisesRegex(ValueError, "changed during probe"):
                api_entry.verify_media_unchanged(snap)

    def test_override_dotdot_spelling_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                api_entry.resolve_commercial_cue(rom, Path("../rom/a.cue"), None)


class BoundaryTests(unittest.TestCase):
    def test_resolve_rom_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            (real / "a.cue").write_text(VALID_CUE, encoding="utf-8")
            (real / "track.bin").write_bytes(b"x")
            root = Path(tmp) / "root"
            root.mkdir()
            os.symlink(str(real), str(root / "rom"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                resolve_rom_dir(root)

    def test_cue_too_many_refs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = "".join(f'FILE "t{i}.bin" BINARY\n' for i in range(101))
            rom = make_rom(Path(tmp), {"a.cue": cue}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_fifo_cue_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom"
            rom.mkdir()
            fifo = rom / "a.cue"
            os.mkfifo(str(fifo))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                detect_cue_files(rom)

    def test_single_with_bad_index_rejected(self):
        p = Path("/fake/a.cue")
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            select_cue([p], 1)

    def test_parser_differential_extra_token_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "track.bin" BINARY EXTRA\n'}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_parser_differential_missing_type_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "track.bin"\n'}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_cue_too_large_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": "REM " + "x" * ((1 << 20) + 1) + "\n" + VALID_CUE}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_too_many_candidates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {f"c{i:03d}.cue": VALID_CUE for i in range(101)}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                detect_cue_files(rom)

    def test_too_deep_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = "d/" * 11 + "a.cue"
            rom = make_rom(Path(tmp), {deep: VALID_CUE}, {"track.bin": b"x"})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                detect_cue_files(rom)

    def test_snapshot_ctime_change_detected(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            snap = api_entry.snapshot_media(rom / "a.cue", rom)
            time.sleep(0.01)
            os.chmod(rom / "track.bin", 0o644)  # ctime changes, size/mtime may not
            with self.assertRaisesRegex(ValueError, "changed during probe"):
                api_entry.verify_media_unchanged(snap)

    def test_walk_file_limit_blocked(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            # Lower the walk limit to prove the boundary without creating 10k files.
            with mock.patch.object(commercial_media, "_MAX_WALK_FILES", 1):
                with self.assertRaisesRegex(ValueError, "BLOCKED"):
                    detect_cue_files(rom)

    def test_depth_boundary_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = "d/" * 9 + "a.cue"  # depth computation: rel parts <= 10 -> allowed
            rom = make_rom(Path(tmp), {deep: VALID_CUE}, {"track.bin": b"x"})
            found = detect_cue_files(rom)
            self.assertEqual(len(found), 1)

    def test_snapshot_deleted_file_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            snap = api_entry.snapshot_media(rom / "a.cue", rom)
            (rom / "track.bin").unlink()
            try:
                api_entry.verify_media_unchanged(snap)
            except ValueError as error:
                self.assertNotIn("track.bin", str(error))
                self.assertIn("FAIL", str(error))
            else:
                self.fail("expected FAIL on deleted media")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read chmod 000")
    def test_unreadable_subdir_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": VALID_CUE}, {"track.bin": b"x"})
            sub = rom / "sub"
            sub.mkdir()
            os.chmod(sub, 0o000)
            try:
                with self.assertRaisesRegex(ValueError, "BLOCKED"):
                    detect_cue_files(rom)
            finally:
                os.chmod(sub, 0o700)

    def test_git_failure_falls_back_to_not_run(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            with mock.patch("subprocess.run", side_effect=OSError("no git")):
                # resolve_rom_dir must not crash; it returns primary (missing),
                # and detection then yields no candidates (NOT_RUN by caller).
                rom_dir = resolve_rom_dir(root)
                self.assertEqual(detect_cue_files(rom_dir), [])


class CommercialGateTests(unittest.TestCase):
    """--commercial enforcement and log hygiene (source-level, no Geargrafx)."""

    def test_commercial_flag_required(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        self.assertIn('"--commercial"', src)
        self.assertIn("commercial options require --commercial", src)
        self.assertIn('resolve_commercial_cue', src)
        self.assertIn('resolve_rom_dir', src)
        self.assertIn('OSS_PCE_ALLOW_ROM_DIR', src)

    def test_harness_never_logs_media_paths(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        for forbidden in ("str(args.commercial_rom", "str(args.commercial_cue", "str(args.commercial_output"):
            self.assertNotIn(forbidden, src)
        # resolve helper must not print the cue path either.
        self.assertNotIn("print(cue_path", src)
        self.assertNotIn("print(rom_dir", src)


class MutationTests(unittest.TestCase):
    """Mutants that must be rejected (behavioral, not syntax)."""

    def test_mutant_auto_pick_first_is_rejected(self):
        cands = [Path("/fake/a.cue"), Path("/fake/b.cue")]
        try:
            result = select_cue(cands, None)
        except ValueError:
            return
        self.fail(f"mutant auto-picked first candidate: {result}")

    def test_mutant_pce_as_media_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {}, {"bios.pce": b"\x00" * 10})
            self.assertEqual(detect_cue_files(rom), [])
            with self.assertRaisesRegex(ValueError, "NOT_RUN"):
                select_cue(detect_cue_files(rom), None)

    def test_mutant_symlink_outside_allowed_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = make_rom(Path(tmp), {"a.cue": 'FILE "link.bin" BINARY\n'}, {})
            outside = Path(tmp) / "evil.bin"
            outside.write_bytes(b"x")
            os.symlink(str(outside), str(rom / "link.bin"))
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                validate_cue(rom / "a.cue", rom)

    def test_mutant_no_commercial_flag_allowed_is_rejected(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        # If the gate were removed, the FAIL message would be gone.
        self.assertIn("commercial options require --commercial", src)


class DataTrackIndex01LbaTests(unittest.TestCase):
    """Stat-only INDEX 01 LBA derivation (synthetic CUE/BIN sizes only, no media bytes)."""

    def _rom_with_sizes(self, tmp, cue_text, sizes):
        rom = Path(tmp) / "rom"
        rom.mkdir()
        for name, frames in sizes.items():
            (rom / name).write_bytes(b"\x00" * (frames * 2352))
        (rom / "a.cue").write_text(cue_text, encoding="utf-8")
        return rom

    def test_single_data_track_at_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, VALID_CUE, {"track.bin": 10})
            self.assertEqual(commercial_media.data_track_index01_lba(rom / "a.cue", rom), 0)

    def test_multi_file_audio_then_data_with_stored_pregap(self):
        # Track 01 audio 100 frames, Track 02 data INDEX 00 at 0, INDEX 01 at 25 frames:
        # start_lba = 100 + 25 = 125. Sizes only; BIN contents are zeros.
        cue = ('FILE "a1.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
               'FILE "a2.bin" BINARY\n  TRACK 02 MODE1/2352\n    INDEX 00 00:00:00\n    INDEX 01 00:00:25\n')
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, cue, {"a1.bin": 100, "a2.bin": 50})
            self.assertEqual(commercial_media.data_track_index01_lba(rom / "a.cue", rom), 125)

    def test_virtual_pregap_before_data(self):
        # Audio 100 frames, then data track with CUE PREGAP 50 frames and INDEX 01 at frame 0
        # of its BIN: start_lba = 100 + 50 = 150.
        cue = ('FILE "a1.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
               'FILE "a2.bin" BINARY\n  TRACK 02 MODE1/2352\n    PREGAP 00:00:50\n    INDEX 01 00:00:00\n')
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, cue, {"a1.bin": 100, "a2.bin": 50})
            self.assertEqual(commercial_media.data_track_index01_lba(rom / "a.cue", rom), 150)

    def test_bom_cue_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, "\ufeff" + VALID_CUE, {"track.bin": 10})
            self.assertEqual(commercial_media.data_track_index01_lba(rom / "a.cue", rom), 0)

    def test_multiple_mode1_tracks_blocked(self):
        cue = ('FILE "a1.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
               'FILE "a2.bin" BINARY\n  TRACK 02 MODE1/2352\n    INDEX 01 00:00:00\n')
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, cue, {"a1.bin": 10, "a2.bin": 10})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                commercial_media.data_track_index01_lba(rom / "a.cue", rom)

    def test_audio_only_blocked(self):
        cue = 'FILE "a1.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, cue, {"a1.bin": 10})
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                commercial_media.data_track_index01_lba(rom / "a.cue", rom)

    def test_bin_size_not_multiple_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rom"
            rom.mkdir()
            (rom / "track.bin").write_bytes(b"\x00" * 2353)
            (rom / "a.cue").write_text(VALID_CUE, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "BLOCKED"):
                commercial_media.data_track_index01_lba(rom / "a.cue", rom)

    def test_error_messages_are_sanitized(self):
        # No file names, paths or LBA values in error text.
        cue = 'FILE "SECRET_TRACK_8Y3.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_with_sizes(tmp, cue, {"SECRET_TRACK_8Y3.bin": 10})
            try:
                commercial_media.data_track_index01_lba(rom / "a.cue", rom)
            except ValueError as error:
                self.assertNotIn("SECRET_TRACK_8Y3", str(error))
                self.assertNotIn(str(rom), str(error))
            else:
                self.fail("expected BLOCKED")


class LoaderManifestPrivacyTests(unittest.TestCase):
    """Commercial auto-loader manifests must not leak the derived SEL."""

    def _manifest(self, private):
        from build_dev_ipl_loader_bios import loader_manifest
        return loader_manifest(12345, "debug", "comm-public", ["-D", "IPL_PUBLIC"],
                               b"\x00" * 262144, {}, "x.pce", private_input=private)

    def test_synthetic_manifest_keeps_exact_sel(self):
        manifest = self._manifest(False)
        self.assertEqual(manifest["ipl_sel_lba"], 12345)
        self.assertFalse(manifest["private_media_used"])
        self.assertNotIn("ipl_sel_lba_redacted", manifest)

    def test_private_manifest_redacts_sel_and_marks_private(self):
        manifest = self._manifest(True)
        self.assertNotIn("ipl_sel_lba", manifest)
        self.assertTrue(manifest["ipl_sel_lba_redacted"])
        self.assertTrue(manifest["private_media_used"])
        # The decimal/hex SEL value must not appear anywhere in the manifest.
        import json
        text = json.dumps(manifest, sort_keys=True)
        self.assertNotIn("12345", text)
        self.assertNotIn("3039", text.lower())  # 0x3039 hex spelling

    def test_auto_loader_uses_private_mode_synthetic_does_not(self):
        src = (ROOT / "tests" / "integration" / "geargrafx_api_entry.py").read_text()
        self.assertIn("private_input=True", src)
        # The two synthetic build sites must not pass private_input=True.
        self.assertEqual(src.count("private_input=True"), 1)

    def test_build_output_is_gitignored(self):
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn("build/", gitignore)
        self.assertIn("/rom/", gitignore)


if __name__ == "__main__":
    unittest.main()
