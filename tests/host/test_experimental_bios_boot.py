"""Static contracts for the opt-in experimental System-Card-sized DEV image; no cc65 or ROM build."""
import unittest
from pathlib import Path

from tools.build_experimental_bios_boot import EXPERIMENTAL_BOOT_FLAG, ROM_NAME, experimental_boot_units
from tools.experimental_track_selection import (KIND_AUDIO_OR_INVALID, KIND_MODE1_INDEX01, assembler_defines,
                                                selection)

ROOT = Path(__file__).resolve().parents[2]


class ExperimentalBootSelectionTests(unittest.TestCase):
    def test_default_boot_source_is_not_changed(self):
        boot = (ROOT / "src/boot.s").read_text(encoding="utf-8")
        self.assertIn("reset_not_implemented:", boot)
        self.assertNotIn("experimental_bios", boot)

    def test_opt_in_units_and_vector_define_are_explicit(self):
        units = {path.name: defines for path, defines in experimental_boot_units()}
        self.assertEqual(set(units), {"experimental_bios_boot.s", "cd_load_exec_probe.s"})
        self.assertEqual(units["cd_load_exec_probe.s"], ["-D", EXPERIMENTAL_BOOT_FLAG])

    def test_new_reset_source_has_the_fail_closed_synthetic_flow(self):
        source = (ROOT / "src/experimental_bios_boot.s").read_text(encoding="utf-8")
        for line in ("experimental_bios_reset:", "experimental_bios_init:", "cld", "tam #$01", "tam #$02",
                      "txs", "experimental_selection:", "cpx #1", "jsr cd_set_read_lba", "jmp cd_entry",
                      "experimental_bios_selection_halt:"):
            self.assertIn(line, source)
        self.assertIn("experimental_selection_prefix", source)
        self.assertIn("cpx #6", source)
        self.assertIn("NOT a compatible System Card BIOS", source)
        self.assertIn("NO ABI claim", source)

    def test_image_contract_is_explicitly_256k_and_nonrelease(self):
        builder = (ROOT / "tools/build_experimental_bios_boot.py").read_text(encoding="utf-8")
        self.assertEqual(ROM_NAME, "experimental-dev-only-not-compatible-syscard-sized.pce")
        self.assertIn('"rom_bytes": IMAGE_BYTES', builder)
        self.assertIn('"system_card_abi": "NO_CLAIM"', builder)
        self.assertIn('"release_eligible": False', builder)
        self.assertIn('"private_media_used": False', builder)

    def test_selection_record_is_explicit_and_supports_non_lba5(self):
        record = selection(2, 241, KIND_MODE1_INDEX01)
        self.assertEqual(record["lba"], 241)
        self.assertIn("EXP_SELECTED_LBA_LO=$F1", assembler_defines(record))
        self.assertEqual(selection(2, 0, KIND_AUDIO_OR_INVALID)["kind"], KIND_AUDIO_OR_INVALID)

    def test_make_target_is_opt_in_and_has_real_core_target(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("experimental-bios-boot: dev-rom", makefile)
        self.assertIn("experimental-bios-boot-integration: experimental-bios-boot", makefile)
        self.assertIn("geargrafx_experimental_track_contract.py", makefile)


if __name__ == "__main__":
    unittest.main()
