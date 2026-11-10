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
        # Sources stay inside page 1 (bank 0x00) so the source-bank gate has a
        # classified in-allowlist read to approve; the old default src=0x2000
        # straddled the page 0/1 boundary for TDD (0x2000-7 into bank $F8) and
        # encoded the unchecked-source bug fixed here.
        for op in P.OPS:
            self.assertEqual(pf(op=op, src=0x2010)["verdict"], PERMIT, op)

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
        # Use an in-page source: the old default src=0x2000 straddled into page 0
        # (bank $F8, outside {0,1}) and only landed because the source bank was
        # unchecked. The direction semantics under test are unchanged.
        self.assertEqual(pf(op="TDD", src=0x2010, dst=0x3FFE, length=4)["verdict"], PERMIT)
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


class ForbiddenMmioTest(unittest.TestCase):
    def test_dst_hits_forbidden_mmio_even_with_wide_window_and_banks(self):
        for mmio in sorted(P.FORBIDDEN_MMIO):
            r = pf(dst=mmio, win=(0, 0x10000), banks={0xF8, 0, 1, 2, 3, 4, 5, 6})
            self.assertEqual(r["verdict"], REFUSED, hex(mmio))
            self.assertEqual(r["reason"], "dst_forbidden_mmio", hex(mmio))
            self.assertEqual(r["first_refused"], mmio, hex(mmio))

    def test_dst_reaching_forbidden_mmio_mid_range_by_all_ops(self):
        pairs = [("TII", 0x1800), ("TIN", 0x1802), ("TDD", 0x1404), ("TIA", 0x1803), ("TAI", 0x1401)]
        for op, dst in pairs:
            self.assertTrue(P.destination_set(op, dst, 4) & P.FORBIDDEN_MMIO, op)
            r = pf(op=op, dst=dst, length=4, win=(0, 0x10000), banks={0xF8, 0, 1, 2, 3, 4, 5, 6})
            self.assertEqual(r["verdict"], REFUSED, op)

    def test_src_reading_forbidden_mmio_refused(self):
        for mmio in sorted(P.FORBIDDEN_MMIO):
            r = pf(src=mmio)
            self.assertEqual(r["verdict"], REFUSED, hex(mmio))
            self.assertEqual(r["reason"], "src_forbidden_mmio", hex(mmio))

    def test_forbidden_mmio_not_in_default_positive_path(self):
        self.assertEqual(pf(dst=0x2100)["verdict"], PERMIT)
        self.assertFalse(P.destination_set("TII", 0x2100, 8) & P.FORBIDDEN_MMIO)


class OperandValidationTest(unittest.TestCase):
    def test_malformed_src_dst_refused_never_raises(self):
        for bad in (0x10000, -1, "2100", None, 1.5, True, False):
            for key in ("src", "dst"):
                r = pf(**{key: bad})
                self.assertEqual(r["verdict"], REFUSED, (key, bad))
                self.assertEqual(r["gate"], P.BLOCKED, (key, bad))

    def test_destination_set_raises_valueerror_on_malformed(self):
        for bad in (0x10000, -1, "2100", None, True):
            with self.assertRaises(ValueError):
                P.destination_set("TII", bad, 1)
            with self.assertRaises(ValueError):
                P.source_set("TII", bad, 1)

    def test_bool_length_is_malformed(self):
        for bad in (True, False, 1.5, "8", None, -1, 65537):
            r = pf(length=bad)
            self.assertEqual(r["verdict"], REFUSED, bad)
            self.assertEqual(r["reason"], "bad_length", bad)

    def test_length_edges(self):
        self.assertEqual(pf(length=0, dst=0x2100)["verdict"], REFUSED)  # full range reads 0x2104
        r = pf(length=65536, dst=0x2100, win=(0, 0x10000), banks={0xF8, 0, 1, 2, 3, 4, 5, 6})
        self.assertEqual(r["verdict"], REFUSED)  # full range reads a side-effect addr
        self.assertEqual(pf(length=65537)["reason"], "bad_length")
        with self.assertRaises(ValueError):
            P.destination_set("TII", 0, 65537)

    def test_wrap_destinations_are_fully_checked(self):
        r = pf(dst=0xFFFE, length=4)
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "dst_outside_window")
        self.assertIn(0x0000, P.destination_set("TII", 0xFFFE, 4))
        r2 = pf(op="TDD", dst=1, length=4)
        self.assertEqual(r2["verdict"], REFUSED)
        self.assertIn(0xFFFF, P.destination_set("TDD", 1, 4))

    def test_malformed_allowlist_window_mpr_build_refused(self):
        for bad in ("01", 123, {0.5}, {"a"}, {256}, {}, (x for x in [0, 1])):
            r = pf(banks=bad)
            self.assertEqual(r["verdict"], REFUSED, bad)
            self.assertEqual(r["reason"], "bad_window_allowlist", bad)
        for bad in ((True, 0x4000), (0x2000, 16384.0), (0x4000, 0x2000), (0x2000, 0x2000), "x"):
            r = pf(win=bad)
            self.assertEqual(r["verdict"], REFUSED, bad)
        self.assertEqual(pf(mpr=[0xF8, True, 1, 2, 3, 4, 5, 6])["verdict"], REFUSED)
        for bad in ("gg_testing", 123, ["gg_testing"]):
            r = pf(build=bad)
            self.assertEqual(r["verdict"], REFUSED, bad)
            self.assertEqual(r["reason"], "malformed_build", bad)

    def test_build_key_case_insensitive(self):
        for key in ("Gg_Testing", "GG_TESTING", "gg_testing", "LibRetro", "LIBRETRO", "libretro"):
            r = pf(build={key: True})
            self.assertEqual(r["verdict"], REFUSED, key)
            self.assertEqual(r["reason"], "testing_build", key)


class DeviceAuditHardeningTest(unittest.TestCase):
    def test_malformed_rows_blocked_never_raises(self):
        for bad in ("notalist", 123, None, {"id": "X"}):
            r = P.device_audit(bad)
            self.assertEqual(r["verdict"], "BLOCKED_AUDIT", bad)
            self.assertEqual(r["gate"], P.BLOCKED, bad)
        self.assertEqual(P.device_audit([{"id": "X"}, "s", 7])["verdict"], "BLOCKED_AUDIT")

    def test_case_insensitive_unknown_and_wram_blocked(self):
        self.assertEqual(P.device_audit([dev("X", "DEVICE_STORE", "wram", "unknown")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", "device_store", "WRAM", "Not_Main_Ram")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", " Device_Store ", "VRAM", "NOT_MAIN_RAM")])["verdict"], "DRAINED_NO_UNKNOWN")

    def test_unlisted_role_verdict_pair_blocked(self):
        self.assertEqual(P.device_audit([dev("X", "device_store", "VRAM", "CPU_ORIGINATED")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", "cpu_store", "WRAM", "NOT_MAIN_RAM")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", "unknown_store", "VRAM", "NOT_MAIN_RAM")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", "mystery", "VRAM", "NOT_MAIN_RAM")])["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(P.device_audit([dev("X", "device_store", "VRAM", "MYSTERY")])["verdict"], "BLOCKED_AUDIT")

    def test_bad_ids_deduplicated_in_order(self):
        rows = [dev("A", "unknown_store", "ANY", "UNKNOWN"), dev("A", "unknown_store", "ANY", "UNKNOWN"),
                dev("B", "device_store", "WRAM", "NOT_MAIN_RAM")]
        self.assertEqual(P.device_audit(rows)["bad_ids"], ["A", "B"])

    def test_unknown_target_blocked(self):
        for tgt in ("UNKNOWN", "unknown", " Unknown ", "MYSTERY", "FOO"):
            with self.subTest(target=tgt):
                r = P.device_audit([dev("X", "device_store", tgt, "NOT_MAIN_RAM")])
                self.assertEqual(r["verdict"], "BLOCKED_AUDIT", tgt)
                self.assertEqual(r["gate"], P.BLOCKED, tgt)
                self.assertEqual(r["execution"], P.BLOCKED, tgt)
                self.assertEqual(r["bad_ids"], ["X"], tgt)
                self.assertNotIn(r["verdict"], ("PASS", "ALLOWED", "VERIFIED"), tgt)

    def test_missing_empty_target_blocked(self):
        rows_missing = [{"id": "X", "role": "device_store", "verdict": "NOT_MAIN_RAM"}]
        self.assertEqual(P.device_audit(rows_missing)["verdict"], "BLOCKED_AUDIT")
        for tgt in ("", "   "):
            with self.subTest(target=tgt):
                r = P.device_audit([dev("X", "device_store", tgt, "NOT_MAIN_RAM")])
                self.assertEqual(r["verdict"], "BLOCKED_AUDIT", tgt)
                self.assertEqual(r["gate"], P.BLOCKED, tgt)

    def test_wrongtype_target_role_verdict_blocked(self):
        for tgt in (None, 123, 1.5, True, ["VRAM"], {"t": "VRAM"}):
            with self.subTest(target=tgt):
                row = {"id": "X", "file": "x.cpp", "line": "1", "role": "device_store",
                       "target": tgt, "verdict": "NOT_MAIN_RAM"}
                r = P.device_audit([row])
                self.assertEqual(r["verdict"], "BLOCKED_AUDIT", tgt)
                self.assertEqual(r["gate"], P.BLOCKED, tgt)
        for role, verdict in ((123, "NOT_MAIN_RAM"), ("device_store", 123), (None, None)):
            with self.subTest(role=role, verdict=verdict):
                row = {"id": "X", "file": "x.cpp", "line": "1", "role": role,
                       "target": "VRAM", "verdict": verdict}
                self.assertEqual(P.device_audit([row])["verdict"], "BLOCKED_AUDIT")

    def test_missing_empty_wrongtype_id_blocked(self):
        base = {"role": "device_store", "target": "VRAM", "verdict": "NOT_MAIN_RAM"}
        variants = [dict(base), dict(base, id=None), dict(base, id=""),
                    dict(base, id="   "), dict(base, id=123), dict(base, id=["X"])]
        for i, row in enumerate(variants):
            with self.subTest(row=row):
                r = P.device_audit([row])
                self.assertEqual(r["verdict"], "BLOCKED_AUDIT", row)
                self.assertEqual(r["gate"], P.BLOCKED, row)
                self.assertEqual(r["execution"], P.BLOCKED, row)
                self.assertTrue(r["bad_ids"], row)

    def test_duplicate_ids_blocked_even_when_rows_look_clean(self):
        rows = [dev("DUP", "device_store", "VRAM", "NOT_MAIN_RAM"),
                dev("DUP", "cpu_store", "WRAM", "CPU_ORIGINATED")]
        r = P.device_audit(rows)
        self.assertEqual(r["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)
        self.assertIn("DUP", r["bad_ids"])
        self.assertNotIn(r["verdict"], ("PASS", "ALLOWED", "VERIFIED"))

    def test_not_main_ram_to_wram_is_self_contradictory(self):
        r = P.device_audit([dev("X", "device_store", "WRAM", "NOT_MAIN_RAM")])
        self.assertEqual(r["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(r["bad_ids"], ["X"])

    def test_target_allowlist_covers_fixture(self):
        text = (ROOT / "tests" / "fixtures" / "367_device_write_sites.tsv").read_text(encoding="utf-8")
        targets = set()
        for raw in text.splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            targets.add(raw.split("\t")[4].strip().upper())
        self.assertTrue(targets)
        self.assertEqual(targets - set(P.KNOWN_TARGETS), set())
        self.assertNotIn("UNKNOWN", set(P.KNOWN_TARGETS))


class MutationExtraTest(unittest.TestCase):
    def test_m16_drop_forbidden_mmio_would_permit(self):
        mutant_allows = all(0 <= a <= 0xFFFF for a in P.destination_set("TII", 0x1804, 1))
        self.assertTrue(mutant_allows)
        r = pf(dst=0x1804, win=(0, 0x10000), banks={0xF8, 0, 1, 2, 3, 4, 5, 6})
        self.assertEqual(r["verdict"], REFUSED)
        self.assertNotEqual(r["verdict"], "PERMITTED_STATIC_ONLY")

    def test_m17_bool_length_as_int_would_permit(self):
        mutant_length = int(True)
        self.assertEqual(mutant_length, 1)
        self.assertEqual(pf(length=True)["verdict"], REFUSED)
        self.assertEqual(pf(length=True)["reason"], "bad_length")

    def test_m18_start_byte_only_check_would_miss_wrap(self):
        mutant_allows = 0xFFFE in P.destination_set("TII", 0x2100, 1)
        self.assertFalse(mutant_allows)
        self.assertEqual(P.destination_set("TII", 0xFFFE, 4), {0xFFFE, 0xFFFF, 0x0000, 0x0001})
        r = pf(dst=0xFFFE, length=4)
        self.assertEqual(r["verdict"], REFUSED)

    def test_m19_case_sensitive_build_check_bypassed(self):
        naive = any(k in ("gg_testing", "libretro") for k in {"LibRetro": True})
        self.assertFalse(naive)
        self.assertEqual(pf(build={"LibRetro": True})["verdict"], REFUSED)

    def test_m20_case_sensitive_audit_bypassed(self):
        row = dev("X", "DEVICE_STORE", "wram", "unknown")
        naive_blocked = row.get("verdict") == "UNKNOWN" or (row.get("role") in P.DEVICE_WRITER_ROLES and row.get("target") == "WRAM")
        self.assertFalse(naive_blocked)
        self.assertEqual(P.device_audit([row])["verdict"], "BLOCKED_AUDIT")

    def test_m21_unchecked_device_pair_would_permit(self):
        row = dev("X", "device_store", "VRAM", "CPU_ORIGINATED")
        naive_blocked = (row["target"] == "WRAM" and row["role"] in P.DEVICE_WRITER_ROLES) or row["verdict"] == "UNKNOWN"
        self.assertFalse(naive_blocked)
        self.assertEqual(P.device_audit([row])["verdict"], "BLOCKED_AUDIT")

    def test_m22_src_forbidden_mmio_dropped_would_permit(self):
        self.assertIn(0x1802, P.source_set("TII", 0x1802, 1))
        self.assertEqual(pf(src=0x1802)["verdict"], REFUSED)

    def test_m23_tin_store_count_is_one(self):
        r = pf(op="TIN", length=8)
        self.assertEqual(r["verdict"], PERMIT)
        self.assertEqual(r["stores_in_one_instruction"], 1)
        self.assertEqual(r["distinct_destinations"], 1)

    def test_m24_no_pass_allowed_or_postdetect_verdict_anywhere(self):
        matrix = [pf(), pf(op="TXX"), pf(dst=0x1804, win=(0, 0x10000), banks={0xF8, 0, 1, 2, 3, 4, 5, 6}),
                  pf(dst=0x3FF8), pf(length=True), pf(build={"LibRetro": True}), pf(mpr=[1]),
                  pf(src=0x1802), P.device_audit([dev("U", "unknown_store", "ANY", "UNKNOWN")]),
                  P.device_audit([dev("D", "device_store", "VRAM", "NOT_MAIN_RAM")])]
        for r in matrix:
            self.assertNotIn(r["verdict"], ("PASS", "ALLOWED", "DETECTED_AFTER_SIDE_EFFECT", "VERIFIED"))
            self.assertEqual(r["gate"], P.BLOCKED)
            self.assertEqual(r["execution"], P.BLOCKED)
            self.assertEqual(r["jsr_count"], 0)

    def test_m25_empty_audit_is_blocked_not_vacuously_clean(self):
        for empty in ([], ()):
            r = P.device_audit(empty)
            with self.subTest(rows=empty):
                self.assertEqual(r["verdict"], "BLOCKED_AUDIT")
                self.assertEqual(r["gate"], P.BLOCKED)
                self.assertTrue(r["bad_ids"])

    def test_m26_forged_clean_rows_never_prove_safety(self):
        forged = [dev("F-%d" % i, "device_store", "VRAM", "NOT_MAIN_RAM") for i in range(50)]
        forged += [dev("C-%d" % i, "cpu_store", "WRAM", "CPU_ORIGINATED") for i in range(50)]
        r = P.device_audit(forged)
        self.assertEqual(r["verdict"], "DRAINED_NO_UNKNOWN")
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)
        self.assertEqual(r["jsr_count"], 0)
        self.assertNotIn(r["verdict"], ("PASS", "ALLOWED", "VERIFIED"))

    def test_m27_audit_result_shape_stable(self):
        for rows in ([dev("D", "device_store", "VRAM", "NOT_MAIN_RAM")], [], None, "x",
                     [dev("U", "unknown_store", "ANY", "UNKNOWN")]):
            r = P.device_audit(rows)
            with self.subTest(rows=rows):
                self.assertEqual(set(r), {"verdict", "gate", "execution", "jsr_count", "bad_ids"})
                self.assertEqual(r["gate"], P.BLOCKED)
                self.assertEqual(r["execution"], P.BLOCKED)
                self.assertEqual(r["jsr_count"], 0)


class SourceBankTest(unittest.TestCase):
    """MEDIUM-1: every source-set address resolves through MPR (Issue #739)."""

    def test_red_hw_source_refused(self):
        r = P.preflight("TII", 0xE000, 0x2100, 1, [0, 0xF8, 0, 0, 0, 0, 0, 0xFF],
                        (0x2100, 0x2200), {0xF8})
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "src_bank_not_allowed")
        self.assertEqual(r["first_refused"], 0xE000)
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)

    def test_hw_source_refused_all_ops(self):
        mpr = [0, 0xF8, 0, 0, 0, 0, 0, 0xFF]
        for op in P.OPS:
            with self.subTest(op=op):
                r = P.preflight(op, 0xE000, 0x2100, 1, list(mpr), (0x2100, 0x2200), {0xF8})
                self.assertEqual(r["verdict"], REFUSED, op)
                self.assertEqual(r["reason"], "src_bank_not_allowed", op)
                self.assertEqual(r["gate"], P.BLOCKED, op)
                self.assertEqual(r["execution"], P.BLOCKED, op)

    def test_unclassified_source_bank_refused_all_ops(self):
        # Page 0 maps to $F8, outside the {0x00, 0x01} allowlist: a
        # non-hardware but unclassified device-source read. Side effects of
        # such reads cannot be statically proven, so deny-by-default refuses.
        for op in P.OPS:
            with self.subTest(op=op):
                r = pf(op=op, src=0x1000, length=1)
                self.assertEqual(r["verdict"], REFUSED, op)
                self.assertEqual(r["reason"], "src_bank_not_allowed", op)
                self.assertEqual(r["first_refused"], 0x1000, op)

    def test_source_boundary_crossing_refused(self):
        # TII src 0x1FFC len 8 straddles page 0 ($F8) / page 1 ($00).
        r = pf(op="TII", src=0x1FFC, dst=0x2110, length=8)
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "src_bank_not_allowed")
        # TDD reading down across the same boundary.
        r = pf(op="TDD", src=0x2003, dst=0x2110, length=8)
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "src_bank_not_allowed")

    def test_source_wrap_refused(self):
        mpr = [0, 0xF8, 0, 0, 0, 0, 0, 0xFF]
        win = (0x2100, 0x2200)
        # TII reading up across 0xFFFF wraps to 0x0000; 0xFFFF is page-7 $FF.
        # dst=0x2110 keeps TDD-style descending destinations inside the window.
        r = P.preflight("TII", 0xFFFF, 0x2110, 2, list(mpr), win, {0xF8})
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "src_bank_not_allowed")
        self.assertIn(r["first_refused"], {0xFFFF, 0x0000})
        # TDD reading down across 0x0000 wraps to 0xFFFF (page-7 $FF).
        r = P.preflight("TDD", 0x0001, 0x2110, 4, list(mpr), win, {0xF8})
        self.assertEqual(r["verdict"], REFUSED)
        self.assertEqual(r["reason"], "src_bank_not_allowed")
        # TIN/TIA/TAI wrap the same way through their own source sets.
        self.assertEqual(
            P.preflight("TIN", 0xFFFF, 0x2110, 2, list(mpr), win, {0xF8})["verdict"], REFUSED)
        self.assertEqual(
            P.preflight("TIA", 0xFFFF, 0x2110, 2, list(mpr), win, {0xF8})["verdict"], REFUSED)
        self.assertEqual(
            P.preflight("TAI", 0xFFFF, 0x2110, 4, list(mpr), win, {0xF8})["verdict"], REFUSED)

    def test_in_allowlist_source_still_permits_all_ops(self):
        for op in P.OPS:
            with self.subTest(op=op):
                r = pf(op=op, src=0x2010, dst=0x2110, length=8)
                self.assertEqual(r["verdict"], PERMIT, op)
                self.assertEqual(r["gate"], P.BLOCKED, op)
                self.assertEqual(r["execution"], P.BLOCKED, op)

    def test_same_value_and_temp_restore_still_ban_hw_source(self):
        mpr = [0, 0xF8, 0, 0, 0, 0, 0, 0xFF]
        self.assertEqual(
            P.preflight("TII", 0xE000, 0x2100, 1, list(mpr), (0x2100, 0x2200),
                        {0xF8}, same_value=True)["reason"], "same_value_write")
        self.assertEqual(
            P.preflight("TII", 0xE000, 0x2100, 1, list(mpr), (0x2100, 0x2200),
                        {0xF8}, temp_restore=True)["reason"], "temp_write_restore")


class AmbiguousTargetTest(unittest.TestCase):
    """MEDIUM-2: ANY_MAPPED may include WRAM, so it never proves NOT_MAIN_RAM."""

    def test_red_device_store_any_mapped_blocked(self):
        r = P.device_audit([dev("DEV-X", "device_store", "ANY_MAPPED", "NOT_MAIN_RAM")])
        self.assertEqual(r["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)
        self.assertEqual(r["bad_ids"], ["DEV-X"])
        self.assertNotIn(r["verdict"], ("PASS", "ALLOWED", "VERIFIED"))

    def test_device_store_any_mapped_case_insensitive_blocked(self):
        r = P.device_audit([dev("X", " Device_Store ", "any_mapped", "not_main_ram")])
        self.assertEqual(r["verdict"], "BLOCKED_AUDIT")
        self.assertEqual(r["bad_ids"], ["X"])

    def test_cpu_block_store_any_mapped_contract_kept(self):
        rows = [dev("BLK-0%d" % i, "cpu_block_store", "ANY_MAPPED", "PER_BYTE_STEP")
                for i in range(1, 6)]
        r = P.device_audit(rows)
        self.assertEqual(r["verdict"], "DRAINED_NO_UNKNOWN")
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)

    def test_per_role_targets(self):
        # Concrete device targets still drain.
        self.assertEqual(
            P.device_audit([dev("D", "device_store", "VRAM", "NOT_MAIN_RAM")])["verdict"],
            "DRAINED_NO_UNKNOWN")
        # CPU/reset/host contracts on concrete targets (incl. WRAM) still drain.
        self.assertEqual(
            P.device_audit([dev("C", "cpu_store", "WRAM", "CPU_ORIGINATED")])["bad_ids"], [])
        self.assertEqual(
            P.device_audit([dev("R", "reset_store", "WRAM", "RESET_ONLY")])["verdict"],
            "DRAINED_NO_UNKNOWN")
        self.assertEqual(
            P.device_audit([dev("H", "host_store", "WRAM", "HOST_ONLY")])["verdict"],
            "DRAINED_NO_UNKNOWN")
        # cpu_block_store is only meaningful for the ambiguous whole-map target.
        self.assertEqual(
            P.device_audit([dev("B", "cpu_block_store", "VRAM", "PER_BYTE_STEP")])["verdict"],
            "BLOCKED_AUDIT")

    def test_fixture_still_drains(self):
        text = (ROOT / "tests" / "fixtures" / "367_device_write_sites.tsv").read_text(encoding="utf-8")
        rows = []
        for raw in text.splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            c = raw.split("\t")
            rows.append({"id": c[0], "role": c[3], "target": c[4], "verdict": c[5]})
        r = P.device_audit(rows)
        self.assertEqual(r["verdict"], "DRAINED_NO_UNKNOWN")
        self.assertEqual(r["gate"], P.BLOCKED)
        self.assertEqual(r["execution"], P.BLOCKED)


if __name__ == "__main__":
    unittest.main()
