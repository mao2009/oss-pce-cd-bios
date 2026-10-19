"""ex_bcdbin (Issue #71): host reference model and real Geargrafx run of src/ex_bcdbin.s.

The contract is this project's own spec (docs/ex-bcdbin.md), not the reference BIOS ABI.
The real-core class is skipped (SKIP, not PASS) when the pinned cc65 or Geargrafx core is absent.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from environment import core_path, executable  # noqa: E402

RESULT_BYTES = 4 * 256 + 1  # A, P, X, Y tables + final SP (tests/fixtures/bcdbin_harness.s)
ONES_CHECK = bytes.fromhex("c9 0a")  # CMP #10 in ex_bcdbin


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


class GeargrafxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.core, ca65, ld65 = core_path(), executable("ca65"), executable("ld65")
        except (ValueError, OSError) as error:
            raise unittest.SkipTest(f"SKIP real core: {error}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-bcdbin-")
        out = Path(cls.tmp.name)
        objects = []
        for source in (ROOT / "src/ex_bcdbin.s", ROOT / "tests/fixtures/bcdbin_harness.s"):
            objects.append(str(out / (source.stem + ".o")))
            subprocess.run([ca65, "--cpu", "huc6280", "-o", objects[-1], str(source)], check=True)
        cls.rom = out / "bcdbin-harness-not-bios.pce"
        subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/bcdbin.cfg"), "-o", str(cls.rom),
                        *objects], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_core(self, rom):
        code = ("import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "from geargrafx_smoke import execute; "
                "print(json.dumps(execute(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))))")
        result = subprocess.run([sys.executable, "-c", code, str(ROOT / "tests/integration"),
                                 str(self.core), str(rom), str(RESULT_BYTES)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError(f"Geargrafx run failed: {result.stderr[-2000:]}")
        observations = json.loads(result.stdout)["cold_and_warm_reset_ram"]
        self.assertEqual(len(observations), 2)
        return [bytes.fromhex(item)[6:] for item in observations]

    def test_all_256_inputs_cold_and_warm(self):
        for ram in self.run_core(self.rom):
            check_ram(ram)

    def test_mutated_digit_check_is_rejected_on_core(self):
        data = self.rom.read_bytes()
        self.assertEqual(data.count(ONES_CHECK), 1)
        bad = Path(self.tmp.name) / "mutated.pce"
        bad.write_bytes(data.replace(ONES_CHECK, bytes.fromhex("c9 0b")))
        for ram in self.run_core(bad):
            with self.assertRaises(ValueError):
                check_ram(ram)


if __name__ == "__main__":
    unittest.main()
