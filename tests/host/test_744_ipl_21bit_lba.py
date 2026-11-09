"""Issue #744 (child of #670, Owner decision B): host-side 21-bit LBA checks.

Pure arithmetic against tools/ipl_lba21_model.py plus a static audit of the
assembled loader ROM (tests/integration/ipl_loader_rom.s): the ROM must carry
the 24-bit base/high working-LBA zero-page cells and encode READ(6) CDB byte 1
as (LBA>>16)&0x1F instead of a hardcoded zero.

The boundary table here is executed against the real core by
tests/integration/geargrafx_ipl_loader.py (synthetic media only). This file
never runs a core and never proves bus behavior on its own.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from tools.environment import executable, requires_cc65  # noqa: E402
from tools.ipl_lba21_model import (  # noqa: E402
    CODE_COUNT_RANGE,
    CODE_IPLBLK_RANGE,
    CODE_LBA_RANGE,
    cdb_for,
    decide,
)

SEL = 0x0109  # synthetic INDEX 01 LBA embedded by the integration driver


def accept(sel, iplblk, nsec, dev=True):
    return decide(sel, (iplblk >> 16) & 0xFF, (iplblk >> 8) & 0xFF, iplblk & 0xFF, nsec, dev)


class AcceptBoundaryTests(unittest.TestCase):
    def test_abs_lbas_across_and_above_16bit_are_accepted(self):
        for abs_lba in (0x00FFFF, 0x010000, 0x010001, 0x1FFFFE, 0x1FFFFF):
            with self.subTest(abs_lba=hex(abs_lba)):
                result = accept(SEL, abs_lba - SEL, 1)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["body"], abs_lba)
                self.assertEqual(result["last"], abs_lba)

    def test_nonzero_high_iplblk_byte_accepted_in_range(self):
        result = accept(SEL, 0x010002, 5)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["body"], SEL + 0x010002)

    def test_index01_carry_into_high_byte(self):
        result = accept(SEL, 0xFEF7, 1)  # 0x109 + 0xFEF7 = 0x10000
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["body"], 0x010000)

    def test_last_lba_exactly_at_21bit_top_accepted(self):
        result = accept(SEL, 0x1FFEF6, 1)  # body = 0x1FFFFF, end = 0x200000
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["last"], 0x1FFFFF)

    def test_tail_crossing_16bit_accepted(self):
        result = accept(SEL, 0xFEF5, 3)  # body 0xFFFE, end 0x10001
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["last"], 0x10000)

    def test_public_maximum_count_at_top(self):
        result = accept(0, 0x1FFFF2, 15, dev=False)  # end 0x200001 > 0x200000: refused
        self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})
        result = accept(0, 0x1FFFE1, 31, dev=False)  # end = 0x200000 exactly
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["last"], 0x1FFFFF)


class RefuseBoundaryTests(unittest.TestCase):
    def test_abs_lba_at_and_above_21bit_top_refused(self):
        for abs_lba, iplblk in ((0x200000, 0x1FFEF7), (0xFFFFFF + SEL, 0xFFFFFF)):
            with self.subTest(abs_lba=hex(abs_lba)):
                result = accept(SEL, iplblk, 1)
                self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})

    def test_last_lba_overflow_refused_while_body_in_range(self):
        result = accept(SEL, 0x1FFEF6, 2)  # body 0x1FFFFF, end 0x200001
        self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})

    def test_nsec_zero_refused(self):
        self.assertEqual(accept(SEL, 2, 0), {"ok": False, "code": CODE_COUNT_RANGE})

    def test_nsec_above_format_maximum_refused(self):
        self.assertEqual(accept(SEL, 2, 11), {"ok": False, "code": CODE_COUNT_RANGE})
        self.assertEqual(accept(SEL, 2, 11, dev=False)["ok"], True)
        self.assertEqual(accept(SEL, 2, 32, dev=False), {"ok": False, "code": CODE_COUNT_RANGE})

    def test_iplblk_below_two_refused(self):
        for iplblk in (0x000000, 0x000001):
            with self.subTest(iplblk=hex(iplblk)):
                self.assertEqual(accept(SEL, iplblk, 5), {"ok": False, "code": CODE_IPLBLK_RANGE})

    def test_no_24bit_wraparound(self):
        # SEL + 0xFFFFFF carries out of 24 bits: refused, never wrapped to 0x108.
        result = accept(SEL, 0xFFFFFF, 1)
        self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})

    def test_double_fault_takes_guest_precedence(self):
        # Base carry-out (code 27) precedes the count check (code 22),
        # mirroring the guest: SEL + 0xFFFFFF carries and nsec == 0.
        result = accept(SEL, 0xFFFFFF, 0)
        self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})

    def test_refusal_emits_no_sector_lbas(self):
        result = accept(SEL, 0xFFFFFF, 5)
        self.assertFalse(result["ok"])
        self.assertNotIn("sectors", result)
        self.assertNotIn("probe_cdb", result)


class CarryAndConfusionTests(unittest.TestCase):
    def test_per_sector_carry_0xffff_to_0x10000(self):
        result = accept(SEL, 0xFEF6, 2)  # body 0xFFFF, sectors 0xFFFF and 0x10000
        self.assertTrue(result["ok"], result)
        self.assertEqual([c[3] for c in result["sectors"]], [0xFF, 0x00])
        self.assertEqual([c[1] for c in result["sectors"]], [0x00, 0x01])

    def test_relative_vs_absolute_confusion(self):
        # A relative IPLBLK below 0x10000 can still yield an absolute LBA above it.
        result = accept(SEL, 0xFFFF, 1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["body"], 0x10108)
        self.assertNotEqual(result["body"], 0xFFFF)

    def test_no_16bit_truncation_of_base(self):
        result = accept(SEL, 0xFEF7, 1)
        self.assertEqual(result["body"], 0x10000)
        self.assertNotEqual(result["body"], 0x0000)  # the 16-bit-truncated base

    def test_16bit_wrap_case_stays_full_width(self):
        # Old 16-bit code read base 0x0009 here; the 24-bit base is 0x10009.
        result = accept(SEL, 0xFF00, 1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["body"], 0x10009)

    def test_21bit_wrap_refused(self):
        result = accept(0, 0x1FFFFF, 2)  # end 0x200001
        self.assertEqual(result, {"ok": False, "code": CODE_LBA_RANGE})


class CdbEncodingTests(unittest.TestCase):
    def test_cdb_bytes_exact(self):
        self.assertEqual(cdb_for(0x000109), [0x08, 0x00, 0x01, 0x09, 0x01, 0x00])
        self.assertEqual(cdb_for(0x010000), [0x08, 0x01, 0x00, 0x00, 0x01, 0x00])
        self.assertEqual(cdb_for(0x1FFFFF), [0x08, 0x1F, 0xFF, 0xFF, 0x01, 0x00])

    def test_cdb_byte1_is_top_five_bits_only(self):
        for lba in (0, 1, 0xFF, 0x100, 0xFFFF, 0x10000, 0x123456, 0x1FFFFF):
            with self.subTest(lba=hex(lba)):
                cdb = cdb_for(lba)
                self.assertEqual(cdb[1], (lba >> 16) & 0x1F)
                self.assertLess(cdb[1], 0x20, "byte 1 must never carry LUN/high bits")

    def test_cdb_rejects_out_of_range_lba(self):
        for bad in (-1, 0x200000, 0xFFFFFF):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    cdb_for(bad)

    def test_model_probe_cdb_matches_last_sector(self):
        result = accept(SEL, 0xFEF7, 5)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["probe_cdb"], cdb_for(0x10004))
        self.assertEqual(result["sectors"][-1], cdb_for(0x10004))


@requires_cc65
class RomArtifactTests(unittest.TestCase):
    """The assembled loader must implement the model above (decision B).

    RED before the #744 loader change (hardcoded CDB byte 1, no 21-bit
    encode); GREEN after. This audits artifact bytes, not the model.
    """

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        out = Path(tmp.name)
        ca65, ld65 = executable("ca65"), executable("ld65")
        obj, rom = out / "ipl.o", out / "ipl.pce"
        subprocess.run([ca65, str(ROOT / "tests" / "integration" / "ipl_loader_rom.s"),
                        "-D", f"IPL_SEL=${SEL:04X}", "-o", str(obj), "-g"], check=True)
        subprocess.run([ld65, "-C", str(ROOT / "tests" / "integration" / "ipl_loader_rom.cfg"),
                        "-o", str(rom), str(obj)], check=True)
        cls.image = rom.read_bytes()

    def test_rom_size_unchanged(self):
        self.assertEqual(len(self.image), 0x2000)

    def test_cdb_byte1_is_computed_not_hardcoded(self):
        import re

        # LDA zp / AND #$1F / STA $2F11 (R_CDB+1): the 21-bit CDB encoding.
        self.assertIsNotNone(re.search(rb"\xa5.\x29\x1f\x8d\x11\x2f", self.image))
        # Old 16-bit form: LDA #0 / STA $2F11 (high LBA byte ignored).
        self.assertNotIn(bytes([0xA9, 0x00, 0x8D, 0x11, 0x2F]), self.image)


if __name__ == "__main__":
    unittest.main()
