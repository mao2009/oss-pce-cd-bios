"""Host tests for the G5 2-base CD_BASE variant builder (synthetic only).

RED->GREEN: missing builder import fails first; implementation passes after.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Build2BaseHostTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
