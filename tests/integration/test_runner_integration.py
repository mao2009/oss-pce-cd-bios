"""Runner integration tests (Phase 2 stage 3): run_series() with real processes and the fake warm child.

No Geargrafx, no ROM, no WSL. The fake child prints its schedule at REAL_LIMITS scale (0.5 s per unit).
Run from this directory: python -m unittest -v test_runner_integration
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import geargrafx_ad_reset_warm as runner  # noqa: E402
import warm_runner_window as w  # noqa: E402
import fake_warm_child as fake  # noqa: E402

FAKE = HERE / "fake_warm_child.py"
NONCE = "abcdef0123456789"
SCALE = "0.5"


def fake_command(mode):
    return [sys.executable, str(FAKE), mode, NONCE, "--scale", SCALE]


def series(mode):
    return runner.run_series(fake_command(mode), NONCE, limits=w.REAL_LIMITS)


def problems(result):
    return runner.check_protocol(result, fake.FAKE_BIOS_SHA, fake.FAKE_CUE_SHA)


class RunnerIntegration(unittest.TestCase):
    def test_normal_series_passes_protocol(self):
        r = series("normal")
        self.assertEqual(r["end_state"], "COMPLETED")
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual(r["record"]["end_state"], "COMPLETED")
        self.assertEqual(problems(r), [])

    def test_trial1_timeout_is_decided_by_window_not_probe(self):
        r = series("trial1_timeout")
        self.assertEqual(r["end_state"], "TIMEOUT")
        self.assertEqual(r["record"], "NOT_CAPTURED")   # no JSON is kept for a window TIMEOUT
        self.assertEqual(problems(r), [])

    def test_b_final_only_is_blocked_with_reason(self):
        r = series("b_final_only")
        self.assertEqual(r["end_state"], "BLOCKED")
        self.assertEqual(r["window"]["series_reason"], runner.REASON_B)
        self.assertEqual(r["exit_code"], 1)
        self.assertEqual(problems(r), [])

    def test_process_timeout(self):
        r = series("process_timeout")
        self.assertEqual(r["end_state"], "TIMEOUT")
        self.assertEqual(r["record"], "NOT_CAPTURED")
        self.assertEqual(problems(r), [])

    def test_b_gate_json_is_checked_when_present(self):
        # b_final_only carries the gate JSON; a missing JSON or a wrong reason must be a protocol problem
        r = series("b_final_only")
        self.assertEqual(problems(r), [])
        self.assertEqual(r["record"]["end_reason"], runner.REASON_B)

    def test_b_without_json_is_a_protocol_problem(self):
        p = problems(series("b_final_no_json"))
        self.assertTrue(any("no JSON" in x for x in p), p)

    def test_b_with_wrong_reason_is_a_protocol_problem(self):
        p = problems(series("b_json_wrong_reason"))
        self.assertTrue(any("end_reason" in x or "not BLOCKED" in x for x in p), p)

    def test_pid_must_be_an_integer(self):
        p = problems(series("pid_string"))
        self.assertTrue(any("pid must be an integer" in x for x in p), p)

    def test_json_disagreeing_with_control_lines_is_rejected(self):
        # the child prints a trial 1 state in JSON that its control line did not report
        r = series("state_mismatch")
        self.assertTrue(any("does not match its control line" in p for p in problems(r)))

    def test_mismatch_becomes_aborted_and_keeps_window_result(self):
        # pid_string: the window is clean (COMPLETED), the protocol check is not, so the series becomes ABORTED
        r = runner.finish_series(series("pid_string"), fake.FAKE_BIOS_SHA, fake.FAKE_CUE_SHA)
        self.assertEqual(r["end_state"], "ABORTED")
        self.assertEqual(r["end_reason"], "protocol_mismatch")
        self.assertEqual(r["window_state"], "COMPLETED")
        self.assertIn("window_end_reason", r)
        self.assertTrue(r["protocol_problems"])


if __name__ == "__main__":
    unittest.main()
