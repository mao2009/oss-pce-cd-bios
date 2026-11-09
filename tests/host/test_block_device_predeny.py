"""E4/E5 host tests: block/device pre-deny and mutation suite (Issue #737).

Synthetic only, pinned Geargrafx b49ae82a. No emulator, BIOS, ROM or genuine callee.
Positive control lands (PERMITTED_STATIC_ONLY) but gate stays BLOCKED; unguarded mutants
reproduce the forbidden side-effect and must not yield false PASS (E5, 15 mutations here).
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import block_device_predeny as P

MPR = [0xF8, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06]
WIN = (0x2000, 0x4000)
BANKS = {0x00, 0x01}
REFUSED = "REFUSED_PREEXEC"
PERMIT = "PERMITTED_STATIC_ONLY"


def pf(op="TII", src=0x2000, dst=0x2100, length=8, mpr=None, win=None, banks=None, **kw):
    return P.preflight(op, src, dst, length, mpr or list(MPR), win or WIN,
                       banks if banks is not None else set(BANKS), **kw)


def dev(id, role, target, verdict):
    return {"id": id, "file": "x.cpp", "line": "1", "role": role, "target": target, "verdict": verdict}


class PositiveTest(unittest.TestCase):
    def test_positive_lands_but_blocked(self):
        r = pf()
        self.assertEqual(r["verdict"], PERMIT)
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)
        self.assertEqual(r["jsr_count"], 0)

    def test_all_five_ops_positive(self):
        for op in P.OPS:
            self.assertEqual(pf(op=op)["verdict"], PERMIT, op)

    def test_pinned_revision(self):
        deps = json.loads((ROOT / "tools" / "dependencies.json").read_text(encoding="utf-8"))
        self.assertEqual(P.PINNED_REVISION, deps["geargrafx"]["revision"])
        self.assertTrue(P.PINNED_REVISION.startswith("b49ae82a"))


class PreflightNegativeTest(unittest.TestCase):
    def test_per_byte_outside_all_ops(self):
        cases = [("TII", 0x3FF8, 16), ("TIN", 0x4000, 1), ("TDD", 0x4000, 4), ("TIA", 0x3FF9, 7), ("TAI", 0x3FF8, 16)]
        for op, dst, n in cases:
            r = pf(op=op, dst=dst, length=n)
            self.assertEqual(r["verdict"], REFUSED, op)
            self.assertIsNotNone(r["first_refused"], op)

    def test_hw_bank_refused_even_if_listed(self):
        mpr = [0xF8, 0, 1, 2, 3, 4, 5, 0xFF]
        self.assertEqual(pf(mpr=mpr, banks={0, 1, 0xFF}, dst=0xE000, win=(0xE000, 0x10000))["verdict"], REFUSED)

    def test_rom_bank_not_allowed(self):
        self.assertEqual(pf(banks={0x01})["verdict"], REFUSED)

    def test_ff_mpr_refused(self):
        mpr = [0xF8, 0, 1, 2, 3, 4, 0xFF, 6]
        self.assertEqual(pf(mpr=mpr)["verdict"], REFUSED)

    def test_unknown_op_and_malformed_mpr(self):
        self.assertEqual(pf(op="TXX")["verdict"], REFUSED)
        self.assertEqual(pf(mpr=[1, 2])["verdict"], REFUSED)
        self.assertEqual(pf(mpr=[0xF8] * 7 + [0x100])["verdict"], REFUSED)

    def test_gg_testing_and_libretro(self):
        self.assertEqual(pf(build={"gg_testing": True})["verdict"], REFUSED)
        self.assertEqual(pf(build={"libretro": True})["verdict"], REFUSED)

    def test_length0_worst_case_refused(self):
        r = pf(length=0, dst=0x3FF0)
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(len(P.destination_set("TII", 0x3FF0, 0)), P.MAX_BYTES)
        self.assertEqual(pf(length=16)["stores_in_one_instruction"], 16)

    def test_src_side_effect_same_value_temp(self):
        self.assertEqual(pf(src=0x4104)["verdict"], REFUSED)
        self.assertEqual(pf(same_value=True)["verdict"], REFUSED)
        self.assertEqual(pf(temp_restore=True)["verdict"], REFUSED)

    def test_gate_never_passes(self):
        for r in (pf(), pf(op="TXX"), pf(dst=0x3FF8), pf(mpr=[1])):
            self.assertEqual(r["gate"], P.BLOCKED)
            self.assertEqual(r["execution"], P.BLOCKED)
            self.assertNotIn(r["verdict"], ("PASS", "ALLOWED"))


class DeviceAuditTest(unittest.TestCase):
    def test_drained_no_unknown(self):
        rows = [dev("DEV-01", "device_store", "VRAM", "NOT_MAIN_RAM"),
                dev("DEV-04", "reset_store", "WRAM", "RESET_ONLY"),
                dev("CPU-04", "cpu_store", "WRAM", "CPU_ORIGINATED")]
        self.assertEqual(P.device_audit(rows)["verdict"], "DRAINED_NO_UNKNOWN")

    def test_unknown_and_device_wram_blocked(self):
        self.assertEqual(P.device_audit([dev("UNK-01", "unknown_store", "ANY", "UNKNOWN")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("DEV-99", "device_store", "WRAM", "NOT_MAIN_RAM")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("UNK-99", "unknown_store", "WRAM", "UNKNOWN")])["bad_ids"], ["UNK-99"])

    def test_cpu_wram_is_not_device_writer(self):
        self.assertEqual(P.device_audit([dev("CPU-99", "cpu_store", "WRAM", "CPU_ORIGINATED")])["bad_ids"], [])

    def test_fixture_unk_drained(self):
        text = (ROOT / "tests" / "fixtures" / "367_device_write_sites.tsv").read_text(encoding="utf-8")
        rows = []
        for raw in text.splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            c = raw.split("\t")
            rows.append({"id": c[0], "role": c[3], "target": c[4], "verdict": c[5]})
        self.assertEqual(P.device_audit(rows)["verdict"], "DRAINED_NO_UNKNOWN")
        self.assertFalse(any(r["verdict"] == "UNKNOWN" for r in rows))


class MutationSuiteTest(unittest.TestCase):
    def test_m01_drop_hw_bank(self):
        mpr = [0xF8, 0, 1, 2, 3, 4, 5, 0xFF]
        self.assertEqual(pf(mpr=mpr, banks={0, 1, 0xFF}, dst=0xE000, win=(0xE000, 0x10000))["verdict"], REFUSED)
        self.assertTrue(all(0xE000 <= a < 0x10000 and mpr[(a >> 13) & 7] in {0, 1, 0xFF} for a in P.destination_set("TII", 0xE000, 4)))

    def test_m02_drop_window(self):
        self.assertEqual(pf(dst=0x3FFC, length=8)["verdict"], REFUSED)
        outside = [a for a in P.destination_set("TII", 0x3FFC, 8) if a >= 0x4000]
        self.assertTrue(outside)

    def test_m03_tdd_as_tii(self):
        self.assertEqual(pf(op="TDD", dst=0x3FFE, length=4)["verdict"], PERMIT)
        self.assertNotEqual(P.destination_set("TDD", 0x3FFE, 4), {(0x3FFE + i) & 0xFFFF for i in range(4)})

    def test_m04_len0_as_zero(self):
        self.assertEqual(pf(length=0, dst=0x3FF0)["verdict"], REFUSED)
        self.assertNotEqual(len(P.destination_set("TII", 0x3FF0, 0)), 0)

    def test_m05_ff_mpr_allowed(self):
        mpr = [0xF8, 0, 1, 2, 3, 4, 0xFF, 6]
        self.assertEqual(pf(mpr=mpr)["verdict"], REFUSED)

    def test_m06_gg_testing_ignored(self):
        self.assertEqual(pf(build={"gg_testing": True})["verdict"], REFUSED)

    def test_m07_libretro_ignored(self):
        self.assertEqual(pf(build={"libretro": True})["verdict"], REFUSED)

    def test_m08_unknown_as_safe(self):
        self.assertEqual(P.device_audit([dev("UNK-01", "unknown_store", "ANY", "UNKNOWN")])["verdict"], "BLOCKED_AUDIT")

    def test_m09_unknown_op_allow(self):
        self.assertEqual(pf(op="TXX")["verdict"], REFUSED)
        with self.assertRaises(ValueError):
            P.destination_set("TXX", 0, 1)

    def test_m10_missing_mpr_allow(self):
        self.assertEqual(pf(mpr=[1, 2])["verdict"], REFUSED)

    def test_m11_same_value_ignored(self):
        self.assertEqual(pf(same_value=True)["verdict"], REFUSED)

    def test_m12_temp_restore_ignored(self):
        self.assertEqual(pf(temp_restore=True)["verdict"], REFUSED)

    def test_m13_src_read_ignored(self):
        self.assertEqual(pf(src=0x4104)["verdict"], REFUSED)
        self.assertIn(0x4104, P.source_set("TII", 0x4104, 1))

    def test_m14_per_byte_partial(self):
        r = pf(dst=0x3FFE, length=4)
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["mid_instruction_stop"], "BLOCKED_NOT_SAFE_TO_RESUME")

    def test_m15_unguarded_reproduces_side_effect(self):
        forbidden = 0x4000
        self.assertIn(forbidden, P.destination_set("TIA", 0x3FF9, 7))
        self.assertEqual(pf(op="TIA", dst=0x3FF9, length=7)["verdict"], REFUSED)


if __name__ == "__main__":
    unittest.main()
