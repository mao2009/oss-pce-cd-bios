"""Host tests for the minimal slot-$01 (CD_RESET, $E003) reference ABI record and its fail-closed judge (Issue #10).

Synthetic records only. No emulator, no reference BIOS, no JSR. The JSR gate is closed, so every
real-source record must be NOT_RUN + BLOCKED; the open-gate branch is exercised with a patched gate
to prove the classification logic (falsifiability), never as evidence.
"""
import unittest
from unittest import mock

from tools import cdreset_reference_abi as M
from tools.environment import DEPENDENCIES
from tools.reference_jsr_gate import GateRefused, require_allowed

OPEN_GATE = mock.patch.object(M, "require_allowed", return_value={"execution": "ALLOWED"})


def snapshot(**changes):
    base = {"A": 0x11, "X": 0x22, "Y": 0x33, "P": 0x04, "SP": 0xFD, "MPR": [0xF8, 0xF9, 0xFA, 0xFB,
            0xFC, 0xFD, 0x00, 0xFF], "irq_disable": True, "irq_mask": 0x00,
            "ram": {0x2000: 0x00, 0x2001: 0x5A}, "return_pc": 0x4003}
    base.update(changes)
    return base


def record(**changes):
    before = snapshot()
    caller = {key: before[key] for key in ("A", "X", "Y", "P", "MPR")}
    base = {"source": M.REAL_SOURCE, "core_revision": DEPENDENCIES["geargrafx"]["revision"],
            "rom_sha256": "00" * 32, "slot": "$01", "entry_pc": M.CANDIDATE_ENTRY, "media": "present",
            "caller": caller, "before": {k: before[k] for k in M.SNAPSHOT_KEYS["before"]},
            "after": dict(before), "returned": True, "polls": 10, "poll_budget": 100}
    base.update(changes)
    return base


class ContractTests(unittest.TestCase):
    def test_every_item_stays_unknown_or_hypothesis_and_nothing_is_verified(self):
        self.assertTrue(M.CONTRACT)
        for item, status in M.CONTRACT.items():
            with self.subTest(item=item):
                self.assertIn(status, ("UNKNOWN", "HYPOTHESIS", "BLOCKED"))
        self.assertNotIn("VERIFIED", set(M.CONTRACT.values()))

    def test_gate_is_closed_for_slot_01_and_the_reader_refuses(self):
        # The gate is read, never changed: the committed ledger must still refuse slot-01.
        with self.assertRaises(GateRefused):
            require_allowed(M.GATE_ID)

    def test_candidate_entry_is_the_slot_01_entry(self):
        self.assertEqual(M.CANDIDATE_ENTRY, 0xE003)


class GateAndSourceTests(unittest.TestCase):
    def test_gate_closed_reason_reports_refusal_and_none_when_open(self):
        self.assertTrue(M.gate_closed_reason().startswith("BLOCKED"))
        with mock.patch.object(M, "require_allowed", side_effect=M.GateRefused("refused-x")):
            self.assertEqual(M.gate_closed_reason(), "refused-x")
        with OPEN_GATE:
            self.assertIsNone(M.gate_closed_reason())

    def test_unknown_top_level_key_is_rejected(self):
        with self.assertRaises(ValueError):
            M.validate(record(extra_field=1))

    def test_classify_is_private_so_no_public_path_skips_the_gate(self):
        self.assertFalse(hasattr(M, "classify"))
        self.assertTrue(hasattr(M, "_classify"))

    def test_real_record_is_not_run_and_blocked_while_gate_is_closed(self):
        for media in M.MEDIA:
            with self.subTest(media=media):
                self.assertEqual(M.judge(record(media=media)), ("NOT_RUN", "BLOCKED", "gate_closed"))

    def test_synthetic_or_mock_source_is_skipped_never_passed(self):
        for source in ("mock", "hle", "static"):
            with self.subTest(source=source):
                self.assertEqual(M.judge(record(source=source))[1], "SKIP")

    def test_unpinned_core_and_unidentified_rom_block_even_with_open_gate(self):
        with OPEN_GATE:
            self.assertEqual(M.judge(record(core_revision="deadbeef"))[1:], ("BLOCKED", "core_unpinned"))
            self.assertEqual(M.judge(record(rom_sha256=""))[1:], ("BLOCKED", "rom_unidentified"))


class ClassificationTests(unittest.TestCase):
    def test_normal_and_no_media_returns_are_observed_never_pass(self):
        with OPEN_GATE:
            for media in M.MEDIA:
                with self.subTest(media=media):
                    self.assertEqual(M.judge(record(media=media)), ("EVALUATED", "OBSERVED", "returned"))

    def test_bounded_timeout_is_observed_as_timeout(self):
        with OPEN_GATE:
            self.assertEqual(M.judge(record(media="absent", returned=False, polls=100)),
                             ("EVALUATED", "OBSERVED", "timeout"))
            self.assertEqual(M.judge(record(returned=False, polls=40))[2], "not_returned")

    def test_budget_overrun_and_wrong_entry_fail(self):
        with OPEN_GATE:
            self.assertEqual(M.judge(record(polls=101))[1:], ("FAIL", "budget_overrun"))
            self.assertEqual(M.judge(record(polls=101, returned=False))[1:], ("FAIL", "budget_overrun"))
            self.assertEqual(M.judge(record(entry_pc=0xE000))[1:], ("FAIL", "wrong_entry"))

    def test_budget_boundary_is_inclusive(self):
        with OPEN_GATE:
            self.assertEqual(M.judge(record(polls=100))[1], "OBSERVED")
            self.assertEqual(M.judge(record(polls=101))[1], "FAIL")


class StateChangeTests(unittest.TestCase):
    def test_mpr_irq_and_ram_changes_are_reported_separately(self):
        changed = record()
        changed["after"]["MPR"] = list(changed["after"]["MPR"])
        changed["after"]["MPR"][7] = 0x80
        changed["after"]["irq_disable"] = False
        changed["after"]["ram"] = {**changed["after"]["ram"], 0x2001: 0x00}
        self.assertEqual(M.state_changes(changed), {"mpr": True, "irq": True, "ram": [0x2001]})

    def test_identical_before_and_after_reports_no_change(self):
        self.assertEqual(M.state_changes(record()), {"mpr": False, "irq": False, "ram": []})

    def test_ram_region_must_be_the_same_before_and_after(self):
        bad = record()
        bad["after"]["ram"] = {0x2000: 0x00}
        with self.assertRaises(ValueError):
            M.validate(bad)


class MalformedTests(unittest.TestCase):
    def test_missing_required_key_fails_closed(self):
        for key in M.REQUIRED:
            with self.subTest(missing=key), self.assertRaises(ValueError):
                broken = record()
                del broken[key]
                M.judge(broken)

    def test_wrong_types_and_ranges_fail_closed(self):
        cases = [dict(slot="$1E"), dict(media="maybe"), dict(returned=1), dict(polls=True),
                 dict(poll_budget=0), dict(rom_sha256="ABCD"), dict(entry_pc=0x1_0000)]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                M.judge(record(**bad))

    def test_snapshot_shape_is_enforced(self):
        cases = []
        short_mpr = record(); short_mpr["before"]["MPR"] = [0] * 7; cases.append(short_mpr)
        bool_reg = record(); bool_reg["caller"]["A"] = True; cases.append(bool_reg)
        extra_key = record(); extra_key["caller"]["Z"] = 0; cases.append(extra_key)
        bad_irq = record(); bad_irq["after"]["irq_disable"] = 1; cases.append(bad_irq)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                M.validate(bad)


class NoPassTests(unittest.TestCase):
    def test_no_verdict_or_execution_is_plain_pass_or_verified(self):
        seen = set()
        with OPEN_GATE:
            for changes in ({}, {"media": "absent"}, {"returned": False}, {"polls": 101},
                            {"source": "mock"}, {"entry_pc": 0xE000}):
                execution, verdict, reason = M.judge(record(**changes))
                seen.update((execution, verdict, reason))
        seen.update(M.judge(record())[:2])
        self.assertFalse(seen & {"PASS", "PASS_HYPOTHESIS", "VERIFIED", "ALLOWED"}, seen)


if __name__ == "__main__":
    unittest.main()
