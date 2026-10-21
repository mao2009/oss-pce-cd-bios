"""Synthetic-record checks for tools/cd_stat_observation.py (Issue #231). Records are hand-built, not real-core results."""
import copy
import unittest

from tools.build_cd_media_state import MAGIC, NO_DATA, SIGNATURE_OK
from tools.cd_stat_observation import judge_observation

BUDGET = 12_000_000
LOADER = "paired:SetCDROMType(STANDARD)+LoadHuCardFromBuffer+ResetMedia(false)"


def run_row(cycle, state, nbytes, data=b""):
    result = bytes([state, 6, nbytes & 0xFF, nbytes >> 8, 0, 0]).ljust(32, b"\0")
    buffer = data.ljust(2048, b"\x5a").ljust(6144, b"\0")
    return {"name": "reset_ready", "cycle": cycle, "reason": "reset_ready", "halt_confirmed": True,
            "pc": 0xF010, "x": state, "sp": 0xFF, "p": 0x04, "mpr": [0xFF, 0xF8, 0, 0, 0, 0, 0, 0],
            "step": 150_000, "result_hex": result.hex(), "buffer_hex": buffer.hex()}


def records(state, nbytes, disc, data=b""):
    return {"rom_sha256": "aa",
            "CAP": [{"instruction_step": True, "cdrom_hardware": True, "disc_given": disc, "drive_ready": disc,
                     "loader": LOADER}],
            "HIT": [{"name": "ms_done", "cycle": c} for c in ("cold", "warm")],
            "PHASE": [{"cycle": c, "scsi_phase": 4} for c in ("cold", "warm")] if disc else [],
            "RUN": [run_row(c, state, nbytes, data) for c in ("cold", "warm")]}


WITH_DISC = records(SIGNATURE_OK, 2048, True, MAGIC + b"\x01" * 2030)
NO_DISC = records(NO_DATA, 0, False)


class CdStatObservationTest(unittest.TestCase):
    def test_positive_and_negative_pair_is_observed(self):
        result = judge_observation(WITH_DISC, NO_DISC, BUDGET)
        self.assertEqual(result, {"status": "OBSERVED", "disc": "MEDIA_SIGNATURE_OK", "no_disc": "NO_DATA",
                                  "budget": BUDGET})

    def test_no_media_on_both_sides_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not exactly one-sided"):
            judge_observation(NO_DISC, NO_DISC, BUDGET)

    def test_swapped_pair_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not exactly one-sided"):
            judge_observation(NO_DISC, WITH_DISC, BUDGET)

    def test_budget_exhaustion_is_rejected(self):
        stuck = copy.deepcopy(WITH_DISC)
        stuck["RUN"][1].update(reason="budget", step=BUDGET)
        with self.assertRaisesRegex(ValueError, "not bounded by the step budget"):
            judge_observation(stuck, NO_DISC, BUDGET)

    def test_step_over_budget_is_rejected(self):
        over = copy.deepcopy(WITH_DISC)
        over["RUN"][0]["step"] = BUDGET + 1
        with self.assertRaisesRegex(ValueError, "not bounded by the step budget"):
            judge_observation(over, NO_DISC, BUDGET)

    def test_step_at_budget_boundary_is_observed(self):
        edge = copy.deepcopy(WITH_DISC)
        edge["RUN"][0]["step"] = BUDGET
        self.assertEqual(judge_observation(edge, NO_DISC, BUDGET)["status"], "OBSERVED")

    def test_missing_ram_field_is_blocked(self):
        partial = copy.deepcopy(WITH_DISC)
        del partial["RUN"][0]["buffer_hex"]
        with self.assertRaisesRegex(ValueError, "BLOCKED: .*buffer_hex"):
            judge_observation(partial, NO_DISC, BUDGET)

    def test_missing_step_is_blocked_before_budget_check(self):
        partial = copy.deepcopy(WITH_DISC)
        del partial["RUN"][0]["step"]
        with self.assertRaisesRegex(ValueError, "BLOCKED: .*step"):
            judge_observation(partial, NO_DISC, BUDGET)

    def test_short_ram_buffer_is_blocked(self):
        short = copy.deepcopy(WITH_DISC)
        short["RUN"][0]["buffer_hex"] = (MAGIC).hex()
        with self.assertRaisesRegex(ValueError, "BLOCKED: .*too short"):
            judge_observation(short, NO_DISC, BUDGET)

    def test_negative_or_boolean_step_is_rejected(self):
        for bad in (-5, True):
            neg = copy.deepcopy(WITH_DISC)
            neg["RUN"][0]["step"] = bad
            with self.assertRaisesRegex(ValueError, "not bounded by the step budget"):
                judge_observation(neg, NO_DISC, BUDGET)

    def test_non_positive_budget_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "BLOCKED: step budget"):
            judge_observation(WITH_DISC, NO_DISC, 0)


if __name__ == "__main__":
    unittest.main()
