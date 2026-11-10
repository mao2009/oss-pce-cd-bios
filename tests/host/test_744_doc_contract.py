"""Issue #744 MEDIUM-2: doc §4 keeps the current 21-bit contract, old spec in history only.

docs/670-ipl-information-data-block-acceptance.md §4 must state the current
contract (24-bit IPLBLK, 16-bit INDEX 01, INDEX01+1 info LBA, 24-bit body add,
21-bit READ(6), end <= 0x200000, code 27 over-range refusal, 16-bit crossing
accepted) and keep the pre-#744 16-bit contract inside the explicitly labeled
history subsection (§4.1) only.

The checks are section-scoped (not bare keyword search): the history block may
cite old case names and old rules, but the current-spec region must not. Cited
current case names must really exist in the integration driver, and old names
must not. Spot expectations are re-checked against tools/ipl_lba21_model.py.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from tools.ipl_lba21_model import decide  # noqa: E402

DOC = ROOT / "docs" / "670-ipl-information-data-block-acceptance.md"
DRIVER = ROOT / "tests" / "integration" / "geargrafx_ipl_loader.py"

SEL = 0x0109


def section(text, heading):
    """Return the body of a `## N`-level section (subsections included)."""
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and (start is None):
            if line.startswith("## " + heading):
                start = i
        elif line.startswith("## ") and start is not None:
            end = i
            break
    assert start is not None, f"section {heading} not found"
    return "".join(lines[start:end if end is not None else len(lines)])


def current_spec_region():
    sec4 = section(DOC.read_text(encoding="utf-8"), "4.")
    # Strip the explicitly labeled history subsection: old rules/names may
    # live there, never in the current contract above it.
    cut = sec4.find("### 4.1")
    assert cut != -1, "history subsection ### 4.1 missing from §4"
    return sec4[:cut]


def driver_case_names():
    return set(re.findall(r'name="([A-Za-z0-9_]+)"', DRIVER.read_text(encoding="utf-8")))


class DocSectionStructureTest(unittest.TestCase):
    def test_history_subsection_exists_and_is_labeled_old(self):
        sec4 = section(DOC.read_text(encoding="utf-8"), "4.")
        self.assertIn("### 4.1", sec4)
        hist = sec4[sec4.find("### 4.1"):]
        self.assertRegex(hist, r"旧仕様|履歴|pre-#744")

    def test_current_region_has_no_history_leak(self):
        current = current_spec_region()
        self.assertNotIn("### 4.1", current)


class CurrentContractStatementsTest(unittest.TestCase):
    REQUIRED = (
        "24bit ビッグエンディアン",
        "INDEX01+1",
        "$010000",
        "24bit 加算",
        "21bit",
        "0x1FFFFF",
        "0x200000",
        "code 27",
        "code 5 / copied=0",
        "16bit 境界を跨ぐだけでは拒否しない",
        "24bit 桁溢れ",
        "相対 IPLBLK が 2 以上",
    )

    def test_required_statements_present(self):
        current = current_spec_region()
        for phrase in self.REQUIRED:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, current)

    def test_old_contract_absent_from_current_region(self):
        current = current_spec_region()
        for phrase in (
            "16bit を超えない",
            "bodyl + nsec <= 0xFFFF",
            "base + nsec == 0x10000",
            "neg_body_lba_overflows_16bit",
            "neg_body_lba_tail_wraps_16bit",
            "neg_body_lba_final_sector_at_ffff",
            "neg_lba_ffff_n1_width_refused",
            "neg_iplblk_high_byte_nonzero",
            "上位 8bit は 0 でなければならない",
            "高位バイトが非ゼロなら拒否",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, current)


class CitedCaseNamesTest(unittest.TestCase):
    CURRENT_CASES = (
        "neg_abs_010108_crosses_16bit_probe_refused",
        "neg_abs_00fffe_tail_crosses_16bit_probe_refused",
        "neg_abs_last_ffff_probe_refused",
        "neg_lba_ffff_probe_refused",
        "neg_lba_fffe_n1_passes_width_fails_media_probe",
        "neg_iplblk_high_byte_01_in_range_probe_refused",
        "neg_iplblk_high_byte_80_over_range_refused",
        "neg_iplblk_high_byte_ff_over_range_refused",
        "neg_iplblk_0100_passes_lba_checks_fails_media_probe",
        "neg_abs_1fffff_n1_passes_width_fails_media_probe",
        "neg_abs_1fffff_n2_tail_over_width_refused",
        "neg_abs_200000_over_width_refused",
        "neg_iplblk_ffffff_carry_out_width_refused",
        "pos_last_body_sector_is_last_disc_frame",
        "neg_last_body_sector_one_past_disc_end",
        "neg_single_sector_at_first_frame_past_end",
        "neg_body_entirely_beyond_disc",
        "neg_body_tail_beyond_disc_refused_before_copy",
    )
    OLD_CASES = (
        "neg_body_lba_overflows_16bit",
        "neg_body_lba_tail_wraps_16bit",
        "neg_body_lba_final_sector_at_ffff",
        "neg_lba_ffff_n1_width_refused",
        "neg_iplblk_high_byte_nonzero",
    )

    def test_current_cases_cited_and_real(self):
        current = current_spec_region()
        driver_names = driver_case_names()
        for name in self.CURRENT_CASES:
            with self.subTest(name=name):
                self.assertIn(name, current, f"{name} not cited in §4 current spec")
                self.assertIn(name, driver_names, f"{name} cited but missing from driver")

    def test_old_cases_not_in_driver(self):
        driver_names = driver_case_names()
        for name in self.OLD_CASES:
            with self.subTest(name=name):
                self.assertNotIn(name, driver_names)


class ModelAgreementTest(unittest.TestCase):
    def accept(self, iplblk, nsec):
        return decide(SEL, (iplblk >> 16) & 0xFF, (iplblk >> 8) & 0xFF,
                      iplblk & 0xFF, nsec, True)

    def test_doc_boundary_claims_match_model(self):
        # end == 0x200000 accepted; end > 0x200000 refused (code 27).
        self.assertTrue(self.accept(0x1FFEF6, 1)["ok"])
        self.assertEqual(self.accept(0x1FFEF6, 1)["last"], 0x1FFFFF)
        self.assertEqual(self.accept(0x1FFEF6, 2), {"ok": False, "code": 27})
        self.assertEqual(self.accept(0x1FFEF7, 1), {"ok": False, "code": 27})
        # 24-bit carry-out refused.
        self.assertEqual(self.accept(0xFFFFFF, 1), {"ok": False, "code": 27})
        # 16-bit crossing accepted when in 21-bit range.
        self.assertTrue(self.accept(0xFFFF, 5)["ok"])
        self.assertTrue(self.accept(0xFEF5, 1)["ok"])


if __name__ == "__main__":
    unittest.main()
