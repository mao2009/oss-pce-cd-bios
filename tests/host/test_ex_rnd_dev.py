"""MODEL-ONLY host check of the DEV EX_RND contract (slot $3E, Issue #72). Not the System Card ABI.

This host test checks a Python model of the documented contract (Y=1 seeds with X:A,
other Y returns the next byte, state at $2500/$2501) and static checks on the assembly
source. It does NOT assemble or execute src/overrides/slot_3E.s by itself.

The assembled slot_3E.s IS executed by the real-core integration test
tests/integration/geargrafx_dev_ex_rnd.py (pinned cc65 + Geargrafx), which is registered in
the `dev-integration` target. Keep host model checks and real-core checks distinct.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ex_rnd  # noqa: E402


class DevRndModel:
    """Model of the slot: a state word (work RAM $2500/$2501) plus the seed/next selector in Y."""

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
    def test_seed_is_not_ignored(self):
        self.assertNotEqual(ex_rnd.rnd_sequence(0x1234, 8), ex_rnd.rnd_sequence(0x1235, 8))


class AssemblyZeroPageTests(unittest.TestCase):
    """Regression: the state must not live in HuC6280 zero page ($2000-$20FF, shared with adt_dma_routine.s).

    Reads the assembly source (not the model). Fails on the earlier revision that used $2000/$2001.
    """
    SRC = ROOT / "src" / "overrides" / "slot_3E.s"

    def _code_operands(self):
        import re
        ops = []
        for line in self.SRC.read_text(encoding="utf-8").splitlines():
            code = line.split(";", 1)[0]
            ops += [int(m, 16) for m in re.findall(r"\$([0-9A-Fa-f]{4})", code)]
        return ops

    def test_no_absolute_operand_in_zero_page_window(self):
        bad = [hex(v) for v in self._code_operands() if 0x2000 <= v <= 0x20FF]
        self.assertEqual([], bad)

    def test_state_is_at_2500_2501(self):
        self.assertTrue(any(v == 0x2500 for v in self._code_operands()))
        self.assertTrue(any(v == 0x2501 for v in self._code_operands()))




class SpecContractConsistencyTests(unittest.TestCase):
    """Regression: spec/dev_implementations.json must agree with the implementation state address.

    Catches a stale ledger entry ($2000/$2001) that disagrees with src/overrides/slot_3E.s ($2500/$2501).
    """
    SPEC = ROOT / "spec" / "dev_implementations.json"
    SRC = ROOT / "src" / "overrides" / "slot_3E.s"

    def test_spec_note_uses_2500_2501_not_2000_2001(self):
        data = json.loads(self.SPEC.read_text(encoding="utf-8"))
        slot = next(s for s in data["slots"] if s.get("candidate_name") == "EX_RND")
        note = slot["abi_contract"]["note"]
        self.assertIn("$2500/$2501", note)
        self.assertNotIn("$2000/$2001", note)
        self.assertEqual("PROJECT_SPEC_ONLY", slot["abi_contract"]["status"])

    def test_spec_state_address_matches_slot_source(self):
        ops = []
        import re
        for line in self.SRC.read_text(encoding="utf-8").splitlines():
            code = line.split(";", 1)[0]
            ops += [int(m, 16) for m in re.findall(r"\$([0-9A-Fa-f]{4})", code)]
        self.assertTrue(any(v == 0x2500 for v in ops))
        self.assertTrue(any(v == 0x2501 for v in ops))
        self.assertFalse(any(0x2000 <= v <= 0x20FF for v in ops))


if __name__ == "__main__":
    unittest.main()
