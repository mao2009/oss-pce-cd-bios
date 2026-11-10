#!/usr/bin/env python3
"""Issue #365: host-side judgment of the recorded ST0/ST1/ST2 instruction-boundary evidence.

The fixture tests/fixtures/365-st-boundary-evidence.json holds the rows printed by the existing
synthetic-ROM probe (tests/integration/geargrafx_st_boundary.py, PR #383) on the pinned core.
This test only re-judges those recorded rows with the probe's own classifiers. It never runs a core,
never runs a BIOS, and never fills a value that the fixture does not contain.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

import geargrafx_st_boundary as st  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "365-st-boundary-evidence.json"
EXPECTED_CASES = ("st0-entry", "st0-unguarded", "st1-unknown", "st1-unguarded", "st2-unknown", "st2-unguarded")
ARMED = ("st0-entry", "st1-unknown", "st2-unknown")


def unguarded_of(mode):
    return mode.split("-")[0] + "-unguarded"


class StBoundaryRecordTests(unittest.TestCase):
    def setUp(self):
        with FIXTURE.open(encoding="utf-8") as handle:
            self.record = json.load(handle)
        self.rows = {case["case"]: case["row"] for case in self.record["cases"]}

    def test_provenance_is_real_core_on_synthetic_bios(self):
        self.assertEqual(self.record["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(self.record["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(self.record["bios"], "SYNTHETIC")
        self.assertEqual(tuple(sorted(self.rows)), tuple(sorted(EXPECTED_CASES)))

    def test_armed_cases_refused_before_side_effect(self):
        for mode in ARMED:
            with self.subTest(mode=mode):
                verdict = st.check_refused_historical(copy.deepcopy(self.rows[mode]), mode)
                self.assertEqual(verdict, st.REFUSED_VERDICT)

    def test_disabled_monitor_cases_land_side_effect(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                verdict = st.check_unguarded_historical(
                    copy.deepcopy(self.rows[unguarded_of(mode)]), unguarded_of(mode))
                self.assertEqual(verdict, st.LANDED_VERDICT)

    def test_recorded_mutations_are_rejected(self):
        for mode in ARMED:
            with self.subTest(mode=mode):
                st.mutation_test_armed(self.rows[mode], self.rows[unguarded_of(mode)], mode, historical=True)

    def test_removed_opcode_check_proxy_is_rejected(self):
        # Opcode check removed while the boundary stop stays: the ST runs, so the armed row must not verify.
        for mode in ARMED:
            with self.subTest(mode=mode):
                broken = copy.deepcopy(self.rows[unguarded_of(mode)])
                broken.update({"mode": mode, "armed": True, "boundary_check": True})
                with self.assertRaises(ValueError):
                    st.check_refused(broken, mode)

    def test_unguarded_row_cannot_pass_as_refused(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                with self.assertRaises(ValueError):
                    st.check_refused(copy.deepcopy(self.rows[unguarded_of(mode)]), mode)

    def test_mpr0_is_never_ff_in_recorded_rows(self):
        for name, row in self.rows.items():
            with self.subTest(case=name):
                self.assertEqual(row["before"]["mpr"][0], st.MPR0_NON_FF)
                self.assertEqual(row["after"]["mpr"][0], st.MPR0_NON_FF)


class StEntryCoverageTest(unittest.TestCase):
    """Entry-PC coverage for all three ST opcodes (Issue #365).

    Host-only static checks: the driver must define an armed entry refusal for
    ST0/ST1/ST2 at the guest entry PC with 0 executed instructions, and each
    entry must have a no-entry-check RED mutation whose normalized mode is the
    entry itself. The recorded fixture above stays untouched.
    """

    def test_armed_modes_include_entry_for_each_st(self):
        for kind in ("st0", "st1", "st2"):
            with self.subTest(kind=kind):
                self.assertIn(f"{kind}-entry", st.ARMED_MODES)

    def test_entry_modes_refuse_at_guest_entry_with_zero_executed(self):
        for kind in ("st0", "st1", "st2"):
            mode = f"{kind}-entry"
            with self.subTest(mode=mode):
                self.assertEqual(st.ST_EXPECTED_PC[mode], 0x3E00)
                self.assertEqual(st.ST_EXECUTED_BEFORE_REFUSAL[mode], 0)

    def test_each_entry_has_no_entry_check_mutation(self):
        for kind in ("st0", "st1", "st2"):
            mode = f"{kind}-entry"
            with self.subTest(mode=mode):
                self.assertIn(f"{kind}-entry-no-entry-check", st.MUTATIONS[mode])


class StHardwarePageClassifierTests(unittest.TestCase):
    """Dynamic hardware-page-entry classifier contract: fixed-JMP and BBR3 branch (Issue #365).

    Invariants are independent of the recorded fixture values: the classifier must accept any
    hardware-page stop PC and reject out-of-page stops, stepped fetches, SR hazards, SR-invariant
    false passes, MPR mismatches, and missing fields. Fixture rows are only the starting shape.
    """

    def setUp(self):
        self.row = None
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(record["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(record["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(record["bios"], "SYNTHETIC")
        rows = {case["case"]: case["row"] for case in record["cases"]}
        self.row = rows["st0-hwpage-fetch-blocked"]

    def test_recorded_fail_closed_passes(self):
        self.assertEqual(st.check_hardware_page_blocked(copy.deepcopy(self.row)),
                         st.HWPAGE_BLOCKED_VERDICT)

    def test_dynamic_stop_pc_inside_page_passes(self):
        for pc in (0x4000, 0x4002, 0x4321, 0x5FFF):
            with self.subTest(pc=f"{pc:#x}"):
                row = copy.deepcopy(self.row)
                row["stop_pc"] = pc
                row["after"]["pc"] = pc
                row["detected_pc"] = pc
                self.assertEqual(st.check_hardware_page_blocked(row), st.HWPAGE_BLOCKED_VERDICT)

    def test_dynamic_stop_pc_bbr3_exec_count_passes(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        rows = {case["case"]: case["row"] for case in record["cases"]}
        row = copy.deepcopy(rows["st0-bbr3-hwpage-taken"])
        row["stop_pc"] = 0x4005
        row["after"]["pc"] = 0x4005
        row["detected_pc"] = 0x4005
        self.assertEqual(
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken"),
            st.HWPAGE_BLOCKED_VERDICT)

    def test_stop_outside_hardware_page_rejected(self):
        for pc in (0x3FFF, 0x6000, 0x3E00):
            with self.subTest(pc=f"{pc:#x}"):
                row = copy.deepcopy(self.row)
                row["stop_pc"] = pc
                row["after"]["pc"] = pc
                row["detected_pc"] = pc
                with self.assertRaisesRegex(ValueError, "outside the hardware page"):
                    st.check_hardware_page_blocked(row)

    def test_stepped_fetch_rejected_even_inside_page(self):
        row = copy.deepcopy(self.row)
        row.update({"instructions_executed": 4, "fetch_open_stepped": True,
                    "status": "fetch_open_stepped", "stop_pc": 0x4002})
        with self.assertRaisesRegex(ValueError, "did not fail closed"):
            st.check_hardware_page_blocked(row)

    def test_sr_clear_hazard_rejected(self):
        row = copy.deepcopy(self.row)
        row["vdc_sr_after"] = 0x00
        with self.assertRaisesRegex(ValueError, "forbidden read landed"):
            st.check_hardware_page_blocked(row)

    def test_sr_invariant_alone_does_not_pass(self):
        row = copy.deepcopy(self.row)
        row.update({"vdc_sr_before": 0, "vdc_sr_after": 0, "hazard_landed": True})
        with self.assertRaisesRegex(ValueError, "fail-closed"):
            st.check_hardware_page_blocked(row)

    def test_mpr_mapping_mismatch_rejected(self):
        row = copy.deepcopy(self.row)
        row["after"]["mpr"][2] = 0x00
        with self.assertRaises(ValueError):
            st.check_hardware_page_blocked(row)

    def test_missing_fetch_evidence_rejected(self):
        row = copy.deepcopy(self.row)
        row.pop("fetch_failed")
        with self.assertRaisesRegex(ValueError, "non-bool"):
            st.check_hardware_page_blocked(row)

    def test_missing_hazard_flags_rejected(self):
        for key in ("hazard_landed", "fetch_open_stepped", "refused"):
            with self.subTest(key=key):
                row = copy.deepcopy(self.row)
                row.pop(key)
                with self.assertRaisesRegex(ValueError, "non-bool"):
                    st.check_hardware_page_blocked(row)

    def test_detected_pc_must_be_measured_stop(self):
        row = copy.deepcopy(self.row)
        row["detected_pc"] = row["stop_pc"] + 1
        with self.assertRaisesRegex(ValueError, "detected_pc"):
            st.check_hardware_page_blocked(row)
        row = copy.deepcopy(self.row)
        row.pop("detected_pc")
        with self.assertRaises(ValueError):
            st.check_hardware_page_blocked(row)

class StV2FixtureTests(unittest.TestCase):
    """Re-judgment of the v2 fixture: every recorded row drives its own live classifier."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_all_armed_modes_present(self):
        self.assertEqual(set(st.ARMED_MODES), {case for case in self.rows
                                               if case in st.ARMED_MODES})
        self.assertEqual(len(st.ARMED_MODES), 17)

    def test_armed_rows_pass_their_classifiers(self):
        for mode in st.ARMED_MODES:
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                if mode in st.HWPAGE_EXPECTED_EXECUTED:
                    self.assertEqual(st.check_hardware_page_blocked(row, mode),
                                     st.HWPAGE_BLOCKED_VERDICT)
                else:
                    self.assertEqual(st.check_refused(row, mode), st.REFUSED_VERDICT)

    def test_missing_exit_is_rejected(self):
        for bad in (None, False, "0", 2):
            with self.subTest(exit=bad):
                row = copy.deepcopy(self.rows["st1-unknown"])
                row["exit"] = bad
                with self.assertRaises(ValueError):
                    st.check_refused(row, "st1-unknown")
        row = copy.deepcopy(self.rows["st1-unknown"])
        row.pop("exit")
        with self.assertRaises(ValueError):
            st.check_refused(row, "st1-unknown")

    def test_hazard_rows_are_excluded_from_jsr_view(self):
        for case, row in self.rows.items():
            with self.subTest(case=case):
                hazard = row.get("hazard_landed") is True or row.get("fetch_open_stepped") is True
                observation = st.to_jsr_observation(copy.deepcopy(row))
                if hazard:
                    self.assertIsNone(observation, f"{case}: hazard row must be excluded")
                else:
                    self.assertIsNotNone(observation, f"{case}: non-hazard row needs an observation")

    def test_mutation_rows_observed_the_prohibited_effect(self):
        for mode, mutations in st.MUTATIONS.items():
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    verdict = st.check_mutation(copy.deepcopy(self.rows[mutation]), mutation)
                    self.assertIn(verdict, (st.LANDED_VERDICT, st.HAZARD_VERDICT))

    def test_armed_refusal_with_hazard_flag_rejected(self):
        row = copy.deepcopy(self.rows["st1-unknown"])
        row["hazard_landed"] = True
        with self.assertRaisesRegex(ValueError, "hazard_landed"):
            st.check_refused(row, "st1-unknown")

    def test_unrelated_mpr_change_rejected(self):
        row = copy.deepcopy(self.rows["st2-mpr2-unknown"])
        row["after"]["mpr"][5] = 0x77
        with self.assertRaisesRegex(ValueError, "unexpected MPR5"):
            st.check_refused(row, "st2-mpr2-unknown")

    def test_unobserved_irq_never_reports_observed(self):
        row = copy.deepcopy(self.rows["st0-entry"])
        row.pop("irr", None)
        row.pop("idr", None)
        self.assertEqual(st.to_jsr_observation(row)["irq"]["status"], "UNKNOWN")

    def test_missing_field_does_not_unconditionally_pass(self):
        row = copy.deepcopy(self.rows["st1-unknown"])
        row.pop("instructions_executed")
        with self.assertRaises(ValueError):
            st.check_refused(row, "st1-unknown")


class StBbr3MutationModeTests(unittest.TestCase):
    """BBR3 mutation harness: mode-correct judgment with reason-specific rejections (Issue #365).

    RED contract: killing these strains must fail loudly. The harness pre-verifies that the
    safe row passes under the named mode, and each tamper must fail for its own reason.
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        self.taken = copy.deepcopy(self.rows["st0-bbr3-hwpage-taken"])

    def test_1_taken_row_passes_with_its_mode(self):
        self.assertEqual(st.check_hardware_page_blocked(self.taken, "st0-bbr3-hwpage-taken"),
                         st.HWPAGE_BLOCKED_VERDICT)

    def test_2_taken_row_rejected_as_fixed_jmp_mode(self):
        with self.assertRaisesRegex(ValueError, "measured wrong mode"):
            st.check_hardware_page_blocked(self.taken, st.HWPAGE_MODE)

    def test_3_taken_stop_outside_page_rejected(self):
        row = copy.deepcopy(self.taken)
        row["stop_pc"] = 0x3FFF
        row["after"]["pc"] = 0x3FFF
        row["detected_pc"] = 0x3FFF
        with self.assertRaisesRegex(ValueError, "outside the hardware page"):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")

    def test_4_taken_executed_count_tamper_rejected(self):
        row = copy.deepcopy(self.taken)
        row["instructions_executed"] += 1
        with self.assertRaisesRegex(ValueError, "executed 5"):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")

    def test_5_taken_detected_pc_tamper_rejected(self):
        row = copy.deepcopy(self.taken)
        row["detected_pc"] += 1
        with self.assertRaisesRegex(ValueError, "detected_pc"):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")

    def test_6_taken_missing_fetch_evidence_rejected(self):
        row = copy.deepcopy(self.taken)
        row.pop("fetch_failed")
        with self.assertRaisesRegex(ValueError, "non-bool"):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")

    def test_7_mutation_harness_verifies_both_modes_and_rejects_cross_use(self):
        # The harness itself must pass for both hwpage modes with their own rows.
        st.mutation_test_hardware_page(copy.deepcopy(self.rows[st.HWPAGE_MODE]), st.HWPAGE_MODE)
        st.mutation_test_hardware_page(copy.deepcopy(self.taken), "st0-bbr3-hwpage-taken")
        # A checker that judges a safe row under the wrong mode cannot count as a kill.
        with self.assertRaises(ValueError):
            st.mutation_test_hardware_page(copy.deepcopy(self.taken), st.HWPAGE_MODE)

    def test_read_buffer_tamper_variants_rejected(self):
        # (0xFFFF, 0xFFFF) is the reset pair and must pass (see test_2 path), so it is kept out.
        for before, after in ((0xFFFF, 0x0000), (0x0000, 0xFFFF), (0x0000, 0x0000)):
            with self.subTest(before=hex(before), after=hex(after)):
                row = copy.deepcopy(self.taken)
                row["vdc_read_buffer_before"] = before
                row["vdc_read_buffer_after"] = after
                with self.assertRaisesRegex(ValueError, "read buffer"):
                    st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")

    def test_read_buffer_missing_or_wrong_type_rejected(self):
        row = copy.deepcopy(self.taken)
        row.pop("vdc_read_buffer_before")
        with self.assertRaises(ValueError):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")
        row = copy.deepcopy(self.taken)
        row["vdc_read_buffer_after"] = "ffff"
        with self.assertRaises(ValueError):
            st.check_hardware_page_blocked(row, "st0-bbr3-hwpage-taken")


class StHazardLatchContaminationTests(unittest.TestCase):
    """Hazard evidence: exactly one unsafe read per unsafe-read run (latch holds)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_unsafe_read_rows_have_exactly_one_mmio_read(self):
        for case, row in self.rows.items():
            if case.endswith("-unsafe-read"):
                with self.subTest(case=case):
                    self.assertEqual(row["mmio_read_attempts"], 1)

    def test_no_hazard_latch_rows_repeat_the_read(self):
        for case, row in self.rows.items():
            if case.endswith("-no-hazard-latch"):
                with self.subTest(case=case):
                    self.assertGreaterEqual(row["mmio_read_attempts"], 2)

    def test_armed_rows_perform_no_unsafe_read(self):
        for case, row in self.rows.items():
            if case in st.ARMED_MODES:
                with self.subTest(case=case):
                    self.assertEqual(row["mmio_read_attempts"], 0)


class StMmioAndPcIntegrityTests(unittest.TestCase):
    """MMIO-read counts and architectural-PC integrity (Issue #365 audit).

    The armed classifiers must require mmio_read_attempts == 0 on every live run and the
    architectural PC held across after/stop/refused/detected. Every listed tamper value must
    fail for its own reason, for each classifier family.
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_mmio_count_zero_on_armed_rows(self):
        for mode in st.ARMED_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(self.rows[mode]["mmio_read_attempts"], 0)

    def test_mmio_nonzero_rejected_by_both_families(self):
        for bad in (1, 4, -1):
            with self.subTest(value=bad):
                row = copy.deepcopy(self.rows["st1-unknown"])
                row["mmio_read_attempts"] = bad
                with self.assertRaisesRegex(ValueError, "mmio_read_attempts"):
                    st.check_refused(row, "st1-unknown")
                row = copy.deepcopy(self.rows[st.HWPAGE_MODE])
                row["mmio_read_attempts"] = bad
                with self.assertRaisesRegex(ValueError, "mmio_read_attempts"):
                    st.check_hardware_page_blocked(row)

    def test_mmio_type_or_missing_rejected(self):
        for bad in (None, True, "0"):
            with self.subTest(value=bad):
                row = copy.deepcopy(self.rows[st.HWPAGE_MODE])
                row["mmio_read_attempts"] = bad
                with self.assertRaises(ValueError):
                    st.check_hardware_page_blocked(row)
        row = copy.deepcopy(self.rows[st.HWPAGE_MODE])
        row.pop("mmio_read_attempts")
        with self.assertRaisesRegex(ValueError, "non-integer"):
            st.check_hardware_page_blocked(row)

    def test_st_pc_held_everywhere(self):
        for mode, skip in ((m, False) for m in st.ARMED_MODES if m not in st.HWPAGE_EXPECTED_EXECUTED):
            with self.subTest(mode=mode):
                row = self.rows[mode]
                self.assertEqual(row["after"]["pc"], row["stop_pc"])
                self.assertEqual(row["stop_pc"], row["refused_pc"])
                self.assertEqual(row["detected_pc"], row["refused_pc"])

    def test_pc_tamper_each_field_rejected(self):
        base = copy.deepcopy(self.rows["st1-unknown"])
        n = base["stop_pc"]
        cases = [
            ("after_pc_only", {"after": {"pc": (n + 1) & 0xFFFF}}, "not held"),
            ("stop_pc_only", {"stop_pc": (n + 1) & 0xFFFF}, "refused at"),
            ("refused_pc_only", {"refused_pc": (n + 1) & 0xFFFF}, "refused at"),
            ("detected_pc_only", {"detected_pc": (n + 1) & 0xFFFF}, "detected_pc"),
            ("after_pc_range", {"after": {"pc": 0x1FFFF}}, "after.pc"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                for key, value in overwrite.items():
                    if key == "after":
                        row["after"].update(value)
                    else:
                        row[key] = value
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_refused(row, "st1-unknown")

    def test_pc_missing_rejected(self):
        for section in ("after", "before"):
            with self.subTest(section=section):
                row = copy.deepcopy(self.rows["st1-unknown"])
                row[section].pop("pc")
                with self.assertRaisesRegex(ValueError, f"{section}.pc"):
                    st.check_refused(row, "st1-unknown")


class StPeekFailEvidenceTests(unittest.TestCase):
    """Peek-fail-step: the step into MMIO must be proven by its own fetch evidence."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        self.row = copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-peek-fail-step"])

    def test_recorded_fail_open_passes(self):
        self.assertEqual(st.check_peek_fail_step(self.row, "st0-hwpage-fetch-blocked-peek-fail-step"),
                         st.HAZARD_VERDICT)

    def test_bbr3_recorded_fail_open_passes(self):
        row = copy.deepcopy(self.rows["st0-bbr3-hwpage-taken-peek-fail-step"])
        self.assertEqual(st.check_peek_fail_step(row, "st0-bbr3-hwpage-taken-peek-fail-step"),
                         st.HAZARD_VERDICT)

    def test_each_fetch_field_tamper_rejected(self):
        tampers = [
            ("fetch_failed", {"fetch_failed": False}, "failed unsafe fetch"),
            ("fetch_unsafe_bank", {"fetch_unsafe_bank": False}, "failed unsafe fetch"),
            ("fetch_page", {"fetch_page": 1}, "fetch page/bank"),
            ("fetch_mpr", {"fetch_mpr": 0xF8}, "fetch page/bank"),
        ]
        for name, overwrite, reason in tampers:
            with self.subTest(field=name):
                row = copy.deepcopy(self.row)
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_peek_fail_step(row, "st0-hwpage-fetch-blocked-peek-fail-step")

    def test_stop_and_held_executed_rejected(self):
        row = copy.deepcopy(self.row)
        row["stop_pc"] = 0x3FFF
        with self.assertRaisesRegex(ValueError, "outside the hardware page"):
            st.check_peek_fail_step(row, "st0-hwpage-fetch-blocked-peek-fail-step")
        row = copy.deepcopy(self.row)
        row["after"]["pc"] = row["stop_pc"] + 1
        with self.assertRaisesRegex(ValueError, "not held"):
            st.check_peek_fail_step(row, "st0-hwpage-fetch-blocked-peek-fail-step")
        row = copy.deepcopy(self.row)
        row["instructions_executed"] -= 1
        with self.assertRaisesRegex(ValueError, "violation step"):
            st.check_peek_fail_step(row, "st0-hwpage-fetch-blocked-peek-fail-step")


class StIrqCoreStopAndDiffTests(unittest.TestCase):
    """IRQ core-stop distinction and net-diff integrity (Issue #365 audit).

    core_irq_stop is the pinned core debugger's own stop notification, never a generic
    IRQ-safety claim. ram_diff entries are validated directly (shape, range, uniqueness,
    stack cells, real changes, count consistency).
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_core_stop_flags_distinguished(self):
        self.assertIs(self.rows["irq2-handler-st0-survey"]["core_irq_stop"], False)
        self.assertIs(self.rows["irq2-core-stop-survey"]["core_irq_stop"], True)

    def test_core_stop_flips_and_types_rejected(self):
        cases = [
            ("core-stop true->false", "irq2-core-stop-survey", {"core_irq_stop": False}),
            ("handler false->true", "irq2-handler-st0-survey", {"core_irq_stop": True}),
            ("core-stop None", "irq2-core-stop-survey", {"core_irq_stop": None}),
            ("core-stop 0", "irq2-core-stop-survey", {"core_irq_stop": 0}),
            ("core-stop 1", "irq2-core-stop-survey", {"core_irq_stop": 1}),
            ("core-stop 'true'", "irq2-core-stop-survey", {"core_irq_stop": "true"}),
        ]
        for name, mode, overwrite in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[mode])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, "core_irq_stop"):
                    st.check_refused(row, mode)
        row = copy.deepcopy(self.rows["irq2-core-stop-survey"])
        row.pop("core_irq_stop")
        with self.assertRaisesRegex(ValueError, "core_irq_stop"):
            st.check_refused(row, "irq2-core-stop-survey")

    def test_ram_diff_tampers_rejected(self):
        base = copy.deepcopy(self.rows["irq2-handler-st0-survey"])
        stack_entry = base["ram_diff"][0]
        cases = [
            ("foreign_2200", lambda r: r["ram_diff"].append(
                {"address": 0x2200, "before": 0, "after": 1}), "outside the stack cells"),
            ("stack_21FC", lambda r: r["ram_diff"].append(
                {"address": 0x21FC, "before": 0, "after": 1}), "outside the stack cells"),
            ("count_mismatch", lambda r: r.__setitem__("ram_diff_count", 99), "ram_diff_count"),
            ("duplicate", lambda r: r["ram_diff"].append(dict(stack_entry)), "duplicate"),
            ("byte_range", lambda r: r["ram_diff"][0].__setitem__("after", 256), "ram_diff.after"),
            ("bool_as_int", lambda r: r["ram_diff"][0].__setitem__("before", True), "ram_diff.before"),
            ("string_as_int", lambda r: r["ram_diff"][0].__setitem__("address", "8702"),
             "ram_diff.address"),
            ("no_change", lambda r: r["ram_diff"][0].__setitem__(
                "before", r["ram_diff"][0]["after"]), "not a change"),
            ("truncated_true", lambda r: r.__setitem__("ram_diff_truncated", True),
             "ram_diff_truncated"),
            ("flag_false", lambda r: r.__setitem__("only_stack_net_diffs", False),
             "only_stack_net_diffs"),
            ("missing_diff", lambda r: r.pop("ram_diff"), "not a list"),
        ]
        for name, mutate, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                mutate(row)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_refused(row, "irq2-handler-st0-survey")


class StUnsafeReadFetchTests(unittest.TestCase):
    """Unsafe-read fetch location and MPR integrity (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        self.row = copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-unsafe-read"])

    def test_recorded_hazard_passes(self):
        self.assertEqual(st.check_unsafe_read_hazard(
            self.row, "st0-hwpage-fetch-blocked-unsafe-read"), st.HAZARD_VERDICT)

    def test_bbr3_recorded_hazard_passes(self):
        row = copy.deepcopy(self.rows["st0-bbr3-hwpage-taken-unsafe-read"])
        self.assertEqual(st.check_unsafe_read_hazard(
            row, "st0-bbr3-hwpage-taken-unsafe-read"), st.HAZARD_VERDICT)

    def test_fetch_tampers_rejected(self):
        cases = [
            ("page", {"fetch_page": 1}, "fetch page/bank"),
            ("mpr", {"fetch_mpr": 0xF8}, "fetch page/bank"),
            ("unsafe_bank", {"fetch_unsafe_bank": False}, "unsafe bank"),
            ("stop_outside", {"stop_pc": 0x3FFF, "after": {"pc": 0x3FFF}}, "outside"),
            ("pc_not_held", {"after": {"pc": 0x4001}}, "not held"),
            ("via_peek", {"fetch_via": "peek"}, "unsafe fetch"),
            ("hazard_off", {"hazard_landed": False}, "unsafe fetch"),
            ("mmio_zero", {"mmio_read_attempts": 0}, "mmio_read_attempts"),
            ("mmio_two", {"mmio_read_attempts": 2}, "mmio_read_attempts"),
            ("executed", {"instructions_executed": 4}, "after the hazard stop"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(self.row)
                for key, value in overwrite.items():
                    if key == "after":
                        row["after"].update(value)
                    else:
                        row[key] = value
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_unsafe_read_hazard(row, "st0-hwpage-fetch-blocked-unsafe-read")


class StIrqFrameIntegrityTests(unittest.TestCase):
    """IRQ stack-frame bytes must match the recorded net diffs (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.row = {case["case"]: case["row"] for case in record["cases"]}["irq2-handler-st0-survey"]

    def test_stack_layout_and_frame_pass(self):
        self.assertEqual(self.row["stack_cpu"], [0x21FF, 0x21FE, 0x21FD])
        self.assertEqual(self.row["stack_frame_layout"], [0x3E, 0x01, 0x00])
        self.assertEqual(st.check_refused(copy.deepcopy(self.row), "irq2-handler-st0-survey"),
                         st.REFUSED_VERDICT)

    def test_diff_after_mismatches_frame_rejected(self):
        row = copy.deepcopy(self.row)
        for entry in row["ram_diff"]:
            if entry["address"] == 0x21FE:
                entry["after"] = 0x02
        with self.assertRaisesRegex(ValueError, "does not match the captured"):
            st.check_refused(row, "irq2-handler-st0-survey")
        row = copy.deepcopy(self.row)
        for entry in row["ram_diff"]:
            if entry["address"] == 0x21FF:
                entry["after"] = 0x3D
        with self.assertRaisesRegex(ValueError, "does not match the captured"):
            st.check_refused(row, "irq2-handler-st0-survey")

    def test_frame_and_cpu_tampers_rejected(self):
        cases = [
            ("frame_only", {"stack_frame_layout": [0x3D, 0x01, 0x00]}, "return PC"),
            ("cpu_reorder", {"stack_cpu": [0x21FD, 0x21FE, 0x21FF]}, "stack_cpu"),
            ("cpu_outside", {"stack_cpu": [0x2200, 0x21FE, 0x21FD]}, "stack_cpu"),
            ("frame_short", {"stack_frame_layout": [0x3E, 0x01]}, "malformed"),
            ("frame_bool", {"stack_frame_layout": [True, 0x01, 0x00]}, "malformed"),
            ("frame_str", {"stack_frame_layout": ["3E", 0x01, 0x00]}, "malformed"),
            ("frame_range", {"stack_frame_layout": [256, 0x01, 0x00]}, "malformed"),
            ("cpu_missing", "stack_cpu", "stack_cpu"),
            ("cpu_type", {"stack_cpu": "21FF"}, "stack_cpu"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(self.row)
                if isinstance(overwrite, str):
                    row.pop(overwrite)
                else:
                    row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_refused(row, "irq2-handler-st0-survey")


class StHazardLatchResumeTests(unittest.TestCase):
    """Hazard runs must prove 3 refused resumes with flat state (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_unsafe_read_rows_prove_resume_refusal(self):
        for case, row in self.rows.items():
            if case.endswith("-unsafe-read"):
                with self.subTest(case=case):
                    self.assertEqual(row["resume_attempts"], 3)
                    self.assertEqual(row["resume_refused"], 3)
                    self.assertEqual(row["executed_at_refusal"], row["instructions_executed"])
                    self.assertEqual(row["executed_after_resume"], row["instructions_executed"])
                    self.assertIs(row["ram_unchanged_during_resume"], True)
                    self.assertIs(row["fetch_safe"], False)

    def test_resume_tampers_rejected(self):
        base = copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-unsafe-read"])
        cases = [
            ("attempts_zero", {"resume_attempts": 0}, "resume not fully refused"),
            ("attempts_two", {"resume_attempts": 2}, "resume not fully refused"),
            ("refused_zero", {"resume_refused": 0}, "resume not fully refused"),
            ("refused_two", {"resume_refused": 2}, "resume not fully refused"),
            ("exec_at", {"executed_at_refusal": 99}, "after the hazard stop"),
            ("exec_after", {"executed_after_resume": 99}, "after the hazard stop"),
            ("mmio_zero", {"mmio_read_attempts": 0}, "mmio_read_attempts"),
            ("mmio_two", {"mmio_read_attempts": 2}, "mmio_read_attempts"),
            ("fetch_safe", {"fetch_safe": True}, "fetch_safe"),
            ("ram_changed", {"ram_unchanged_during_resume": False}, "WRAM changed"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_unsafe_read_hazard(row, "st0-hwpage-fetch-blocked-unsafe-read")

    def test_no_latch_rows_have_flat_execution(self):
        for case, row in self.rows.items():
            if case.endswith("-no-hazard-latch"):
                with self.subTest(case=case):
                    self.assertEqual(row["resume_attempts"], 3)
                    self.assertEqual(row["resume_refused"], 3)
                    self.assertEqual(row["executed_at_refusal"], row["instructions_executed"])
                    self.assertGreaterEqual(row["mmio_read_attempts"], 2)

    def test_no_latch_resume_tampers_rejected(self):
        base = copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-no-hazard-latch"])
        cases = [
            ("attempts_zero", {"resume_attempts": 0}, "resume not fully refused"),
            ("refused_two", {"resume_refused": 2}, "resume not fully refused"),
            ("exec_after", {"executed_after_resume": 99}, "resume attempts"),
            ("attempts_type", {"resume_attempts": "3"}, "missing/non-integer"),
            ("attempts_missing", "resume_attempts", "missing/non-integer"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                if isinstance(overwrite, str):
                    row.pop(overwrite)
                else:
                    row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_hazard_not_latched(row, "st0-hwpage-fetch-blocked-no-hazard-latch")


class StExpectedPcTests(unittest.TestCase):
    """expected_st_pc is a plan expectation (not a measurement): ST stops must match it."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.row = {case["case"]: case["row"] for case in record["cases"]}["st1-unknown"]

    def test_expected_st_pc_tamper_rejected(self):
        for bad in (0x3E11, None, "3E10", True):
            with self.subTest(value=bad):
                row = copy.deepcopy(self.row)
                row["expected_st_pc"] = bad
                with self.assertRaisesRegex(ValueError, "expected_st_pc"):
                    st.check_refused(row, "st1-unknown")
        row = copy.deepcopy(self.row)
        row.pop("expected_st_pc")
        with self.assertRaisesRegex(ValueError, "expected_st_pc"):
            st.check_refused(row, "st1-unknown")


class StOpcodeModeConsistencyTests(unittest.TestCase):
    """Opcode identity comes from the mode, never from row data (Issue #365 MAJOR)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_opcode_triple_flip_rejected(self):
        cases = [
            ("st1-entry", "st0", 0x03, "st_mode"),
            ("st0-entry", "st2", 0x23, "st_mode"),
            ("st2-entry", "st1", 0x13, "st_mode"),
        ]
        for mode, other_kind, other_op, reason in cases:
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["st_mode"] = other_kind
                row["refused_opcode"] = other_op
                row["fetch_opcode"] = other_op
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_refused(row, mode)

    def test_single_field_flips_rejected_for_their_reasons(self):
        row = copy.deepcopy(self.rows["st1-entry"])
        row["st_mode"] = "st0"
        with self.assertRaisesRegex(ValueError, "st_mode"):
            st.check_refused(row, "st1-entry")
        row = copy.deepcopy(self.rows["st1-entry"])
        row["refused_opcode"] = 0x03
        with self.assertRaisesRegex(ValueError, "refused opcode"):
            st.check_refused(row, "st1-entry")
        row = copy.deepcopy(self.rows["st1-entry"])
        row["fetch_opcode"] = 0x03
        with self.assertRaisesRegex(ValueError, "fetched opcode"):
            st.check_refused(row, "st1-entry")

    def test_st_mode_missing_or_bogus_rejected(self):
        for bad in (None, "st9", 1):
            with self.subTest(value=bad):
                row = copy.deepcopy(self.rows["st1-entry"])
                row["st_mode"] = bad
                with self.assertRaisesRegex(ValueError, "st_mode"):
                    st.check_refused(row, "st1-entry")
        row = copy.deepcopy(self.rows["st1-entry"])
        row.pop("st_mode")
        with self.assertRaisesRegex(ValueError, "st_mode"):
            st.check_refused(row, "st1-entry")

    def test_irq_survey_st_mode_is_st0(self):
        row = copy.deepcopy(self.rows["irq2-handler-st0-survey"])
        row["st_mode"] = "st1"
        with self.assertRaisesRegex(ValueError, "st_mode"):
            st.check_refused(row, "irq2-handler-st0-survey")


class StIrqSpTests(unittest.TestCase):
    """IRQ stack-push evidence: SP transitions $FF -> $FC (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_both_surveys_transition_sp(self):
        for mode in ("irq2-handler-st0-survey", "irq2-core-stop-survey"):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                self.assertEqual(row["before"]["sp"], 0xFF)
                self.assertEqual(row["after"]["sp"], 0xFC)
                self.assertEqual(st.check_refused(row, mode), st.REFUSED_VERDICT)

    def test_sp_tampers_rejected(self):
        base = copy.deepcopy(self.rows["irq2-handler-st0-survey"])
        cases = [
            ("before_fe", {"before": {"sp": 0xFE}}, "SP before acceptance"),
            ("before_00", {"before": {"sp": 0x00}}, "SP before acceptance"),
            ("after_fd", {"after": {"sp": 0xFD}}, "SP after acceptance"),
            ("after_ff", {"after": {"sp": 0xFF}}, "SP after acceptance"),
            ("before_none", {"before": {"sp": None}}, "must be integers"),
            ("before_true", {"before": {"sp": True}}, "must be integers"),
            ("before_str", {"before": {"sp": "255"}}, "must be integers"),
            ("after_none", {"after": {"sp": None}}, "must be integers"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                for section, value in overwrite.items():
                    row[section].update(value)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_refused(row, "irq2-handler-st0-survey")
        for section in ("before", "after"):
            with self.subTest(name=f"{section}_missing"):
                row = copy.deepcopy(base)
                row[section].pop("sp")
                with self.assertRaisesRegex(ValueError, "must be integers"):
                    st.check_refused(row, "irq2-handler-st0-survey")


class StIrqObservationTruthfulnessTests(unittest.TestCase):
    """JSR IRQ honesty: OBSERVED only for an accepted survey IRQ (Issue #365 MAJOR)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_plain_st_row_is_unknown(self):
        row = copy.deepcopy(self.rows["st1-entry"])
        self.assertIn("irr", row)
        self.assertIn("idr", row)
        self.assertIs(row["irq_accepted"], False)
        self.assertEqual(st.to_jsr_observation(row)["irq"]["status"], "UNKNOWN")

    def test_bbr3_row_is_unknown(self):
        row = copy.deepcopy(self.rows["st0-bbr3-hwpage-taken"])
        self.assertEqual(st.to_jsr_observation(row)["irq"]["status"], "UNKNOWN")

    def test_survey_rows_are_observed(self):
        for mode in ("irq2-handler-st0-survey", "irq2-core-stop-survey"):
            with self.subTest(mode=mode):
                observation = st.to_jsr_observation(copy.deepcopy(self.rows[mode]))
                self.assertEqual(observation["irq"]["status"], "OBSERVED")
                self.assertEqual(observation["irq"]["mask"], self.rows[mode]["idr"] & 0xFF)
                self.assertEqual(observation["irq"]["pending"], self.rows[mode]["irr"] & 0xFF)

    def test_acceptance_missing_or_wrong_type_is_not_observed(self):
        for bad in (None, 0, 1, "true"):
            with self.subTest(value=bad):
                row = copy.deepcopy(self.rows["irq2-handler-st0-survey"])
                row["irq_accepted"] = bad
                self.assertNotEqual(st.to_jsr_observation(row)["irq"]["status"], "OBSERVED")
        row = copy.deepcopy(self.rows["irq2-handler-st0-survey"])
        row.pop("irq_accepted")
        self.assertNotEqual(st.to_jsr_observation(row)["irq"]["status"], "OBSERVED")

    def test_forged_acceptance_outside_survey_is_not_observed(self):
        row = copy.deepcopy(self.rows["st1-entry"])
        row["irq_accepted"] = True
        observation = st.to_jsr_observation(row)
        self.assertNotEqual(observation["irq"]["status"], "OBSERVED")


class StIrqNetDiffCompletenessTests(unittest.TestCase):
    """Empty/truncated net diffs must not pass (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        self.row = copy.deepcopy(self.rows["irq2-handler-st0-survey"])

    def test_empty_diff_rejected(self):
        row = copy.deepcopy(self.row)
        row["ram_diff"] = []
        row["ram_diff_count"] = 0
        with self.assertRaisesRegex(ValueError, "missing required diff"):
            st.check_refused(row, "irq2-handler-st0-survey")

    def test_single_diff_rejected(self):
        for doomed in (0x21FE, 0x21FF):
            with self.subTest(addr=hex(doomed)):
                row = copy.deepcopy(self.row)
                row["ram_diff"] = [e for e in row["ram_diff"] if e["address"] != doomed]
                row["ram_diff_count"] = len(row["ram_diff"])
                with self.assertRaisesRegex(ValueError, "missing required diff"):
                    st.check_refused(row, "irq2-handler-st0-survey")


class StNoHazardLatchFetchTests(unittest.TestCase):
    """Latch-removal evidence must anchor fetch location/MPR/stop/executed (Issue #365 audit)."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_recorded_variants_pass(self):
        self.assertEqual(st.check_hazard_not_latched(
            copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-no-hazard-latch"]),
            "st0-hwpage-fetch-blocked-no-hazard-latch"), st.HAZARD_VERDICT)
        self.assertEqual(st.check_hazard_not_latched(
            copy.deepcopy(self.rows["st0-bbr3-hwpage-taken-no-hazard-latch"]),
            "st0-bbr3-hwpage-taken-no-hazard-latch"), st.HAZARD_VERDICT)

    def test_fetch_tampers_rejected(self):
        base = copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-no-hazard-latch"])
        cases = [
            ("page", {"fetch_page": 1}, "fetch page/bank"),
            ("mpr", {"fetch_mpr": 0xF8}, "fetch page/bank"),
            ("unsafe", {"fetch_unsafe_bank": False}, "unsafe bank"),
            ("stop_outside", {"stop_pc": 0x3FFF}, "outside"),
            ("stop_not_held", {"after": {"pc": 0x4001}}, "not held"),
            ("mmio_two", {"mmio_read_attempts": 2}, "mmio_read_attempts"),
            ("mmio_three", {"mmio_read_attempts": 3}, "mmio_read_attempts"),
            ("mmio_five", {"mmio_read_attempts": 5}, "mmio_read_attempts"),
            ("executed", {"instructions_executed": 99}, "executed"),
        ]
        for name, overwrite, reason in cases:
            with self.subTest(name=name):
                row = copy.deepcopy(base)
                for key, value in overwrite.items():
                    if key == "after":
                        row["after"].update(value)
                    else:
                        row[key] = value
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_hazard_not_latched(row, "st0-hwpage-fetch-blocked-no-hazard-latch")


class StNoHazardLatchVdcTests(unittest.TestCase):
    """Latch-free hazard rows must carry the measured VDC signature (Issue #365 11th review).

    The fixed-JMP $4000 status read clears SR ($3F -> $00); the BBR3 $4002 data-port read
    leaves SR untouched ($3F -> $3F). The BBR3 invariance is an integrity anchor only, never
    device-side proof of the hazard. AR/R and the VDC read buffer must hold in both shapes.
    Each tampered pair must be rejected for its own reason.
    """

    FIXED = "st0-hwpage-fetch-blocked-no-hazard-latch"
    BBR3 = "st0-bbr3-hwpage-taken-no-hazard-latch"

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_normal_fixed_jmp_and_bbr3_latch_free_pass(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                self.assertEqual(st.check_hazard_not_latched(copy.deepcopy(self.rows[mode]), mode),
                                 st.HAZARD_VERDICT)

    def test_fixed_jmp_requires_sr_clear(self):
        self.assertEqual(self.rows[self.FIXED]["vdc_sr_before"], 0x3F)
        self.assertEqual(self.rows[self.FIXED]["vdc_sr_after"], 0x00)
        for name, overwrite in (("sr_after_invariant", {"vdc_sr_after": 0x3F}),
                                ("sr_before_zero", {"vdc_sr_before": 0x00})):
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[self.FIXED])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, "SR"):
                    st.check_hazard_not_latched(row, self.FIXED)

    def test_bbr3_requires_sr_invariance(self):
        self.assertEqual(self.rows[self.BBR3]["vdc_sr_before"], 0x3F)
        self.assertEqual(self.rows[self.BBR3]["vdc_sr_after"], 0x3F)
        for name, overwrite in (("sr_after_clear", {"vdc_sr_after": 0x00}),
                                ("sr_before_zero", {"vdc_sr_before": 0x00})):
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[self.BBR3])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, "SR"):
                    st.check_hazard_not_latched(row, self.BBR3)

    def test_ar_r_and_read_buffer_tampers_rejected(self):
        for mode in (self.FIXED, self.BBR3):
            for name, overwrite, reason in (
                    ("ar", {"vdc_ar_after": 0x06}, "VDC AR"),
                    ("r", {"vdc_r_after": 0x01}, "VDC R"),
                    ("read_buffer_before", {"vdc_read_buffer_before": 0x0000}, "read buffer"),
                    ("read_buffer_after", {"vdc_read_buffer_after": 0x0000}, "read buffer")):
                with self.subTest(mode=mode, name=name):
                    row = copy.deepcopy(self.rows[mode])
                    row.update(overwrite)
                    with self.assertRaisesRegex(ValueError, reason):
                        st.check_hazard_not_latched(row, mode)


class StNoHazardLatchContradictionTests(unittest.TestCase):
    """Latch-free hazard evidence must not mix contradictory verdicts (Issue #365 11th review).

    The probe's hazard contract is: status 'refused', refused/fetch_blocked/fetch_open_stepped/
    fetch_safe/fetch_failed all false, fetch_via 'read', hazard_landed true and flat resume
    state. Any other combination is rejected for its own reason, and the three-field composite
    tamper (status ok + fetch_safe + fetch_blocked) must never pass.
    """

    FIXED = "st0-hwpage-fetch-blocked-no-hazard-latch"
    BBR3 = "st0-bbr3-hwpage-taken-no-hazard-latch"

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_recorded_variants_pass(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                self.assertEqual(st.check_hazard_not_latched(copy.deepcopy(self.rows[mode]), mode),
                                 st.HAZARD_VERDICT)

    def test_single_field_contradictions_rejected(self):
        cases = [
            ("status", {"status": "ok"}, "status"),
            ("refused", {"refused": True}, "refused"),
            ("fetch_blocked", {"fetch_blocked": True}, "fetch_blocked"),
            ("fetch_open_stepped", {"fetch_open_stepped": True}, "fetch_open_stepped"),
            ("fetch_safe", {"fetch_safe": True}, "fetch_safe"),
            ("fetch_failed", {"fetch_failed": True}, "fetch_failed"),
            ("fetch_via", {"fetch_via": "peek"}, "hazard was not observed"),
            ("hazard_landed", {"hazard_landed": False}, "hazard was not observed"),
            ("ram_unchanged", {"ram_unchanged_during_resume": False}, "WRAM changed"),
            ("resume_attempts", {"resume_attempts": 2}, "resume not fully refused"),
            ("resume_refused", {"resume_refused": 2}, "resume not fully refused"),
            ("mmio_read_attempts", {"mmio_read_attempts": 1}, "mmio_read_attempts"),
            ("executed_at_refusal", {"executed_at_refusal": 99}, "resume attempts"),
            ("executed_after_resume", {"executed_after_resume": 99}, "resume attempts"),
            ("fetch_page", {"fetch_page": 1}, "fetch page/bank"),
            ("fetch_mpr", {"fetch_mpr": 0xF8}, "fetch page/bank"),
            ("fetch_unsafe_bank", {"fetch_unsafe_bank": False}, "unsafe bank"),
            ("stop_outside", {"stop_pc": 0x3FFF}, "outside"),
        ]
        for mode in (self.FIXED, self.BBR3):
            for name, overwrite, reason in cases:
                with self.subTest(mode=mode, name=name):
                    row = copy.deepcopy(self.rows[mode])
                    row.update(overwrite)
                    with self.assertRaisesRegex(ValueError, reason):
                        st.check_hazard_not_latched(row, mode)

    def test_detected_pc_tamper_rejected(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["detected_pc"] = (row["stop_pc"] + 1) & 0xFFFF
                with self.assertRaisesRegex(ValueError, "detected_pc"):
                    st.check_hazard_not_latched(row, mode)

    def test_executed_count_tamper_rejected(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["instructions_executed"] += 1
                with self.assertRaisesRegex(ValueError, "executed"):
                    st.check_hazard_not_latched(row, mode)

    def test_stop_not_held_rejected(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["after"]["pc"] = (row["stop_pc"] + 1) & 0xFFFF
                with self.assertRaisesRegex(ValueError, "not held"):
                    st.check_hazard_not_latched(row, mode)

    def test_composite_status_fetch_tamper_rejected(self):
        for mode in (self.FIXED, self.BBR3):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["status"] = "ok"
                row["fetch_safe"] = True
                row["fetch_blocked"] = True
                with self.assertRaisesRegex(
                        ValueError,
                        "status|refused|fetch_blocked|fetch_open_stepped|fetch_safe|fetch_failed"):
                    st.check_hazard_not_latched(row, mode)


class StIrqMutationObservationTests(unittest.TestCase):
    """IRQ OBSERVED requires verified vector/SP/frame/net-diff evidence, not just acceptance.

    The same evidence contract applies to armed surveys and to survey mutations that executed
    the handler ST. Unverified or contradictory mutation evidence can never be OBSERVED.
    """

    SURVEYS = ("irq2-handler-st0-survey", "irq2-core-stop-survey")
    MUTATIONS = ("irq2-handler-st0-survey-knownpc-only", "irq2-core-stop-survey-knownpc-only")

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_normal_surveys_observed(self):
        for mode in self.SURVEYS:
            with self.subTest(mode=mode):
                observation = st.to_jsr_observation(copy.deepcopy(self.rows[mode]))
                self.assertEqual(observation["irq"]["status"], "OBSERVED")

    def test_verified_survey_mutations_observed(self):
        for mode in self.MUTATIONS:
            with self.subTest(mode=mode):
                observation = st.to_jsr_observation(copy.deepcopy(self.rows[mode]))
                self.assertEqual(observation["irq"]["status"], "OBSERVED")

    def test_unverified_mutation_evidence_not_observed(self):
        cases = [
            ("vector_read", lambda r: r.__setitem__("vector_read", False)),
            ("observed_vector", lambda r: r.__setitem__("observed_vector", 0)),
            ("sp_before", lambda r: r["before"].__setitem__("sp", 0xFE)),
            ("sp_after", lambda r: r["after"].__setitem__("sp", 0xFD)),
            ("frame", lambda r: r.__setitem__("stack_frame_layout", [0x3D, 0x01, 0x00])),
            ("stack_cpu", lambda r: r.__setitem__("stack_cpu", [0x21FD, 0x21FE, 0x21FF])),
            ("ram_diff_missing", lambda r: r.pop("ram_diff")),
            ("ram_diff_empty", lambda r: (r.__setitem__("ram_diff", []),
                                          r.__setitem__("ram_diff_count", 0))),
            ("ram_diff_foreign", lambda r: r["ram_diff"].append(
                {"address": 0x2200, "before": 0, "after": 1})),
        ]
        for mode in self.MUTATIONS:
            for name, mutate in cases:
                with self.subTest(mode=mode, name=name):
                    row = copy.deepcopy(self.rows[mode])
                    mutate(row)
                    observation = st.to_jsr_observation(row)
                    self.assertIsNotNone(observation)
                    self.assertEqual(observation["irq"]["status"], "UNKNOWN")

    def test_unaccepted_irq_is_unknown(self):
        for mode in self.MUTATIONS:
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["irq_accepted"] = False
                self.assertEqual(st.to_jsr_observation(row)["irq"]["status"], "UNKNOWN")

    def test_plain_st_and_hazard_views(self):
        self.assertEqual(st.to_jsr_observation(
            copy.deepcopy(self.rows["st1-entry"]))["irq"]["status"], "UNKNOWN")
        self.assertIsNone(st.to_jsr_observation(
            copy.deepcopy(self.rows["st0-hwpage-fetch-blocked-no-hazard-latch"])))


class StCrossCuttingContradictionTests(unittest.TestCase):
    """Contradictory hazard/refusal flags must be rejected by every classifier family."""

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_unguarded_hazard_flags_rejected(self):
        for flag in ("hazard_landed", "fetch_open_stepped"):
            with self.subTest(flag=flag):
                row = copy.deepcopy(self.rows["st1-unknown-unguarded"])
                row[flag] = True
                with self.assertRaisesRegex(ValueError, flag):
                    st.check_unguarded(row, "st1-unknown-unguarded")

    def test_bypassed_mutation_hazard_flags_rejected(self):
        for flag in ("hazard_landed", "fetch_open_stepped"):
            with self.subTest(flag=flag):
                row = copy.deepcopy(self.rows["st0-entry-no-entry-check"])
                row[flag] = True
                with self.assertRaisesRegex(ValueError, flag):
                    st.check_bypassed_mutation(row, "st0-entry-no-entry-check")

    def test_bypassed_mutation_st_mode_mismatch_rejected(self):
        row = copy.deepcopy(self.rows["st0-entry-no-entry-check"])
        row["st_mode"] = "st1"
        with self.assertRaisesRegex(ValueError, "st_mode"):
            st.check_bypassed_mutation(row, "st0-entry-no-entry-check")

    def test_unsafe_read_contradictions_rejected(self):
        mode = "st0-hwpage-fetch-blocked-unsafe-read"
        for name, overwrite, reason in (
                ("status", {"status": "ok"}, "status"),
                ("refused", {"refused": True}, "refused"),
                ("fetch_blocked", {"fetch_blocked": True}, "fetch_blocked"),
                ("fetch_open_stepped", {"fetch_open_stepped": True}, "fetch_open_stepped"),
                ("fetch_failed", {"fetch_failed": True}, "fetch_failed")):
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[mode])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_unsafe_read_hazard(row, mode)

    def test_unsafe_read_bbr3_sr_tamper_rejected(self):
        mode = "st0-bbr3-hwpage-taken-unsafe-read"
        for name, overwrite in (("sr_after_clear", {"vdc_sr_after": 0x00}),
                                ("sr_before_zero", {"vdc_sr_before": 0x00})):
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[mode])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, "SR"):
                    st.check_unsafe_read_hazard(row, mode)

    def test_peek_fail_contradictions_rejected(self):
        mode = "st0-hwpage-fetch-blocked-peek-fail-step"
        for name, overwrite, reason in (
                ("hazard", {"hazard_landed": True}, "hazard_landed"),
                ("refused", {"refused": True}, "refused"),
                ("fetch_blocked", {"fetch_blocked": True}, "fetch_blocked"),
                ("fetch_safe", {"fetch_safe": True}, "fetch_safe")):
            with self.subTest(name=name):
                row = copy.deepcopy(self.rows[mode])
                row.update(overwrite)
                with self.assertRaisesRegex(ValueError, reason):
                    st.check_peek_fail_step(row, mode)

    def test_refused_read_buffer_tamper_rejected(self):
        row = copy.deepcopy(self.rows["st1-unknown"])
        row["vdc_read_buffer_after"] = 0x0000
        with self.assertRaisesRegex(ValueError, "read buffer"):
            st.check_refused(row, "st1-unknown")

    def test_unknown_mode_rejected(self):
        row = copy.deepcopy(self.rows["st1-unknown"])
        row["mode"] = "st1-not-a-mode"
        with self.assertRaisesRegex(ValueError, "unknown mode"):
            st.check_refused(row, "st1-not-a-mode")


class StHistoricalLiveSeparationTests(unittest.TestCase):
    """Live evidence must never be downgraded to historical by omitting a fetch field (Issue #365 R12).

    The public live classifiers validate the full live schema. Only the explicitly named
    historical entry points accept the pre-fetch-map fixture, and only for that fixture.
    Removing any mandatory live field is a hard failure with a reason-specific message.
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        old = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.old = {case["case"]: case["row"] for case in old["cases"]}

    def test_live_field_removed_is_rejected(self):
        targets = [
            ("st0-entry-no-entry-check", st.check_bypassed_mutation),
            ("st1-unknown-unguarded", st.check_unguarded),
        ]
        removals = [
            ("fetch_opcode", "missing/non-integer fetch_opcode"),
            ("fetch_page", "missing/non-integer fetch_page"),
            ("fetch_mpr", "missing/non-integer fetch_mpr"),
            ("fetch_via", "fetch_via must be peek/read"),
            ("fetch_safe", "missing/non-bool fetch_safe"),
            ("mmio_read_attempts", "missing/non-integer mmio_read_attempts"),
            ("resume_attempts", "missing/non-integer resume_attempts"),
        ]
        for mode, checker in targets:
            for field, reason in removals:
                with self.subTest(mode=mode, field=field):
                    row = copy.deepcopy(self.rows[mode])
                    row.pop(field)
                    with self.assertRaisesRegex(ValueError, reason):
                        checker(row, mode)

    def test_fetch_opcode_removed_is_rejected_through_mutation_dispatch(self):
        row = copy.deepcopy(self.rows["st0-entry-no-entry-check"])
        row.pop("fetch_opcode")
        with self.assertRaisesRegex(ValueError, "missing/non-integer fetch_opcode"):
            st.check_mutation(row, "st0-entry-no-entry-check")
        row = copy.deepcopy(self.rows["st1-unknown-unguarded"])
        row.pop("fetch_opcode")
        with self.assertRaisesRegex(ValueError, "missing/non-integer fetch_opcode"):
            st.check_mutation(row, "st1-unknown-unguarded")

    def test_live_mutations_pass(self):
        for mode in ("st0-entry-no-entry-check", "st2-mpr2-unknown-no-recheck",
                     "st1-unknown-unguarded"):
            with self.subTest(mode=mode):
                self.assertEqual(st.check_mutation(copy.deepcopy(self.rows[mode]), mode),
                                 st.LANDED_VERDICT)

    def test_historical_fixture_still_passes(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                verdict = st.check_unguarded_historical(
                    copy.deepcopy(self.old[unguarded_of(mode)]), unguarded_of(mode))
                self.assertEqual(verdict, st.LANDED_VERDICT)

    def test_historical_evidence_rejected_by_live_checker(self):
        for mode in ARMED:
            with self.subTest(mode=unguarded_of(mode)):
                with self.assertRaisesRegex(ValueError, "before.pc missing/non-16-bit"):
                    st.check_unguarded(copy.deepcopy(self.old[unguarded_of(mode)]),
                                       unguarded_of(mode))

    def test_historical_api_is_not_a_blanket_bypass(self):
        # The explicit historical entry points still enforce the non-fetch envelope.
        row = copy.deepcopy(self.old["st1-unguarded"])
        row["vdc_r_after"] = 0x00
        with self.assertRaisesRegex(ValueError, "did not land"):
            st.check_unguarded_historical(row, "st1-unguarded")
        row = copy.deepcopy(self.old["st1-unguarded"])
        row["status"] = "refused"
        with self.assertRaisesRegex(ValueError, "self-loop"):
            st.check_unguarded_historical(row, "st1-unguarded")
        row = copy.deepcopy(self.old["st1-unguarded"])
        row["armed"] = True
        with self.assertRaisesRegex(ValueError, "armed/refused"):
            st.check_unguarded_historical(row, "st1-unguarded")


class StMutationFetchIntegrityTests(unittest.TestCase):
    """Live mutation fetch integrity (Issue #365 R13 MEDIUM).

    A landed mutation runs on past the ST and halts in the self-loop, so the recorded fetch_*
    fields describe the LAST instruction (the halt branch at stop_pc), not the ST. The stop PC
    is anchored on the measured final architectural PC (``after.pc``) and the page MPR is
    anchored on the measured ``after`` map. The terminal instruction is the PC-unchanging halt
    branch, which can never be an ST step (an ST step advances the PC), so an ST opcode there is
    internally inconsistent. There is no oracle for the loop byte's value, so only byte-ness and
    the not-an-ST-shape property are asserted (never the plan's ``expected_st_pc`` as if it were
    measured, and never a comparison to ``refused_opcode``, which records an earlier event).
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.rows = {case["case"]: case["row"] for case in record["cases"]}

    def test_fetch_tampers_rejected(self):
        for mode, checker in (
                ("st1-unknown-unguarded", st.check_unguarded),
                ("st0-entry-no-entry-check", st.check_bypassed_mutation),
                ("st2-mpr2-unknown-no-recheck", st.check_bypassed_mutation)):
            for name, mutate, reason in (
                    ("st_opcode", lambda r: r.__setitem__("fetch_opcode", 0x03),
                     "terminal fetch claims an ST opcode"),
                    ("other_st", lambda r: r.__setitem__("fetch_opcode", 0x23),
                     "terminal fetch claims an ST opcode"),
                    ("mode_st", lambda r: r.__setitem__(
                        "fetch_opcode", st.ST_OPCODE[st._expected_kind(mode)]),
                     "terminal fetch claims an ST opcode"),
                    ("opcode_range", lambda r: r.__setitem__("fetch_opcode", 0x100),
                     "is not a byte"),
                    ("page", lambda r: r.__setitem__("fetch_page", r["fetch_page"] ^ 1),
                     "fetch page"),
                    ("mpr", lambda r: r.__setitem__("fetch_mpr", r["fetch_mpr"] ^ 0xF8),
                     "fetch MPR"),
                    ("stop", lambda r: r.__setitem__(
                        "stop_pc", (r["stop_pc"] + 0x20) & 0xFFFF),
                     "not the measured final PC")):
                with self.subTest(mode=mode, name=name):
                    row = copy.deepcopy(self.rows[mode])
                    mutate(row)
                    with self.assertRaisesRegex(ValueError, reason):
                        checker(row, mode)

    def test_fetch_tamper_rejected_through_dispatch(self):
        row = copy.deepcopy(self.rows["st1-unknown-unguarded"])
        row["fetch_page"] ^= 1
        with self.assertRaisesRegex(ValueError, "fetch page"):
            st.check_mutation(row, "st1-unknown-unguarded")
        row = copy.deepcopy(self.rows["st0-entry-no-entry-check"])
        row["fetch_opcode"] = 0x23
        with self.assertRaisesRegex(ValueError, "terminal fetch claims an ST opcode"):
            st.check_mutation(row, "st0-entry-no-entry-check")

    def test_recorded_live_mutations_pass(self):
        for mode in ("st1-unknown-unguarded", "st0-entry-no-entry-check",
                     "st2-mpr2-unknown-no-recheck", "st2-unknown-unguarded",
                     "st1-unknown-far-knownpc-only", "st0-jsr-rts-knownpc-only",
                     "st1-branch-unknown-resume-allowed"):
            with self.subTest(mode=mode):
                self.assertEqual(st.check_mutation(copy.deepcopy(self.rows[mode]), mode),
                                 st.LANDED_VERDICT)

    def test_non_st_byte_change_is_not_an_inconsistency(self):
        # No oracle distinguishes a non-ST loop byte from a tampered one; only byte-ness and the
        # not-an-ST shape are measured properties. This documents the contract boundary.
        for mode, checker in (
                ("st1-unknown-unguarded", st.check_unguarded),
                ("st0-entry-no-entry-check", st.check_bypassed_mutation)):
            with self.subTest(mode=mode):
                row = copy.deepcopy(self.rows[mode])
                row["fetch_opcode"] = 0x42
                self.assertEqual(checker(row, mode), st.LANDED_VERDICT)


class StV2PositiveControlTests(unittest.TestCase):
    """Positive-control evidence: completeness, live re-judgment, tamper detection (Issue #365).

    The v2 fixture holds the 40 measured REAL_CORE_EXECUTION rows (armed 17 + mutation 22 +
    pos-write 1). The positive control proves the rig can observe a landed allowlisted store:
    without it, a rig that refuses everything would still pass the armed checks (GREEN without
    a positive). Every tamper below must fail loudly (RED contract for the positive leg).
    """

    def setUp(self):
        record = json.loads((ROOT / "tests" / "fixtures" / "365-st-boundary-v2-evidence.json")
                            .read_text(encoding="utf-8"))
        self.record = record
        self.rows = {case["case"]: case["row"] for case in record["cases"]}
        self.verdicts = {case["case"]: case["verdict"] for case in record["cases"]}

    def test_fixture_covers_all_40_measured_cases(self):
        expected = (set(st.ARMED_MODES)
                    | {mutation for mode in st.ARMED_MODES for mutation in st.MUTATIONS[mode]}
                    | {"pos-write"})
        self.assertEqual(len(st.ARMED_MODES), 17)
        self.assertEqual(sum(len(st.MUTATIONS[mode]) for mode in st.ARMED_MODES), 22)
        self.assertEqual(set(self.rows), expected)
        self.assertEqual(len(self.rows), 40)

    def test_provenance_headers(self):
        self.assertEqual(self.record["observation_kind"], "REAL_CORE_EXECUTION")
        self.assertEqual(self.record["core_revision"], st.PINNED_CORE_REVISION)
        self.assertEqual(self.record["bios"], "SYNTHETIC")
        self.assertEqual(self.record["issue"], 365)

    def test_positive_control_passes_live_classifier(self):
        row = copy.deepcopy(self.rows["pos-write"])
        self.assertIsNone(st.check_pos(row, st.POS_CONTROL))
        self.assertEqual(self.verdicts["pos-write"], "allowlisted_ram_write_landed")
        self.assertEqual(self.rows["pos-write"]["mode"], "pos-write")

    def test_positive_control_has_jsr_observation(self):
        observation = st.to_jsr_observation(copy.deepcopy(self.rows["pos-write"]))
        self.assertIsNotNone(observation)
        self.assertEqual(observation["outcome"]["status"], "ok")
        self.assertEqual(observation["outcome"]["stop_pc"],
                         self.rows["pos-write"]["stop_pc"])

    def test_armed_row_cannot_pass_as_positive(self):
        with self.assertRaises(ValueError):
            st.check_pos(copy.deepcopy(self.rows["st0-entry"]), st.POS_CONTROL)

    def test_positive_control_tampers_rejected(self):
        tampers = [
            ("status", {"status": "refused"}),
            ("detected", {"detected": True}),
            ("wrong_target", {"guard_target": st.POS_CONTROL + 1}),
            ("already_set", {"guard_before": 0x5A}),
            ("not_landed", {"guard_after": 0x00}),
            ("wrong_value_landed", {"guard_after": 0x01}),
            ("too_many_executed", {"instructions_executed": 101}),
            ("unchanged", {"guard_after": 0x00, "guard_before": 0x00}),
            ("no_progress", {"instructions_executed": 0}),
            ("probe_failed", {"exit": 1}),
        ]
        for name, overwrite in tampers:
            with self.subTest(tamper=name):
                row = copy.deepcopy(self.rows["pos-write"])
                row.update(overwrite)
                with self.assertRaises(ValueError):
                    st.check_pos(row, st.POS_CONTROL)


if __name__ == "__main__":
    unittest.main()
