"""Host tests for the C-3 static checks, CI-2 (block transfer contract). Synthetic source trees; no emulator."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import c3_rules_transfer as T  # noqa: E402
import c3_static_check as C  # noqa: E402

API = """.setcpu "huc6280"
.segment "OVERRIDES"
VDC_DATA = $0002
api_copy:
    tii $3F00, $3E00, 16
    tia $E100, VDC_DATA, 32
    rts
"""
BASE = {
    "schema": 1,
    "rule_groups": ["CI-1", "CI-2"],
    "production_allowlist": {"status": "UNDECIDED", "approved_by": None, "review_ref": None},
    "regions": {
        "_note": "facts, not an allowlist",
        "wram": {"kind": "ram", "bank": "0xF8"},
        "rom_bank0": {"kind": "rom", "bank": "0x00"},
        "vdc_data_port": {"kind": "mmio", "bank": "0xFF", "offsets": ["0x0002", "0x0003"]},
    },
    "scope": {"bios": ["src/api.s"], "fixture": [], "fixture_reasons": {}},
    "files": {"src/api.s": {"role": "api", "tam": [], "transfers": [
        {"op": "tii", "src": "0x3F00", "dst": "0x3E00", "len": "0x10", "mpr": {"1": "0xF8"},
         "src_region": "wram", "dst_region": "wram", "purpose": "work RAM copy"},
        {"op": "tia", "src": "0xE100", "dst": "0x0002", "len": "0x20", "mpr": {"7": "0x00", "0": "0xFF"},
         "src_region": "rom_bank0", "dst_region": "vdc_data_port", "purpose": "VRAM upload"},
    ]}},
}


def run(source, contract):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src/api.s").write_text(source, encoding="utf-8")
        path = root / "contract.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        report, _ = C.run(path, root)
    return report


def by_rule(report, rule):
    return [f["verdict"] for f in report.findings if f["rule"] == rule]


class SequenceTests(unittest.TestCase):
    """Address sequences of the pinned core (#648)."""

    def test_sequences(self):
        self.assertEqual(T.sequences("tii", 0x10, 0x20, 3), ([0x10, 0x11, 0x12], [0x20, 0x21, 0x22]))
        self.assertEqual(T.sequences("tdd", 0x10, 0x20, 3), ([0x10, 0x0F, 0x0E], [0x20, 0x1F, 0x1E]))
        self.assertEqual(T.sequences("tin", 0x10, 0x20, 3)[1], [0x20, 0x20, 0x20])
        self.assertEqual(T.sequences("tia", 0x10, 0x20, 4)[1], [0x20, 0x21, 0x20, 0x21])
        self.assertEqual(T.sequences("tai", 0x10, 0x20, 4)[0], [0x10, 0x11, 0x10, 0x11])
        self.assertEqual(len(T.sequences("tii", 0, 0, 0)[0]), 0x10000)   # length 0 = 65536


class PositiveTests(unittest.TestCase):
    def test_wram_copy_ok_and_vdc_transfer_blocked_not_fail(self):
        report = run(API, BASE)
        self.assertFalse(report.failed(), report.findings)
        self.assertIn(C.OK, by_rule(report, "CI-2.05"))
        self.assertIn(C.BLOCKED, by_rule(report, "CI-2.06"))      # legitimate VDC transfer: not refused
        self.assertIn(C.NOT_STATIC, by_rule(report, "CI-2.07"))

    def test_repository_contract_has_no_fail(self):
        report, _ = C.run(C.CONTRACT, ROOT)
        self.assertFalse(report.failed(), [f for f in report.findings if f["verdict"] == C.FAIL])
        self.assertTrue(any("0 block transfer site" in f["where"] for f in report.findings if f["rule"] == "CI-2.07"))


class NegativeTests(unittest.TestCase):
    def assert_fail(self, rule, source=API, contract=BASE):
        report = run(source, contract)
        self.assertIn(C.FAIL, by_rule(report, rule), report.findings)

    def test_undeclared_transfer(self):
        self.assert_fail("CI-2.01", API.replace("    rts", "    tin $3F00, $3E00, 1\n    rts"))

    def test_non_constant_operand(self):
        self.assert_fail("CI-2.02", API.replace("tii $3F00, $3E00, 16", "tii src_ptr, $3E00, 16"))

    def test_operand_mismatch(self):
        self.assert_fail("CI-2.03", API.replace("tii $3F00, $3E00, 16", "tii $3F00, $3E00, 17"))

    def test_undeclared_page_mapping(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][1]["mpr"] = {"7": "0x00"}
        self.assert_fail("CI-2.04", contract=c)

    def test_destination_leaves_the_region_at_a_page_boundary(self):   # TII crossing $3FFF -> $4000
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][0].update(dst="0x3FF8", mpr={"1": "0xF8", "2": "0xFF"})
        self.assert_fail("CI-2.05", API.replace("$3E00", "$3FF8"), c)

    def test_tdd_crossing_downwards(self):   # mutation of the address model: TDD walks down into page 0
        src = API.replace("tii $3F00, $3E00, 16", "tdd $3F00, $2004, 16")
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][0].update(op="tdd", dst="0x2004", mpr={"0": "0xFF", "1": "0xF8"})
        self.assert_fail("CI-2.05", src, c)

    def test_tia_vdc_overrun(self):   # TIA alternates dst, dst+1: $0003/$0004 leaves the two-port region
        src = API.replace("VDC_DATA = $0002", "VDC_DATA = $0003")
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][1]["dst"] = "0x0003"
        self.assert_fail("CI-2.05", src, c)

    def test_rom_destination(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][0].update(dst_region="rom_bank0")
        self.assert_fail("CI-2.05", contract=c)

    def test_unknown_region(self):
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][0]["src_region"] = "nowhere"
        self.assert_fail("CI-2.05", contract=c)

    def test_vdc_transfer_with_ram_destination_region_is_refused(self):   # mmio must be declared as such
        c = copy.deepcopy(BASE)
        c["files"]["src/api.s"]["transfers"][1]["dst_region"] = "wram"
        self.assert_fail("CI-2.05", contract=c)


if __name__ == "__main__":
    unittest.main()
