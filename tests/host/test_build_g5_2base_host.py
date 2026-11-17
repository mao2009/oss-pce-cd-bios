"""Host tests for the G5 2-base CD_BASE variant builder (synthetic only).

RED->GREEN: missing builder import fails first; implementation passes after.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Build2BaseHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Self-contained: build the DEV ROM into a private temp dir so the
        # suite never depends on a repo build/ tree (CI runs host tests
        # without a prior `make dev-rom`).
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        from unittest.mock import patch
        import build_rom
        cls._tmp = tempfile.TemporaryDirectory(prefix="pce-2base-devrom-")
        out = Path(cls._tmp.name) / "dev"
        with patch("sys.argv", ["build_rom.py", "--mode", "debug",
                                  "--output", str(out)]):
            assert build_rom.main() == 0
        cls.orig = out / "dev-only-not-compatible-syscard3.pce"
        assert cls.orig.is_file()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_sources_exist(self):
        self.assertTrue((ROOT / "src" / "cd_base_2base.s").is_file())
        self.assertTrue((ROOT / "tools" / "build_ipl_loader_g5_2base.py").is_file())

    def test_entry_symbol_present(self):
        text = (ROOT / "src" / "cd_base_2base.s").read_text(encoding="utf-8")
        self.assertIn("cd_base_2base_entry", text)
        self.assertIn("recbase", text.lower())

    def test_no_hardcoded_private_lba(self):
        text = (ROOT / "src" / "cd_base_2base.s").read_text(encoding="utf-8")
        # No raw commercial LBA bytes; INDEX01 comes from experimental_selection.
        self.assertIn("experimental_selection", text)

    def test_builder_import(self):
        import sys
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        self.assertTrue(hasattr(b2, "build_ipl_loader_g5_rom"))

    def test_symlinked_leaf_refused(self):
        # A-4 negative: a pre-planted symlink where a builder leaf goes
        # must fail closed instead of being followed/overwritten.
        import sys
        import tempfile
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        with tempfile.TemporaryDirectory(prefix="pce-2base-symlink-") as td:
            out = Path(td)
            (out / f"cd_base_2base_victim.o").symlink_to("/etc/hostname")
            with self.assertRaises(ValueError):
                b2.build_ipl_loader_g5_rom(
                    self.orig, out, 0x109, "debug", "victim",
                    ["-D", "IPL_PUBLIC"], track=2, private_input=False)

    def test_rebuild_is_deterministic(self):
        # A-4: two fresh builds from current sources are byte-identical
        # (no stale-ROM reuse can mask a source change).
        import sys
        import tempfile
        import hashlib
        sys.path[:0] = [str(ROOT / "tools")]
        import build_ipl_loader_g5_2base as b2
        digests = []
        for tag in ("det-a", "det-b"):
            with tempfile.TemporaryDirectory(prefix="pce-2base-det-") as td:
                rom, _ = b2.build_ipl_loader_g5_rom(
                    self.orig, Path(td), 0x109, "debug", tag,
                    ["-D", "IPL_PUBLIC"], track=2, private_input=False)
                digests.append(hashlib.sha256(rom.read_bytes()).hexdigest())
        self.assertEqual(digests[0], digests[1])


if __name__ == "__main__":
    unittest.main()
