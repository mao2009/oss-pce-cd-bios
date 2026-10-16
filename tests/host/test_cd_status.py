"""Synthetic-record tests for tools/cd_status.py (Issue #5 status classifier; no emulator, no BIOS data)."""
import unittest

from tools.build_cdread import ERROR_NAMES, HALT_CONFIRM_STEPS
from tools.cd_status import BLOCKED, FAIL, MEASURED, PASS, SKIP, CODES, classify_records, classify_run, overall


def run(cycle="cold", code=0, stage=9, transferred=2048, reason="cd_halt_ok", scsi=0, msg=0, **extra):
    block = bytes((code, stage)) + transferred.to_bytes(2, "little") + bytes((scsi, msg, 0, 0))
    row = {"cycle": cycle, "reason": reason, "result_hex": (block + bytes(26)).hex(), "halt_confirmed": True,
           "halt_confirmation_steps": HALT_CONFIRM_STEPS, "halt_confirmation_required": HALT_CONFIRM_STEPS}
    row.update(extra)
    return row


def records(*runs, media=True):
    return {"CAP": [{"cdrom_media": media, "instruction_step": True, "cdrom_hardware": True}],
            "RUN": list(runs), "PHASE": [], "HIT": [], "BLOCK": []}


class ClassifyRunTests(unittest.TestCase):
    def test_positive_success_single_and_three_sectors(self):
        self.assertEqual(classify_run(run(), 1).outcome, PASS)
        self.assertEqual(classify_run(run(transferred=6144), 3).outcome, PASS)

    def test_positive_expected_failure_codes_pass_only_when_they_match(self):
        verdict = classify_run(run(code=2, stage=2, transferred=0, reason="cd_halt_fail"), 1, expect=2)
        self.assertEqual((verdict.outcome, verdict.category, verdict.name, verdict.evidence),
                         (PASS, "TIMEOUT", "SELECT_TIMEOUT", MEASURED))
        self.assertEqual(classify_run(run(code=0), 1, expect=0).outcome, PASS)

    def test_negative_failure_without_expectation_fails_with_category(self):
        verdict = classify_run(run(code=4, stage=4, transferred=0, reason="cd_halt_fail"), 1)
        self.assertEqual((verdict.outcome, verdict.category, verdict.name), (FAIL, "TIMEOUT", "DATA_TIMEOUT"))

    def test_negative_wrong_expected_code_fails(self):
        self.assertEqual(classify_run(run(code=2, reason="cd_halt_fail"), 1, expect=4).outcome, FAIL)
        self.assertEqual(classify_run(run(code=0), 1, expect=2).outcome, FAIL)

    def test_negative_unknown_code_is_hypothesis_and_fails(self):
        verdict = classify_run(run(code=0x42, reason="cd_halt_fail"), 1)
        self.assertEqual((verdict.outcome, verdict.category, verdict.evidence), (FAIL, "UNKNOWN_CODE", "HYPOTHESIS"))
        self.assertEqual(classify_run(run(code=0xFF, reason="cd_halt_fail"), 1).category, "UNKNOWN_CODE")

    def test_negative_success_halt_with_inconsistent_result_fails(self):
        for bad in (dict(stage=8), dict(scsi=2), dict(msg=1), dict(transferred=1024)):
            self.assertEqual(classify_run(run(**bad), 1).category, "PROTOCOL_MISMATCH", bad)

    def test_negative_failure_halt_claiming_code_zero_fails(self):
        self.assertEqual(classify_run(run(code=0, reason="cd_halt_fail"), 1).category, "PROTOCOL_MISMATCH")

    def test_negative_interrupt_or_other_halt_is_never_success(self):
        verdict = classify_run(run(reason="cd_unexpected_interrupt"), 1, expect=0)
        self.assertEqual((verdict.outcome, verdict.category), (FAIL, "UNEXPECTED_HALT"))

    def test_skip_when_budget_exhausted_without_verdict(self):
        verdict = classify_run(run(reason="budget", halt_confirmed=False), 1)
        self.assertEqual((verdict.outcome, verdict.category), (SKIP, "INCONCLUSIVE"))

    def test_blocked_without_media_or_incomplete_record(self):
        self.assertEqual(classify_run(run(), 1, media=False).outcome, BLOCKED)
        row = run()
        del row["result_hex"]
        self.assertEqual(classify_run(row, 1).category, "INCOMPLETE_RECORD")
        self.assertEqual(classify_run(run(reason="cd_halt_fail", result_hex="00"), 1).outcome, BLOCKED)

    def test_boundary_request_count_zero_and_four_must_be_rejected_with_code_9(self):
        for count in (0, 4):
            self.assertEqual(classify_run(run(code=9, stage=1, reason="cd_halt_fail"), count, expect=9).outcome, PASS)
            self.assertEqual(classify_run(run(code=9, stage=1, reason="cd_halt_fail"), count).outcome, PASS)
            self.assertEqual(classify_run(run(code=0), count).category, "PROTOCOL_MISMATCH")
            self.assertEqual(classify_run(run(code=5, stage=5, reason="cd_halt_fail"), count).category,
                             "PROTOCOL_MISMATCH")

    def test_boundary_count_three_is_valid_and_nine_is_not_reported_for_it(self):
        self.assertEqual(classify_run(run(code=9, stage=1, reason="cd_halt_fail"), 3).category, "INVALID_REQUEST")
        self.assertEqual(classify_run(run(code=9, stage=1, reason="cd_halt_fail"), 3, expect=0).outcome, FAIL)

    def test_boundary_partial_transfer_before_premature_status_is_reported(self):
        verdict = classify_run(run(code=5, stage=5, transferred=4096, reason="cd_halt_fail"), 3, expect=5)
        self.assertEqual((verdict.outcome, verdict.category, verdict.name, verdict.transferred),
                         (PASS, "PREMATURE_STATUS", "PREMATURE_STATUS_PHASE", 4096))
        self.assertIn("4096 of 6144 bytes", verdict.reason)

    def test_every_probe_code_has_a_name_and_a_declared_evidence_level(self):
        self.assertEqual(set(CODES), set(ERROR_NAMES) - {0xFF})
        for code, (category, evidence) in CODES.items():
            self.assertIn(evidence, {MEASURED, "SOURCE_DEFINED"}, code)


class ClassifyRecordsTests(unittest.TestCase):
    def test_positive_cold_and_warm_success_is_pass(self):
        self.assertEqual(classify_records(records(run("cold"), run("warm")), 1)[0], PASS)

    def test_negative_one_failing_cycle_fails_overall_even_with_blocked_peer(self):
        outcome, _ = classify_records(records(run("cold", code=2, reason="cd_halt_fail", transferred=0),
                                              run("warm", reason="budget")), 1)
        self.assertEqual(outcome, FAIL)

    def test_negative_no_media_is_blocked_not_pass(self):
        outcome, verdicts = classify_records(records(run("cold"), run("warm"), media=False), 1)
        self.assertEqual((outcome, verdicts[0].category), (BLOCKED, "NO_MEDIA"))

    def test_boundary_missing_cycle_or_no_runs_is_blocked(self):
        self.assertEqual(classify_records(records(run("cold")), 1)[0], BLOCKED)
        self.assertEqual(classify_records({"CAP": [{"cdrom_media": True}]}, 1)[0], BLOCKED)

    def test_overall_precedence(self):
        self.assertEqual(overall([PASS, SKIP, BLOCKED, FAIL]), FAIL)
        self.assertEqual(overall([PASS, SKIP, BLOCKED]), BLOCKED)
        self.assertEqual(overall([PASS, SKIP]), SKIP)
        self.assertEqual(overall([PASS, PASS]), PASS)
        self.assertEqual(overall([]), BLOCKED)


if __name__ == "__main__":
    unittest.main()
