"""Host ledger-consistency checks for #726 (synthetic only, no emulator/BIOS/ROM/core).

Verifies that docs/726-367-evidence.md matches the #367 sources it cites and
does not promote any unmeasured range to SAFE:
- fixture row counts / verdicts (no runtime device writer to WRAM, no UNKNOWN),
- static audit gate (always BLOCKED, length-0 worst case, TIA superset),
- driver case sets (matrix 51, midrefusal 30 / per-op walks, preblock 7 families),
- ledger discipline (BLOCKED + release_eligible=false + SAFE_IN_SCOPE ceiling,
  explicit NOT-SAFE rows, CPU vs device distinction, no gate opening).
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import block_xfer_audit as B  # noqa: E402
import geargrafx_block_midrefusal as MR  # noqa: E402
import geargrafx_block_preblock as PB  # noqa: E402
import geargrafx_block_transfer_matrix as MX  # noqa: E402

DOC = ROOT / "docs" / "726-367-evidence.md"
FIXTURE = ROOT / "tests" / "fixtures" / "367_device_write_sites.tsv"


def load_fixture_rows():
    rows = []
    for raw in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        cells = raw.split("\t")
        assert len(cells) == 6, f"bad column count: {raw!r}"
        rows.append(dict(zip(["id", "file", "line", "role", "target", "verdict"], cells)))
    return rows


def runtime_wram_device_writers(rows):
    return [r["id"] for r in rows
            if r["target"] == "WRAM" and r["role"] in ("device_store", "unknown_store")]


class LedgerExistsTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(DOC.is_file(), "docs/726-367-evidence.md must exist")


class FixtureMatchTest(unittest.TestCase):
    def test_25_rows_no_runtime_device_writer_to_wram(self):
        rows = load_fixture_rows()
        self.assertEqual(len(rows), 25)
        self.assertEqual(runtime_wram_device_writers(rows), [])

    def test_no_unknown_verdict(self):
        rows = load_fixture_rows()
        self.assertEqual([r["id"] for r in rows if r["verdict"] == "UNKNOWN"], [])

    def test_five_block_stores_per_byte_step(self):
        rows = load_fixture_rows()
        blk = [r for r in rows if r["role"] == "cpu_block_store"]
        self.assertEqual(len(blk), 5)
        self.assertTrue(all(r["verdict"] == "PER_BYTE_STEP" for r in blk))

    def test_doc_cites_25_rows_and_lists_roles(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("25 rows", text)
        for token in ("NOT_MAIN_RAM", "RESET_ONLY", "CPU_ORIGINATED", "HOST_ONLY", "PER_BYTE_STEP"):
            self.assertIn(token, text)


class StaticModelMatchTest(unittest.TestCase):
    def test_gate_always_blocked_and_mid_stop_unmeasured(self):
        mpr = [0xF8, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF]
        allowed = B.policy_from_mpr(mpr, {0x00, 0x01}, (0x2000, 0x4000))
        for op in B.OPS:
            r = B.audit(op, 0x2000, 0x2100, 16, allowed)
            self.assertEqual(r["gate"], B.BLOCKED, op)
            self.assertEqual(r["mid_instruction_stop"], "UNMEASURED", op)

    def test_length_zero_is_worst_case(self):
        self.assertEqual(len(B.destination_set("TII", 0x2000, 0)), B.MAX_BYTES)

    def test_tia_superset_is_conservative(self):
        self.assertIn(0x2104, B.destination_set("TIA", 0x2100, 4))

    def test_doc_marks_static_not_safe(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("mid_instruction_stop", text)
        self.assertIn("UNMEASURED", text)
        self.assertIn("Static only, BLOCKED", text)


class DriverCaseSetMatchTest(unittest.TestCase):
    def test_matrix_51_cases(self):
        self.assertEqual(len(MX.CASES), 51)
        self.assertEqual(len(set(MX.CASES)), 51)
        self.assertEqual(len([c for c in MX.CASES if c in MX.LEN_CASES]), 25)
        self.assertEqual(len(MX.DENY_CASES), 15)
        self.assertIn("tii-len0", MX.CASES)
        self.assertNotIn("tii-len0", MX.LEN_CASES)

    def test_midrefusal_30_cases_and_distinct_walks(self):
        self.assertEqual(len(list(MR.cases())), 30)
        seqs = {op: tuple(MR.CROSS[op][0]) for op in MR.OPS}
        self.assertEqual(seqs["tii"], seqs["tai"])
        self.assertNotEqual(seqs["tii"], seqs["tdd"])

    def test_preblock_7_armed_families(self):
        self.assertEqual(len(PB.ARMED_MODES), 7)
        for mode in PB.ARMED_MODES:
            self.assertIn(mode, PB.BLOCK_EXPECTED_PC)

    def test_doc_cites_case_sets(self):
        text = DOC.read_text(encoding="utf-8")
        for token in ("51-case matrix", "30 cases", "7 armed families", "tii-len0"):
            self.assertIn(token, text)


class NoPromotionTest(unittest.TestCase):
    def test_gate_and_release_markers(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("BLOCKED", text)
        self.assertIn("release_eligible=false", text)
        self.assertIn("SAFE_IN_SCOPE", text)
        self.assertIn("BLOCKED_ABI", text)

    def test_explicit_not_safe_section(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("Explicitly NOT SAFE", text)
        for token in ("never promoted", "never SAFE", "UNKNOWN"):
            self.assertIn(token, text)

    def test_mid_transfer_and_device_dma_are_not_safe(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?s)mid-transfer.*BLOCKED")
        self.assertRegex(text, r"(?s)device-origin.*BLOCKED|W17.*BLOCKED|device.*never SAFE")

    def test_cpu_vs_device_distinction(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("cpu_store", text)
        self.assertIn("device_store", text)
        self.assertIn("CPU_ORIGINATED", text)
        self.assertRegex(text, r"(?s)[Cc]onfus|not confused|must not be mislabelled|vs DMA|vs device")

    def test_no_bare_safe_for_unmeasured(self):
        # The ledger may use SAFE only inside the capped phrase SAFE_IN_SCOPE
        # or inside an explicit negation (NOT SAFE / never SAFE / no SAFE claim).
        text = DOC.read_text(encoding="utf-8")
        for m in re.finditer(r"\bSAFE\b", text):
            window = text[max(0, m.start() - 20):m.end() + 10]
            if "SAFE_IN_SCOPE" in window:
                continue
            lowered = window.lower()
            sentence_start = text.rfind(".", 0, m.start())
            sentence_end = text.find(".", m.end())
            sentence = text[sentence_start + 1:sentence_end + 1 if sentence_end != -1 else len(text)].lower()
            allowed = ("not safe" in lowered or "never safe" in lowered or "no safe" in lowered
                       or "excluded from safe" in sentence or "promoted to safe" in sentence
                       or "promotion" in sentence or "never promoted" in sentence)
            self.assertTrue(allowed,
                f"bare SAFE outside SAFE_IN_SCOPE/negation is forbidden: ...{window}...")

    def test_no_gate_opening_claim(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)gate (is |now |opened|released|unblocked)")
        self.assertIn("no gate opening", text.lower() + "no gate opening" if "no gate opening" in text.lower() else text)


if __name__ == "__main__":
    unittest.main()
