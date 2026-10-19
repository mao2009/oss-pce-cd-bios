"""ex_bcdbin (Issue #71): host reference model of src/ex_bcdbin.s.

The contract is this project's own spec (docs/ex-bcdbin.md), not the reference BIOS ABI.
The real-core run is opt-in: tests/integration/geargrafx_bcdbin.py (Linux only), not part of host tests.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

RESULT_BYTES = 4 * 256 + 1  # A, P, X, Y tables + final SP (tests/fixtures/bcdbin_harness.s)


def model(a):
    """Reference model of the project spec: packed 2-digit BCD byte -> (A, C)."""
    tens, ones = a >> 4, a & 0x0F
    if tens > 9 or ones > 9:
        return a, 1
    return tens * 10 + ones, 0


def oracle(a):
    """Independent formulation: a valid packed BCD byte's hex text reads as decimal."""
    text = f"{a:02X}"
    return (int(text), 0) if text.isdigit() else (a, 1)


def check_ram(ram):
    a, p, x, y = (ram[i * 256:(i + 1) * 256] for i in range(4))
    bad = [f"${n:02X}" for n in range(256)
           if (a[n], p[n] & 1) != model(n) or (x[n], y[n]) != (0x5A, 0xA5)]
    if bad or ram[1024] != 0xFF:
        raise ValueError(f"guest results differ from model at {bad[:8]} (sp=${ram[1024]:02X})")


class ModelTests(unittest.TestCase):
    def test_model_matches_oracle_for_all_256_inputs(self):
        for a in range(256):
            self.assertEqual(model(a), oracle(a), f"${a:02X}")
        self.assertEqual(sum(model(a)[1] == 0 for a in range(256)), 100)

    def test_boundaries(self):
        cases = {0x00: (0, 0), 0x09: (9, 0), 0x10: (10, 0), 0x19: (19, 0), 0x90: (90, 0),
                 0x99: (99, 0), 0x0A: (0x0A, 1), 0x0F: (0x0F, 1), 0x9A: (0x9A, 1),
                 0xA0: (0xA0, 1), 0xA9: (0xA9, 1), 0xFF: (0xFF, 1)}
        for a, want in cases.items():
            self.assertEqual(model(a), want, f"${a:02X}")

    def test_round_trip_bin_bcd_bin_model(self):
        # Later integration target: replace this encoder with EX_BINBCD (#70) once it exists.
        for n in range(100):
            self.assertEqual(model((n // 10) << 4 | n % 10), (n, 0))


class RealCoreBlockedTests(unittest.TestCase):
    """Negative: an unavailable real core must exit nonzero as BLOCKED, never SKIP (OK) or PASS."""

    def test_missing_core_exits_nonzero_not_skip(self):
        script = Path(__file__).resolve().parents[1] / "integration/geargrafx_bcdbin.py"
        missing = Path(tempfile.gettempdir()) / "missing-geargrafx-core-issue71.so"
        env = dict(os.environ, GEARGRAFX_CORE=str(missing))
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                env=env, timeout=120)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("BLOCKED real core", output)
        self.assertNotIn("skipped", output.lower())


if __name__ == "__main__":
    unittest.main()
