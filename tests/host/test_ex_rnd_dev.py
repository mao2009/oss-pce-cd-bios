"""Host model of the DEV EX_RND override (slot $3E, Issue #72). Not the System Card ABI.

Mirrors src/overrides/slot_3E.s: Y=1 seeds with X:A, any other Y returns the next byte.
The state lives at $2000/$2001. The assembled bytes are exercised only by the real-core run.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ex_rnd  # noqa: E402


class DevRndModel:
    """Model of the slot: a state word (work RAM $2000/$2001) plus the seed/next selector in Y."""

    def __init__(self, state=0):
        self.state = state & 0xFFFF

    def call(self, y, x, a):
        if y == 1:
            self.state = (x << 8) | a
            return None
        byte, self.state = ex_rnd.rnd_next(self.state)
        return byte


class SlotContractTests(unittest.TestCase):
    def test_seed_then_next_matches_model_sequence(self):
        m = DevRndModel()
        m.call(1, 0x12, 0x34)
        self.assertEqual(ex_rnd.rnd_sequence(0x1234, 256), [m.call(0, 0, 0) for _ in range(256)])

    def test_same_seed_reproduces(self):
        a, b = DevRndModel(0x0000), DevRndModel(0xBEEF)
        a.call(1, 0x9A, 0xBC)
        b.call(1, 0x9A, 0xBC)
        self.assertEqual([a.call(0, 0, 0) for _ in range(64)], [b.call(0, 0, 0) for _ in range(64)])

    def test_zero_seed_behaves_as_seed_one(self):
        m = DevRndModel()
        m.call(1, 0, 0)
        self.assertEqual(ex_rnd.rnd_sequence(1, 64), [m.call(0, 0, 0) for _ in range(64)])

    def test_zeroed_ram_without_seed_behaves_as_seed_one(self):
        m = DevRndModel(0)
        self.assertEqual(ex_rnd.rnd_sequence(1, 16), [m.call(0, 0, 0) for _ in range(16)])

    def test_state_never_zero_and_period_is_65535(self):
        state, seen = 1, set()
        for _ in range(65535):
            _, state = ex_rnd.rnd_next(state)
            self.assertNotEqual(0, state)
            self.assertNotIn(state, seen)
            seen.add(state)
        self.assertEqual(65535, len(seen))
        self.assertEqual(1, state)  # back to the start after 65535 states

    def test_y_selector_only_one_seeds(self):
        m = DevRndModel()
        m.call(1, 0x00, 0x42)
        before = m.state
        self.assertIsNotNone(m.call(0, 0, 0))
        self.assertNotEqual(before, m.state)
        self.assertIsNone(DevRndModel().call(1, 0, 1))

    def test_output_is_low_byte_after_eight_steps(self):
        byte, state = ex_rnd.rnd_next(0x1234)
        self.assertEqual(state & 0xFF, byte)
        s = 0x1234
        for _ in range(8):
            s = ex_rnd.lfsr_step(s)
        self.assertEqual(s, state)


class NegativeTests(unittest.TestCase):
    def test_wrong_taps_change_sequence(self):
        mutant, s = [], 0x1234
        for _ in range(16):
            for _ in range(8):
                bit, s = s & 1, s >> 1
                if bit:
                    s ^= 0xB500
            mutant.append(s & 0xFF)
        self.assertNotEqual(ex_rnd.rnd_sequence(0x1234, 16), mutant)

    def test_seed_is_not_ignored(self):
        self.assertNotEqual(ex_rnd.rnd_sequence(0x1234, 8), ex_rnd.rnd_sequence(0x1235, 8))

    def test_source_uses_only_declared_state_and_slot(self):
        src = (ROOT / "src/overrides/slot_3E.s").read_text(encoding="utf-8")
        self.assertIn(".export api_slot_3E", src)
        self.assertIn("api_slot_3E:", src)
        self.assertNotIn("$2002", src)
        self.assertNotIn("$2200", src)


if __name__ == "__main__":
    unittest.main()
