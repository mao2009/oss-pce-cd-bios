"""Host model of the DEV MA_MUL8U override (slot $3F, Issue #436), checked for all 65,536 pairs.

The model mirrors src/overrides/slot_3F.s step by step: hi/lo/m live in stack bytes at $2101,x / $2102,x / $2103,x.
One LSR of lo runs first, then each of the 8 rounds does ADC m on carry, ROR hi, ROR lo.

This is a Python reference-model check of the algorithm. It does NOT test the assembled bytes exhaustively; the
assembled routine is exercised only by the real-core run (18 cases) in tests/integration/geargrafx_dev_mul8u.py.
This is not the System Card MA_MUL8U ABI; it only checks that the emitted algorithm multiplies.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402


def dev_mul8u(a, x):
    """A = multiplicand, X = multiplier -> (A = product low, X = product high), as slot_3F.s."""
    stack = {"hi": 0, "lo": x >> 1, "m": a}
    carry = x & 1  # lda $2102,x / lsr a / sta $2102,x before the rounds: C = multiplier bit 0
    for _ in range(8):
        if carry:  # bcc not taken: clc; lda hi; adc m
            total = stack["hi"] + stack["m"]
            stack["hi"], carry = total & 0xFF, total >> 8
        bit0 = stack["hi"] & 1  # ror $2101,x: carry in, bit 0 out
        stack["hi"] = (carry << 7) | (stack["hi"] >> 1)
        carry = stack["lo"] & 1  # ror $2102,x: C leaves as the next multiplier bit
        stack["lo"] = (bit0 << 7) | (stack["lo"] >> 1)  # hi bit 0 enters bit 7
    return stack["lo"], stack["hi"]


class DevMul8uModelTests(unittest.TestCase):
    def test_all_pairs_match_product(self):
        for a in range(256):
            for x in range(256):
                product = a * x
                self.assertEqual((product & 0xFF, product >> 8), dev_mul8u(a, x), (a, x))

    def test_slot_3f_is_a_dev_override(self):
        self.assertIn(0x3F, overrides())
        source = (ROOT / "src" / "overrides" / "slot_3F.s").read_text(encoding="utf-8")
        self.assertIn("api_slot_3F:", source)
        self.assertIn("ldy #8", source)  # the 8 rounds: Y counter, BNE back to @round
        self.assertIn("bne @round", source)

    def test_all_pairs_match_repeated_addition(self):
        # Independent of a*x and of the shift-add model: X additions of A, 16-bit accumulator.
        for a in range(256):
            acc = 0
            for x in range(256):
                self.assertEqual((acc & 0xFF, acc >> 8), dev_mul8u(a, x), (a, x))
                acc += a

    def test_named_edge_values(self):
        cases = {  # (A, X): (low, high); 255 * 255 = 65025 = $FE01 is the maximum product
            (0, 0): (0x00, 0x00), (0, 255): (0x00, 0x00), (255, 0): (0x00, 0x00),
            (1, 1): (0x01, 0x00), (1, 255): (0xFF, 0x00), (255, 1): (0xFF, 0x00),
            (128, 2): (0x00, 0x01), (16, 16): (0x00, 0x01), (255, 255): (0x01, 0xFE),
        }
        for (a, x), want in cases.items():
            self.assertEqual(want, dev_mul8u(a, x), (a, x))

    def _slot_code(self):
        """Instruction mnemonics + operands of the slot body, comments and labels removed, in order."""
        source = (ROOT / "src" / "overrides" / "slot_3F.s").read_text(encoding="utf-8")
        body = source.split("api_slot_3F:", 1)[1]
        return [line.split(";", 1)[0].strip() for line in body.splitlines() if line.split(";", 1)[0].strip()]

    def test_flags_and_y_are_saved_first_and_restored_last(self):
        code = self._slot_code()
        self.assertEqual(["php", "cld"], code[:2])  # P saved before D is cleared for binary ADC
        self.assertIn("phy", code[:3])
        self.assertEqual(["plp", "rts"], code[-2:])  # caller's P restored last
        self.assertEqual(["ply", "ply"], code[-4:-2])  # discard m, then restore caller's Y

    def test_stack_pushes_and_pulls_balance(self):
        code = self._slot_code()
        pushes = sum(1 for op in code if op.split()[0] in ("php", "phy", "pha"))
        pulls = sum(1 for op in code if op.split()[0] in ("plp", "ply", "pla", "plx"))
        self.assertEqual(5, pushes)  # 5 bytes: P, Y, m, lo, hi
        self.assertEqual(pushes, pulls)

    def test_stack_offsets_stay_in_the_pushed_bytes(self):
        # After 5 pushes X = S-5, so hi/lo/m sit at X+1..X+3 (= S-4..S-2), inside the pushed region.
        # The routine needs S >= $05 on entry; the offsets below must never leave that 5-byte window.
        import re
        source = (ROOT / "src" / "overrides" / "slot_3F.s").read_text(encoding="utf-8")
        offsets = {int(off, 16) for off in re.findall(r"\$21([0-9A-Fa-f]{2}),x", source)}
        self.assertEqual({0x01, 0x02, 0x03}, offsets)
        self.assertEqual(5, len(re.findall(r"^\s*(?:php|phy|pha)\b", source, re.M)))


if __name__ == "__main__":
    unittest.main()
