"""Host checks for the #367 device-origin write record (synthetic, no emulator, BIOS or ROM read).

The fixture is a recorded source-audit result (locations and verdicts only). These tests check that the
record is internally consistent, that the audit doc lists every row, and that the WRAM-writer check is
sensitive: an injected runtime device writer to WRAM must be reported.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "367_device_write_sites.tsv"
DOC = ROOT / "docs" / "research" / "367-device-write-audit.md"

VERDICTS = {"NOT_MAIN_RAM", "RESET_ONLY", "UNKNOWN", "PER_BYTE_STEP"}
COLUMNS = ["id", "file", "line", "role", "target", "verdict"]


def load_rows(text):
    rows = []
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        cells = raw.split("\t")
        assert len(cells) == len(COLUMNS), f"bad column count: {raw!r}"
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def runtime_wram_device_writers(rows):
    """Device-origin stores that can reach WRAM at run time, or are unresolved and may reach it."""
    return [r["id"] for r in rows
            if r["target"] == "WRAM" and r["role"] in ("device_store", "unknown_store")]


class DeviceWriteRecordTest(unittest.TestCase):
    def setUp(self):
        self.rows = load_rows(FIXTURE.read_text(encoding="utf-8"))

    def test_schema_and_verdicts(self):
        self.assertTrue(self.rows)
        for r in self.rows:
            self.assertIn(r["verdict"], VERDICTS, r["id"])
            self.assertTrue(r["line"].isdigit(), r["id"])

    def test_no_runtime_device_writer_to_wram(self):
        self.assertEqual(runtime_wram_device_writers(self.rows), [])

    def test_unknown_rows_are_not_claimed_safe(self):
        unknown = [r["id"] for r in self.rows if r["verdict"] == "UNKNOWN"]
        self.assertEqual(unknown, ["UNK-01", "UNK-02"])

    def test_block_transfer_stores_are_five_per_byte_steps(self):
        blk = [r for r in self.rows if r["role"] == "cpu_block_store"]
        self.assertEqual(len(blk), 5)
        self.assertTrue(all(r["verdict"] == "PER_BYTE_STEP" for r in blk))

    def test_doc_lists_every_row(self):
        # The doc groups ids as e.g. "DEV-04/05", so expand the groups before comparing.
        doc = DOC.read_text(encoding="utf-8")
        listed = set()
        for group in re.findall(r"(?:DEV|UNK|BLK)-\d\d(?:/\d\d)*", doc):
            prefix, *nums = re.split(r"[-/]", group)
            listed.update(f"{prefix}-{n}" for n in nums)
        self.assertEqual({r["id"] for r in self.rows}, listed)

    def test_mutation_injected_wram_device_writer_is_reported(self):
        # A mutant record with a runtime device store into WRAM must be caught by the check.
        mutant = self.rows + [dict(zip(COLUMNS, ["DEV-99", "x.cpp", "1", "device_store", "WRAM", "NOT_MAIN_RAM"]))]
        self.assertEqual(runtime_wram_device_writers(mutant), ["DEV-99"])

    def test_mutation_unknown_store_to_wram_is_reported(self):
        mutant = self.rows + [dict(zip(COLUMNS, ["UNK-99", "x.cpp", "1", "unknown_store", "WRAM", "UNKNOWN"]))]
        self.assertEqual(runtime_wram_device_writers(mutant), ["UNK-99"])


if __name__ == "__main__":
    unittest.main()
