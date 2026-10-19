"""ex_bcdbin (Issue #71): host reference model of src/ex_bcdbin.s.

The contract is this project's own spec (docs/ex-bcdbin.md), not the reference BIOS ABI.
The real-core run is tests/integration/geargrafx_bcdbin.py, executed by `make integration` (Linux only).
This module's RealCoreFailurePathTests (negative paths of that run) are host tests, so `make test` and CI run them.
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


INTEGRATION = Path(__file__).resolve().parents[1] / "integration/geargrafx_bcdbin.py"


class RealCoreFailurePathTests(unittest.TestCase):
    """Negative paths of the real-core run: each must fail, never SKIP (OK) or PASS.

    Linux (make setup) checks the exact exit code (3 for BLOCKED, 1 for run failures) and the cause.
    Non-Linux hosts stop at the platform check: every path must then exit 3 with the BLOCKED tag.
    Only Linux exercises the core-missing, tool-missing, launch and timeout causes.
    """

    def run_integration(self, env_updates, timeout=120):
        env = dict(os.environ, **env_updates)
        result = subprocess.run([sys.executable, str(INTEGRATION)], capture_output=True, text=True,
                                env=env, timeout=timeout)
        return result.returncode, result.stdout + result.stderr

    def assert_failure(self, env_updates, cause, exit_code, timeout=120):
        returncode, output = self.run_integration(env_updates, timeout)
        self.assertNotIn("skipped", output.lower())
        if sys.platform.startswith("linux"):
            self.assertEqual(returncode, exit_code, output)
            self.assertIn(cause, output)
        else:
            self.assertEqual(returncode, 3, output)
            self.assertIn("BLOCKED real core", output)

    def test_core_missing_is_blocked(self):
        missing = Path(tempfile.gettempdir()) / "missing-geargrafx-core-issue71.so"
        self.assert_failure({"GEARGRAFX_CORE": str(missing)}, "missing Geargrafx core", 3)

    def test_ca65_missing_is_blocked(self):
        self.assert_failure({"GEARGRAFX_CORE": str(INTEGRATION), "CA65": "/nonexistent/ca65-issue71"},
                            "missing tool ca65", 3)

    def test_ld65_missing_is_blocked(self):
        self.assert_failure({"GEARGRAFX_CORE": str(INTEGRATION), "CA65": sys.executable,
                             "LD65": "/nonexistent/ld65-issue71"}, "missing tool ld65", 3)

    def test_launch_failure_exits_nonzero(self):
        with tempfile.TemporaryDirectory(prefix="issue71-bogus-core-") as directory:
            bogus = Path(directory) / "bogus_core.so"
            bogus.write_bytes(b"not a shared object\n")
            self.assert_failure({"GEARGRAFX_CORE": str(bogus)}, "Geargrafx run failed", 1)

    def test_timeout_exits_nonzero(self):
        # A core whose constructor never returns: the worker hangs until run_core's 30 s timeout.
        with tempfile.TemporaryDirectory(prefix="issue71-hang-core-") as directory:
            hang = Path(directory) / "hang_core.so"
            if sys.platform.startswith("linux"):
                source = Path(directory) / "hang.c"
                source.write_text("__attribute__((constructor)) static void hang(void) { for (;;) {} }\n")
                subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(hang), str(source)], check=True)
            else:
                hang.write_bytes(b"placeholder: non-Linux hosts stop at the platform check\n")
            self.assert_failure({"GEARGRAFX_CORE": str(hang)}, "TimeoutExpired", 1, timeout=300)


if __name__ == "__main__":
    unittest.main()
