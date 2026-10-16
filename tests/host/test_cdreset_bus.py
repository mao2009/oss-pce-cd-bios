"""Host checks for the reset-pulse judge of the Issue #10 CD_RESET bus diagnostic (synthetic records only).

These prove the judge's rules, not the hardware or the emulator. Real behaviour is judged in
tests/integration/geargrafx_cdreset.py against Geargrafx.
"""
import copy
import unittest

from tools.cdreset_bus_verify import RECORD_BYTES, RECORD_OFFSET, check_reset_pulses, pulse_records


def good_run(cycle="cold"):
    block = bytearray(32)
    block[0], block[1], block[2], block[3] = 0, 9, 0x00, 0x08  # success, stage 9, 2048 bytes
    block[7] = 2                                             # two reset pulses completed
    for index in range(2):
        base = RECORD_OFFSET + index * RECORD_BYTES
        block[base:base + 5] = bytes((0x00, 0x00, 0x00, 0x02, 0x00))  # bus free, wait ok, $1804 = $02 then $00
    return {"cycle": cycle, "result_hex": block.hex()}


def mutate(run, index, field_offset, value):
    twin = copy.deepcopy(run)
    block = bytearray.fromhex(twin["result_hex"])
    block[RECORD_OFFSET + index * RECORD_BYTES + field_offset] = value
    twin["result_hex"] = block.hex()
    return twin


class CdResetBusJudgeTests(unittest.TestCase):
    def test_good_pulses_are_accepted(self):
        run = good_run()
        check_reset_pulses(run)
        rows = pulse_records(run)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["readback_asserted"], 0x02)

    def test_reset_never_asserted_is_rejected(self):
        for index in range(2):
            with self.assertRaisesRegex(ValueError, "reset not asserted"):
                check_reset_pulses(mutate(good_run(), index, 3, 0x00))

    def test_release_readback_still_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "still"):
            check_reset_pulses(mutate(good_run(), 0, 4, 0x02))

    def test_bus_not_free_after_reset_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not free"):
            check_reset_pulses(mutate(good_run(), 1, 2, 0x01))

    def test_bus_busy_right_after_release_is_rejected(self):
        for busy in (0x08, 0x40, 0x80, 0xF8):  # any of BSY/REQ/MSG/CD/IO
            with self.assertRaisesRegex(ValueError, "busy"):
                check_reset_pulses(mutate(good_run(), 0, 1, busy))

    def test_wrong_pulse_count_is_rejected(self):
        for count in (0, 1, 3):
            run = good_run()
            block = bytearray.fromhex(run["result_hex"])
            block[7] = count
            run["result_hex"] = block.hex()
            with self.assertRaisesRegex(ValueError, "reset pulses"):
                check_reset_pulses(run)

    def test_bus_while_asserted_is_recorded_not_judged(self):
        # Geargrafx resets the bus immediately, so a busy sample while asserted is an observation, not a failure
        check_reset_pulses(mutate(good_run(), 0, 0, 0x40))


if __name__ == "__main__":
    unittest.main()
