"""Host checks for the project-own EX_RND model and probe build (Issue #72). Not the System Card $3E ABI."""
import tempfile
import unittest
from tools.environment import requires_cc65

from tools.ex_rnd import lfsr_step, build_probe, rnd_next, rnd_sequence


class ExRndModelTests(unittest.TestCase):
    def test_same_seed_gives_same_sequence(self):
        self.assertEqual(rnd_sequence(0x1234, 256), rnd_sequence(0x1234, 256))

    def test_different_seeds_differ(self):
        self.assertNotEqual(rnd_sequence(0x1234, 32), rnd_sequence(0x1235, 32))

    def test_zero_state_is_replaced_by_one(self):
        # Power-on zero or seed 0 must not lock at zero: it behaves exactly as state 1.
        self.assertEqual(rnd_sequence(0, 256), rnd_sequence(1, 256))
        self.assertIn(rnd_next(0)[0], range(256))
        self.assertTrue(any(rnd_sequence(0, 256)))

    def test_state_visits_all_nonzero_states(self):
        # Maximal 16-bit Galois LFSR with taps $B400: period 65535, never reaches zero.
        state, steps = 1, 0
        while True:
            state, steps = lfsr_step(state), steps + 1
            self.assertNotEqual(state, 0)
            if state == 1:
                break
        self.assertEqual(steps, 65535)

    def test_next_is_eight_steps_and_returns_low_byte(self):
        byte, state = rnd_next(0x0001)
        expected = 0x0001
        for _ in range(8):
            expected = lfsr_step(expected)
        self.assertEqual((byte, state), (expected & 0xFF, expected))


class ExRndProbeTests(unittest.TestCase):
    @requires_cc65
    def test_probe_assembles_with_exported_points(self):
        with tempfile.TemporaryDirectory() as directory:
            rom, symbols = build_probe(directory)
            data = rom.read_bytes()
        taps = symbols["ex_rnd_taps"] - 0xE000
        self.assertEqual(data[taps:taps + 2], b"\x49\xb4")  # EOR #$B4 (high byte of $B400)
        self.assertEqual(data[0x1FFE:], b"\x00\xe0")  # reset vector -> probe_reset


if __name__ == "__main__":
    unittest.main()
