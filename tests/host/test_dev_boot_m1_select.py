"""Host checks for the Issue #551 M1 opt-in DEV boot selection (wave26 w02). No cc65, no ROM build.

The default DEV ROM (tools/build_rom.py, src/boot.s) keeps the stop loop. Only the explicit
tools/build_dev_boot_internal.py build passes DEV_BOOT_FLAG, which selects dev_boot_reset -> dev_boot_entry
(init, cd_media_state, media gate) and records the media result at MS_RES.
"""
import unittest
from pathlib import Path

from tools.build_dev_boot_internal import DEV_BOOT_FLAG, MS_RES, dev_boot_units

ROOT = Path(__file__).resolve().parents[2]


class DefaultBuildUnchangedTests(unittest.TestCase):
    def test_default_reset_bytes_are_still_the_stop_loop(self):
        text = (ROOT / "src/boot.s").read_text(encoding="utf-8")
        self.assertIn("reset_not_implemented:\n    sei\n    ldx #$FF", text)
        self.assertIn("bra @loop", text)
        self.assertNotIn("dev_boot", text)

    def test_default_rom_build_never_passes_the_flag(self):
        for path in ("tools/build_rom.py", "src/boot.s", "src/bank0.cfg"):
            self.assertNotIn(DEV_BOOT_FLAG, (ROOT / path).read_text(encoding="utf-8"), path)

    def test_dev_rom_target_does_not_select_the_internal_build(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        dev_rom = makefile.split("dev-rom:", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("build_dev_boot_internal", dev_rom)


class OptInSelectionTests(unittest.TestCase):
    def setUp(self):
        self.units = {src.name: defines for src, defines in dev_boot_units()}

    def test_flag_is_the_existing_reset_switch_define(self):
        self.assertEqual(DEV_BOOT_FLAG, "DEV_BOOT_INTERNAL")
        text = (ROOT / "src/cd_load_exec_probe.s").read_text(encoding="utf-8")
        self.assertIn(".ifdef DEV_BOOT_INTERNAL", text)
        self.assertIn(".word dev_boot_reset", text)

    def test_opt_in_units_are_the_init_path(self):
        self.assertEqual(set(self.units), {"dev_boot_internal.s", "cd_load_exec_probe.s", "cd_media_state.s"})

    def test_flag_is_passed_only_to_the_reset_switch_unit(self):
        self.assertEqual(self.units["cd_load_exec_probe.s"], ["-D", DEV_BOOT_FLAG])
        self.assertEqual(self.units["dev_boot_internal.s"], [])

    def test_media_state_result_is_recorded_at_agreed_address(self):
        self.assertEqual(MS_RES, 0x3F40)
        self.assertEqual(self.units["cd_media_state.s"], ["-D", "MS_RES=$3F40"])


if __name__ == "__main__":
    unittest.main()
