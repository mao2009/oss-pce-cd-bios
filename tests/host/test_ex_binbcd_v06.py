"""Issue #70 V06: register and flag checks for the project-own EX_BINBCD (DEV only).

Contract under test is docs/ex-binbcd-internal.md (project-own, NOT the System Card $3C ABI).
The reference $3C/$E0B4 contract stays UNKNOWN; nothing here is reference evidence.
The routine is never executed here: A/X/Y/P are checked against a host model, and
memory/SP side effects are checked only statically on the source (not verified on a core).
"""
import json
import re
import unittest
from pathlib import Path

from tools.ex_binbcd import binbcd_call

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/ex-binbcd-v06-vectors.json"
SOURCE = ROOT / "src/ex_binbcd.s"

C_FLAG, I_FLAG, D_FLAG, T_FLAG = 0x01, 0x04, 0x08, 0x20
# (X, Y, P) entry states; D is kept clear because D=1 on entry is outside the contract.
ENTRY_STATES = ((0x00, 0x00, 0x00), (0xA5, 0x5A, 0x04), (0xFF, 0x01, 0xF7))


def independent_expected(value):
    """Independent oracle: decimal string digits, not the model's arithmetic."""
    if value >= 100:
        return value, 1
    tens, ones = f"{value:02d}"
    return (int(tens) << 4) | int(ones), 0


class ExBinBcdV06Tests(unittest.TestCase):
    def test_full_domain_a_x_y_c_match_independent_oracle_for_all_entry_states(self):
        for value in range(256):
            for x, y, p in ENTRY_STATES:
                a_out, x_out, y_out, p_out = binbcd_call(value, x, y, p)
                packed, carry = independent_expected(value)
                self.assertEqual(a_out, packed, (value, x, y, p))
                self.assertEqual(p_out & C_FLAG, carry, (value, x, y, p))
                self.assertEqual((x_out, y_out), (x, y), (value, x, y, p))

    def test_flags_other_than_c_are_kept(self):
        # I, T, D pass through; N/Z/V are unspecified and not asserted.
        for value in range(256):
            for x, y, p in ENTRY_STATES:
                p_out = binbcd_call(value, x, y, p)[3]
                for flag in (D_FLAG, I_FLAG, T_FLAG):
                    self.assertEqual(p_out & flag, p & flag, (value, p, flag))

    def test_static_source_has_no_memory_write_or_mode_change_instructions(self):
        # Static text check only: it does not prove runtime side effects.
        code = re.sub(r";[^\n]*", "", SOURCE.read_text())
        mnemonics = ("sta", "stx", "sty", "stz", "tsb", "trb", "tia", "tai", "tii", "tdd",
                     "st0", "st1", "st2", "sed", "cld", "tam", "tma", "cli", "sei", "tst",
                     "csh", "csl", "rmb[0-7]", "smb[0-7]")
        for mnemonic in mnemonics:
            self.assertIsNone(re.search(rf"(?:^|:)\s*{mnemonic}\b", code, re.M | re.I), mnemonic)

    def test_boundary_fixture_matches_independent_oracle(self):
        vectors = json.loads(FIXTURE.read_text())["vectors"]
        self.assertEqual({v["input_a"] for v in vectors},
                         {0, 1, 9, 10, 11, 19, 20, 89, 90, 91, 98, 99, 100, 101, 127, 128, 199, 200, 254, 255})
        for vector in vectors:
            x, y, p = vector["x"], vector["y"], vector["p"]
            a_out, x_out, y_out, p_out = binbcd_call(vector["input_a"], x, y, p)
            self.assertEqual((a_out, p_out & C_FLAG), (vector["expect_a"], vector["expect_c"]), vector["input_a"])
            self.assertEqual((x_out, y_out), (x, y), vector["input_a"])
            self.assertEqual((vector["expect_a"], vector["expect_c"]),
                             independent_expected(vector["input_a"]), vector["input_a"])


if __name__ == "__main__":
    unittest.main()
