"""Host checks for the Issue #215 CD-DA scenario judges on synthetic observations (no emulator needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import geargrafx_cdda_scenarios as scenarios  # noqa: E402

from tools.cdda_verify import IDLE, PAUSED, PLAYING, SAMPLES_PER_SECTOR, check_guest, check_invalid_audio_start, check_playback, pcm


def hit(name, step, x, state, lba=0, sample=0, start=0):
    return {"name": name, "step": step, "x": x, "scsi_phase": 0,
            "audio": {"state": state, "start_lba": start, "stop_lba": 79, "current_lba": lba, "current_sample": sample}}


class ValidStartTests(unittest.TestCase):
    def test_playback_from_lba_40_is_judged_from_40_and_not_from_16(self):
        start, played = 40, SAMPLES_PER_SECTOR + 10
        rows = [[start + i // SAMPLES_PER_SECTOR, i % SAMPLES_PER_SECTOR,
                 *pcm(start + i // SAMPLES_PER_SECTOR, i % SAMPLES_PER_SECTOR), 300 + i] for i in range(played)]
        points = {0: (hit("cdda_cmd_done", 100, 0, IDLE), None),
                  1: (hit("cdda_cmd_done", 200, 1, PLAYING, start, 0, start), hit("cdda_wait_done", 900, 0, PLAYING, 41, 5, start)),
                  3: (hit("cdda_cmd_done", 5000, 3, PAUSED, 41, 10, start), hit("cdda_wait_done", 5100, 0, PAUSED, 41, 10, start))}
        records = {"JUMP": [[0, 0, start, 0, 199]], "AUD": rows}
        self.assertEqual(check_playback(records, points, start)["samples_verified_against_bin"], played)
        with self.assertRaisesRegex(ValueError, "expected PLAYING from LBA 16"):
            check_playback(records, points)


class InvalidStartTests(unittest.TestCase):
    def points(self, state_after=IDLE, lba_after=0):
        before = hit("cdda_cmd_done", 100, 0, IDLE)
        return {0: (before, None),
                1: (hit("cdda_cmd_done", 150, 1, state_after, lba_after), hit("cdda_wait_done", 200, 0, state_after, lba_after))}

    def test_unchanged_state_with_no_seek_or_sample_passes(self):
        self.assertEqual(check_invalid_audio_start({"JUMP": [], "AUD": []}, self.points())["samples"], 0)

    def test_playing_after_invalid_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CD-DA changed after \\$D8"):
            check_invalid_audio_start({"JUMP": [], "AUD": []}, self.points(PLAYING, 16))

    def test_a_seek_on_invalid_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "seek or samples on an invalid start LBA"):
            check_invalid_audio_start({"JUMP": [[0, 0, 80, 0, 160]], "AUD": []}, self.points())


class BudgetTests(unittest.TestCase):
    def test_unfinished_budget_run_is_rejected_as_budget(self):
        run = {"reason": "budget", "halt_confirmed": False, "result_hex": "00" * 0xB0}
        with self.assertRaisesRegex(ValueError, r"\(budget\)"):
            check_guest(run)


class TimeoutRejectedTests(unittest.TestCase):
    """Judge for the real-core timeout scenario: a budget overrun is rejected, never a PASS (synthetic runs)."""
    def make_run(self, **changes):
        run = {"reason": "budget", "halt_confirmed": False, "step": scenarios.TIMEOUT_BUDGET, "result_hex": "00" * 0xB0}
        run.update(changes)
        return run

    def test_unfinished_budget_exhaustion_is_rejected_as_budget(self):
        self.assertIn("(budget)", scenarios.timeout_rejected(self.make_run()))

    def test_successful_halt_is_not_accepted_as_timeout(self):
        with self.assertRaisesRegex(ValueError, "budget not exhausted"):
            scenarios.timeout_rejected(self.make_run(reason="cdda_halt_ok", halt_confirmed=True))

    def test_budget_reason_with_confirmed_halt_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "budget not exhausted"):
            scenarios.timeout_rejected(self.make_run(halt_confirmed=True))

    def test_budget_reason_at_another_step_count_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "budget not exhausted"):
            scenarios.timeout_rejected(self.make_run(step=scenarios.TIMEOUT_BUDGET - 1))


if __name__ == "__main__":
    unittest.main()
