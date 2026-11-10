"""Issue #744 MEDIUM: structured IPL_MUTANT_NORANGE kill criterion (host-only).

check_norange_mutant_obs() in tests/integration/geargrafx_ipl_loader.py is
the gate-kill judge for the tail-gate mutant: only the exact expected halt
(code 5, stage 4, copied 0, payload never ran, halt confirmed, over-width LBA
on the bus) counts as proof. Every misjudgment below must raise -- a code 23
or code 6 halt, a stage-3 stop, a copy, a payload trace, an unconfirmed halt,
or a CDB mismatch is never a kill.

No emulator, no ROM, no core: pure observation-dict negatives plus one
observe_run() parse check on a synthetic probe record.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from geargrafx_ipl_loader import (  # noqa: E402
    NORANGE_EXPECTED_CDB,
    check_norange_mutant_obs,
    observe_run,
)


def good_obs(**kw):
    obs = {"cycle": "cold", "reason": "cd_halt_fail", "halt_confirmed": True,
           "code": 5, "stage": 4, "copied": 0,
           "cdb": list(NORANGE_EXPECTED_CDB), "payload_ran": False}
    obs.update(kw)
    return obs


class NorangeKillCriterionTest(unittest.TestCase):
    def test_exact_expected_halt_passes(self):
        self.assertTrue(check_norange_mutant_obs(good_obs()))

    def test_wrong_code_rejected(self):
        for code in (23, 6, 27, 4, 0):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    check_norange_mutant_obs(good_obs(code=code))

    def test_wrong_stage_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(stage=3))

    def test_nonzero_copy_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(copied=5))

    def test_payload_trace_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(payload_ran=True))

    def test_unconfirmed_halt_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(halt_confirmed=False))

    def test_wrong_reason_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(reason="cd_halt_ok"))

    def test_cdb_high_lba_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(cdb=[0x08, 0x1F, 0xFF, 0xFF, 0x01, 0x00]))

    def test_cdb_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            check_norange_mutant_obs(good_obs(cdb=[0x08, 0x00]))

    def test_missing_field_rejected(self):
        for name in ("cycle", "reason", "halt_confirmed", "code", "stage",
                     "copied", "cdb", "payload_ran"):
            with self.subTest(name=name):
                obs = good_obs()
                del obs[name]
                with self.assertRaises(ValueError):
                    check_norange_mutant_obs(obs)

    def test_observe_run_parses_probe_record(self):
        buf = bytearray(6144)
        buf[0x2F00 - 0x2800] = 5       # R_CODE
        buf[0x2F00 - 0x2800 + 1] = 4   # R_STAGE
        buf[0x2F00 - 0x2800 + 2] = 0   # R_COPIED
        buf[0x2F00 - 0x2800 + 16:0x2F00 - 0x2800 + 22] = bytes(NORANGE_EXPECTED_CDB)
        run = {"cycle": "cold", "reason": "cd_halt_fail", "halt_confirmed": True,
               "buffer_hex": bytes(buf).hex()}
        obs = observe_run(run)
        self.assertEqual((obs["code"], obs["stage"], obs["copied"]), (5, 4, 0))
        self.assertEqual(obs["cdb"], NORANGE_EXPECTED_CDB)
        self.assertIs(obs["payload_ran"], False)
        self.assertTrue(check_norange_mutant_obs(obs))

    def test_observe_run_rejects_short_dump(self):
        run = {"cycle": "cold", "reason": "cd_halt_fail", "halt_confirmed": True,
               "buffer_hex": "00" * 100}
        with self.assertRaises(ValueError):
            observe_run(run)


if __name__ == "__main__":
    unittest.main()
