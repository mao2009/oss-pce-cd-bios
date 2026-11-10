"""Issue #744 MEDIUM-1: builder SEL range matches the ROM/model 16-bit contract.

tools/build_dev_ipl_loader_bios.build_ipl_loader_bios must accept the full
16-bit INDEX 01 range (0x0000..0xFFFF, strict int) that tests/integration/
ipl_loader_rom.s and tools/ipl_lba21_model.py implement, and refuse everything
else. The pre-#744 0xFFF0 "headroom" bound guarded the old 16-bit body math;
since #744 the guest computes the base in 24 bits and refuses over-range
itself (code 27), so the builder only enforces the embedding contract.

Accepting an input is not claiming a real-core media read succeeds: INDEX 01
= 0xFFFF yields info-sector LBA 0x010000 (pinned by test_744_ipl_21bit_lba and
the $FFFF ROM artifact), whose real-core read is UNKNOWN on the 300-frame
synthetic disc, and INDEX 01 = 0 with no valid info sector is refused
in-guest, never executed.

The range check runs before any file IO, so invalid inputs raise ValueError
without touching media; valid inputs proceed past validation (the missing
original ROM then fails with OSError, which is the assertion signal here --
no cc65, no core, no media needed).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from tools.ipl_lba21_model import cdb_for, info_lba  # noqa: E402

MISSING_ORIGINAL = Path("definitely-not-a-real-original-rom.pce")


def build_sel(sel):
    return build_ipl_loader_bios(MISSING_ORIGINAL, Path("out"), sel)


class BuilderSelRangeTest(unittest.TestCase):
    def test_accepted_boundary_values_pass_validation(self):
        for sel in (0x0000, 0x0109, 0xFFEF, 0xFFF0, 0xFFFE, 0xFFFF):
            with self.subTest(sel=hex(sel)):
                try:
                    build_sel(sel)
                except ValueError as error:
                    self.assertNotIn("IPL_SEL", str(error),
                                     f"{sel:#x} wrongly refused: {error}")
                except OSError:
                    pass  # expected: validation passed, missing original ROM
                else:
                    self.fail(f"{sel:#x} unexpectedly built without an original")

    def test_rejected_values_raise_sel_error(self):
        for bad in (-1, 0x10000, 0x1FFFFF, True, False, "65535", "0x109",
                    None, 1.0, (0x109,), [0x109]):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError) as ctx:
                    build_sel(bad)
                self.assertIn("IPL_SEL", str(ctx.exception))

    def test_bool_is_not_an_int_sel(self):
        # type(True) is int == False is required: True == 1 must not slip in.
        self.assertIs(type(True), bool)
        with self.assertRaises(ValueError):
            build_sel(True)
        with self.assertRaises(ValueError):
            build_sel(False)

    def test_ffff_info_lba_matches_guest_and_model(self):
        # INDEX 01 = 0xFFFF -> info-sector LBA 0x010000 with the exact bus CDB.
        self.assertEqual(info_lba(0xFFFF), 0x010000)
        self.assertEqual(cdb_for(0x010000), [0x08, 0x01, 0x00, 0x00, 0x01, 0x00])
        self.assertEqual(info_lba(0x0000), 0x000001)

    def test_out_of_range_sel_never_reaches_assembler(self):
        # The OSError-vs-ValueError split proves validation precedes all IO:
        # no assembler invocation, no media touch for rejected inputs.
        with self.assertRaises(ValueError):
            build_sel(0x10000)


if __name__ == "__main__":
    unittest.main()
