"""Static contracts for the opt-in experimental System-Card-sized DEV image; no cc65 or ROM build."""
import unittest
from pathlib import Path

from tools.build_experimental_bios_boot import EXPERIMENTAL_BOOT_FLAG, ROM_NAME, experimental_boot_units
from tools.experimental_track_selection import (SelectionError, assembler_defines, media_identity,
                                                selection_from_cue, verify_media_identity)
from tools.synthetic_boot_media import CUE_NAME, write_case
import tempfile

ROOT = Path(__file__).resolve().parents[2]


class ExperimentalBootSelectionTests(unittest.TestCase):
    def test_default_boot_source_is_not_changed(self):
        boot = (ROOT / "src/boot.s").read_text(encoding="utf-8")
        self.assertIn("reset_not_implemented:", boot)
        self.assertNotIn("experimental_bios", boot)

    def test_opt_in_units_and_vector_define_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NONE", "V1")
            units = {path.name: defines for path, defines in experimental_boot_units(root / CUE_NAME)}
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
        self.assertIn('"XSEL", 1, 1', source)  # guest checks magic, version, and format
        self.assertIn("cpx #1", source)         # kind
        self.assertIn("cpx #100", source)       # track 1..99
        self.assertIn("and #$E0", source)        # 21-bit READ(6) LBA
        self.assertNotIn("EXP_SELECTED_LBA_LO = 5", source)
        self.assertIn("NOT a compatible System Card BIOS", source)
        self.assertIn("NO ABI claim", source)

    def test_image_contract_is_explicitly_256k_and_nonrelease(self):
        builder = (ROOT / "tools/build_experimental_bios_boot.py").read_text(encoding="utf-8")
        self.assertEqual(ROM_NAME, "experimental-dev-only-not-compatible-syscard-sized.pce")
        self.assertIn('"rom_bytes": IMAGE_BYTES', builder)
        self.assertIn('"system_card_abi": "NO_CLAIM"', builder)
        self.assertIn('"release_eligible": False', builder)
        self.assertIn('"private_media_used": False', builder)

    def test_selection_record_is_cue_derived_and_supports_non_lba5(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NONE", "V2")
            record = selection_from_cue(root / CUE_NAME, root / CUE_NAME)
        self.assertEqual(record["lba"], 241)
        self.assertIn("EXP_SELECTED_LBA_LO=$F1", assembler_defines(record))
        self.assertEqual(record["kind"], 1)

    def test_absent_arbitrary_audio_and_mount_mismatch_records_are_refused(self):
        with self.assertRaises(ValueError):
            experimental_boot_units(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NO_TRACK", "V1")
            with self.assertRaises(SelectionError):
                selection_from_cue(root / CUE_NAME, root / CUE_NAME)
            write_case(root, "NONE", "V1")
            other = root / "other.cue"
            other.write_text((root / CUE_NAME).read_text(encoding="ascii"), encoding="ascii")
            with self.assertRaisesRegex(SelectionError, "mounted CUE differ"):
                selection_from_cue(root / CUE_NAME, other)
        with self.assertRaises((SelectionError, TypeError)):
            assembler_defines({"track": 2, "lba": 241, "kind": 1})

    def test_media_identity_accepts_final_payload_and_rejects_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NONE", "V2")
            cue = root / CUE_NAME
            record = selection_from_cue(cue, cue)
            verify_media_identity(record["media_identity"], cue)
            self.assertEqual(record["media_identity"], media_identity(cue))
            # Same pathname, changed BIN bytes: reject before any core launch.
            binary = root / "synthetic-boot.bin"
            data = bytearray(binary.read_bytes()); data[-1] ^= 1; binary.write_bytes(data)
            with self.assertRaisesRegex(SelectionError, "content differs"):
                verify_media_identity(record["media_identity"], cue)

    def test_media_identity_rejects_cue_size_and_reference_substitution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NONE", "V1")
            cue = root / CUE_NAME
            identity = media_identity(cue)
            cue.write_text(cue.read_text(encoding="ascii") + "REM changed after selection\n", encoding="ascii")
            with self.assertRaises(SelectionError):
                verify_media_identity(identity, cue)

    def test_media_identity_rejects_bin_size_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root, "NONE", "V1")
            cue = root / CUE_NAME
            identity = media_identity(cue)
            with (root / "synthetic-boot.bin").open("ab") as binary:
                binary.write(b"x")
            with self.assertRaises(SelectionError):
                verify_media_identity(identity, cue)
            # A distinct but byte-identical target still has a different ordered
            # relative FILE reference and must not be silently mounted.
            write_case(root, "NONE", "V1")
            identity = media_identity(cue)
            original = root / "synthetic-boot.bin"
            alternate = root / "replacement.bin"; alternate.write_bytes(original.read_bytes())
            cue.write_text(cue.read_text(encoding="ascii").replace("synthetic-boot.bin", "replacement.bin"), encoding="ascii")
            with self.assertRaises(SelectionError):
                verify_media_identity(identity, cue)

    def test_make_target_is_opt_in_and_has_real_core_target(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("experimental-bios-boot: dev-rom", makefile)
        self.assertIn("EXPERIMENTAL_CUE must name the exact synthetic CUE to mount", makefile)
        self.assertIn("experimental-bios-boot-integration: dev-rom", makefile)
        self.assertIn("geargrafx_experimental_track_contract.py", makefile)

    def test_existing_lba5_load_execute_regression_stays_separate(self):
        regression = (ROOT / "tests" / "integration" / "geargrafx_cdload_exec.py").read_text(encoding="utf-8")
        self.assertIn('suite.positive("payload_from_lba5", 5)', regression)


if __name__ == "__main__":
    unittest.main()
