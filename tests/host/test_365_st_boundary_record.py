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
                verdict = st.check_unguarded(copy.deepcopy(self.rows[unguarded_of(mode)]), unguarded_of(mode))
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
            ("executed", {"instructions_executed": 4}, "hazard run executed"),
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


if __name__ == "__main__":
    unittest.main()
