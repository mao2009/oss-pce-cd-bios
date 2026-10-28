"""Host model of the DEV MA_SIN override (slot $45, Issue #79). Exhaustive over all 256 angles.

Contract (DEV-only, project's own spec; NOT the System Card MA_SIN ABI, which stays UNVERIFIED):
  in : A = binary angle 0..255 (256 steps = one full turn). X is not read.
  out: X:A = signed 16-bit Q15, value = floor(32767 * sin(2*pi*a/256) + 0.5), range -32767..+32767.
       X = high byte, A = low byte. -32768 is never produced.
  rounding: the routine computes sign-magnitude (round-half-up of |32767 sin|, then negated). That
  equals floor(x + 0.5) for all 256 inputs because no input is an exact half-integer (checked
  exhaustively below). Same rule as MA_COS (slot $46, PR #527).

The model mirrors src/overrides/slot_45.s (quarter-wave table, quadrant folding, sign). The independent
oracle is floor(32767*sin(2*pi*a/256)+0.5) computed directly with math.sin (no table, no folding).

This does NOT execute the assembled bytes. The assembled routine is checked by the real Geargrafx run in
tests/integration/geargrafx_dev_sin.py (not yet run; see the PR's open items). Source-level checks below
cover the side-effect contract (ZP, stores, MPR, stack, flags) textually.
"""
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_rom import overrides  # noqa: E402

SOURCE = ROOT / "src" / "overrides" / "slot_45.s"
BODY_END_MARK = "; Q15 quarter-wave table"


def oracle_sin(a):
    """Independent oracle: floor(32767 * sin(2*pi*a/256) + 0.5) (round half up, no table)."""
    return math.floor(32767 * math.sin(2 * math.pi * a / 256) + 0.5)


def cos_reference(k):
    """COPY of the MA_COS (slot $46, PR #527) reference: floor(32767*cos(2*pi*k/256)+0.5).

    Kept here, not imported, because PR #527 is a separate, unmerged PR. Used only for the identity
    SIN(a) == COS((a - 64) mod 256).
    """
    return math.floor(32767 * math.cos(2 * math.pi * k / 256) + 0.5)


def cos_model(k, table=None):
    """COPY of the MA_COS quarter-wave model (PR #527 tests/host/test_ma_cos_dev.py), for the identity."""
    table = [cos_reference(i) for i in range(65)] if table is None else table
    r = k & 0x3F
    index = (64 - r) if (k & 0x40) else r
    value = table[index]
    if ((k >> 6) ^ (k >> 7)) & 1:
        value = -value
    return value


def table_from_source(text=None):
    """(lo, hi) byte lists from the sin_lo / sin_hi .byte lines of slot_45.s (65 entries each)."""
    text = SOURCE.read_text(encoding="utf-8") if text is None else text
    lo_block = text.split("sin_lo:", 1)[1].split("sin_hi:", 1)[0]
    hi_block = text.split("sin_hi:", 1)[1].split(BODY_END_MARK, 1)[0] if BODY_END_MARK in text else text.split("sin_hi:", 1)[1]
    return _bytes(lo_block), _bytes(hi_block)


def _bytes(block):
    values = []
    for line in block.splitlines():
        line = line.split(";", 1)[0].strip()
        if line.startswith(".byte"):
            values.extend(int(v.strip().lstrip("$"), 16) for v in line[len(".byte"):].split(","))
    return values


def table_values(lo, hi):
    """Signed-free Q15 table values T[0..64] from the lo/hi byte lists."""
    return [(h << 8) | l for l, h in zip(lo, hi)]


def dev_sin(a, table=None, mutant=None):
    """Model of slot_45.s for angle 0..255. Returns the signed Q15 value.

    `table` is a list of 65 non-negative values T[0..64]; `mutant` names a deliberately wrong routine.
    """
    if type(a) is not int or not 0 <= a <= 255:
        raise ValueError(f"angle must be an integer 0..255, got {a!r}")
    if table is None:
        table = TABLE
    r = a & 63
    if mutant == "no_fold":
        index = r                                   # odd quadrants read T[r] instead of T[64 - r]
    elif mutant == "index_off_by_one":
        index = min(64, ((64 - r) if (a & 0x40) else r) + 1)
    else:
        index = (64 - r) if (a & 0x40) else r
    value = table[index]
    if mutant == "sign_from_bit6":
        negative = bool(a & 0x40)                   # sign taken from the wrong bit
    else:
        negative = bool(a & 0x80)
    if negative and mutant != "no_negate":
        value = -value
    if mutant == "truncate_not_round":
        value = int(32767 * math.sin(2 * math.pi * a / 256))
    return value


def pack(value):
    """(X, A) bytes of a signed 16-bit result: X = high byte, A = low byte."""
    word = value & 0xFFFF
    return word >> 8, word & 0xFF


def source_problems(text):
    """Structural side-effect checks on slot_45.s text. Returns a list of problems (empty = clean)."""
    problems = []
    if "api_slot_45:" not in text:
        return ["api_slot_45 label missing"]
    body = text.split("api_slot_45:", 1)[1].split(BODY_END_MARK, 1)[0]
    code = [line.split(";", 1)[0].strip() for line in body.splitlines()]
    code = [line for line in code if line]
    if code[:3] != ["php", "cld", "phy"]:
        problems.append("entry is not php / cld / phy")
    if code[-3:] != ["ply", "plp", "rts"]:
        problems.append("exit is not ply / plp / rts")
    code_text = "\n".join(code)
    if re.search(r"\b(tam|tma|jsr|brk|sei|cli)\b", code_text, re.I):
        problems.append("MPR, call, interrupt-mask or BRK instruction in the body")
    if "ldx sin_hi,y" not in body or "lda sin_lo,y" not in body:
        problems.append("table reads must be lda sin_lo,y and ldx sin_hi,y (X = high byte)")
    stores = re.findall(r"\bst[axyz]\s+(\S+)", code_text, re.I)
    allowed = {"$F7", "$F9", "$FA"}
    bad = [s for s in stores if s.upper() not in allowed]
    if bad:
        problems.append(f"stores outside zero page $F7/$F9/$FA: {bad}")
    if re.search(r"\b(stz|inc|dec)\s+\S+", code_text, re.I):
        problems.append("read-modify-write to memory in the body")
    return problems


TABLE = None  # filled from the source below


class DevSinModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global TABLE
        lo, hi = table_from_source()
        cls.lo, cls.hi = lo, hi
        TABLE = table_values(lo, hi)

    def test_table_has_65_entries_and_matches_oracle(self):
        """The source table equals floor(32767*sin(pi/2*i/64)+0.5) for i = 0..64, byte for byte."""
        self.assertEqual(len(self.lo), 65)
        self.assertEqual(len(self.hi), 65)
        expected = [math.floor(32767 * math.sin(math.pi / 2 * i / 64) + 0.5) for i in range(65)]
        self.assertEqual(TABLE, expected)
        self.assertEqual(TABLE[0], 0)
        self.assertEqual(TABLE[64], 32767)

    def test_byte_order_low_then_high(self):
        """Byte order: lo table holds the low bytes, hi holds the high bytes; output X=high, A=low."""
        for i in range(65):
            self.assertEqual(self.lo[i], TABLE[i] & 0xFF, i)
            self.assertEqual(self.hi[i], TABLE[i] >> 8, i)
        self.assertEqual(pack(32767), (0x7F, 0xFF))
        self.assertEqual(pack(-32767), (0x80, 0x01))
        self.assertEqual(pack(0), (0x00, 0x00))

    def test_oracle_margin_is_far_from_rounding_ties(self):
        """Float oracle is trustworthy: no non-exact value lies within 1e-6 of a .5 rounding tie."""
        for a in range(256):
            v = 32767 * math.sin(2 * math.pi * a / 256)
            frac = v - math.floor(v)
            self.assertGreater(abs(frac - 0.5), 1e-6, a)

    def test_all_256_angles_match_oracle(self):
        """Every 8-bit angle: model == independent oracle (exhaustive)."""
        for a in range(256):
            self.assertEqual(dev_sin(a), oracle_sin(a), a)

    def test_quadrant_and_zero_boundaries(self):
        """0, 64, 128, 192 give 0, +32767, 0, -32767; neighbours match the oracle."""
        for a, want in {0: 0, 64: 32767, 128: 0, 192: -32767}.items():
            self.assertEqual(dev_sin(a), want, a)
            self.assertEqual(oracle_sin(a), want, a)
        for a in (1, 63, 65, 127, 129, 191, 193, 255):
            self.assertEqual(dev_sin(a), oracle_sin(a), a)

    def test_max_min_and_no_minus_32768(self):
        """Max +32767 only at 64; min -32767 only at 192; -32768 never produced."""
        values = [dev_sin(a) for a in range(256)]
        self.assertEqual(max(values), 32767)
        self.assertEqual(min(values), -32767)
        self.assertEqual([a for a in range(256) if values[a] == 32767], [64])
        self.assertEqual([a for a in range(256) if values[a] == -32767], [192])
        self.assertNotIn(-32768, values)

    def test_positive_negative_symmetry(self):
        """Half-turn antisymmetry sin(a+128) == -sin(a); odd symmetry sin(-a) == -sin(a)."""
        for a in range(256):
            self.assertEqual(dev_sin((a + 128) & 0xFF), -dev_sin(a), a)
            self.assertEqual(dev_sin((256 - a) & 0xFF), -dev_sin(a), a)  # odd symmetry, incl. a=0 and a=128
        for a in range(128, 256):
            self.assertLessEqual(dev_sin(a), 0, a)
        for a in range(0, 129):
            self.assertGreaterEqual(dev_sin(a), 0, a)

    def test_period_256(self):
        """One period is 256 units. dev_sin rejects a+256 by design (test_out_of_range_inputs_rejected),
        so this checks only that the oracle has period 256: oracle(a+256) == model(a) for every a."""
        for a in range(256):
            self.assertEqual(oracle_sin(a + 256), dev_sin(a), a)

    def test_identity_sin_equals_cos_shifted(self):
        """SIN(a) == COS((a - 64) mod 256), with COS from a copy of the PR #527 reference."""
        cos_table = [cos_reference(i) for i in range(65)]
        for k in range(256):
            self.assertEqual(cos_model(k, cos_table), cos_reference(k), k)  # the copied COS model is itself exact
        for a in range(256):
            self.assertEqual(dev_sin(a), cos_model((a - 64) & 0xFF, cos_table), a)

    def test_out_of_range_inputs_rejected(self):
        """Negative test: inputs outside 0..255 (or non-int) are rejected, never wrapped silently."""
        for bad in (-1, 256, 512, 1.0, "64", None, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                dev_sin(bad)

    def test_table_mutant_is_detected(self):
        """Negative test: one wrong table byte makes the model differ from the oracle."""
        mutated_lo = list(self.lo)
        mutated_lo[10] ^= 0x01
        mutated = table_values(mutated_lo, self.hi)
        wrong = [a for a in range(256) if dev_sin(a, table=mutated) != oracle_sin(a)]
        self.assertTrue(wrong)
        self.assertNotEqual(mutated, [math.floor(32767 * math.sin(math.pi / 2 * i / 64) + 0.5) for i in range(65)])

    def test_algorithm_mutants_are_detected(self):
        """Negative test: plausible wrong routines (no fold, wrong index, wrong sign bit, no negation,
        truncation instead of rounding) each differ from the oracle."""
        for mutant in ("no_fold", "index_off_by_one", "sign_from_bit6", "no_negate", "truncate_not_round"):
            wrong = [a for a in range(256) if dev_sin(a, mutant=mutant) != oracle_sin(a)]
            self.assertTrue(wrong, mutant)

    def test_source_is_clean(self):
        """The shipped routine passes the structural side-effect checks."""
        self.assertEqual(source_problems(SOURCE.read_text(encoding="utf-8")), [])

    def test_source_mutants_are_detected(self):
        """Negative test: edits to the routine text are caught (byte order, extra store, MPR, call, stack)."""
        good = SOURCE.read_text(encoding="utf-8")
        mutants = {
            "swap_byte_order": good.replace("ldx sin_hi,y", "ldx sin_lo,y"),
            "store_to_io": good.replace("    ply                 ; Y", "    sta $2000\n    ply                 ; Y", 1),
            "tam_mpr_change": good.replace("    php                 ; save caller's P", "    tam #$02\n    php                 ; save caller's P", 1),
            "no_plp": good.replace("    plp                 ; P", "    nop                 ; P", 1),
            "zp_write_c": good.replace("    sta $F7             ; a = A", "    sta $C0             ; a = A", 1),
        }
        for name, text in mutants.items():
            self.assertNotEqual(text, good, name)
            self.assertTrue(source_problems(text), name)

    def test_slot_45_is_a_dev_override(self):
        self.assertIn(0x45, overrides())
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("api_slot_45:", source)
        self.assertIn("MA_SIN", source)
        self.assertIn("Issue #79", source)
        self.assertIn("release_eligible: false", source)
        self.assertIn('.segment "OVERRIDES"', source)
        # Zero page $F7/$F9/$FA only; no writes to ZP $00-$07 (used by adt_dma_routine.s).
        self.assertIsNone(re.search(r"\bst[azxy]\s+\$0[0-7]\b", source, re.I))


if __name__ == "__main__":
    unittest.main()
