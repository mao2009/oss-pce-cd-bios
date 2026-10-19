"""Opt-in real-core run of src/ex_bcdbin.s (Issue #71). Linux only; NOT part of host tests.

Run: python3 tests/integration/geargrafx_bcdbin.py   (pinned cc65 + Geargrafx core via make setup)
Off Linux, or when the pinned tools/core are absent, the run is BLOCKED: exit 3 (same as
tests/integration/geargrafx_harness.py EXIT["BLOCKED"]). Other failures exit 1. Never SKIP, never PASS.
Project-own contract only; not a public BIOS API check.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests/host"))
from environment import core_path, executable  # noqa: E402
from test_ex_bcdbin import RESULT_BYTES, check_ram  # noqa: E402

BLOCKED_EXIT = 3
BLOCKED_TAG = "BLOCKED real core"

# (name, original bytes in ex_bcdbin, mutated bytes): each must be caught by check_ram on the core
MUTATIONS = (("ones check", "c9 0a", "c9 0b"), ("tens check", "c9 a0", "c9 b0"),
             ("tens weight", "69 0a", "69 0b"))


class GeargrafxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise RuntimeError(f"{BLOCKED_TAG}: Linux-pinned target required")
        try:
            cls.core, ca65, ld65 = core_path(), executable("ca65"), executable("ld65")
        except (ValueError, OSError) as error:
            raise RuntimeError(f"{BLOCKED_TAG}: {error}") from error
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-bcdbin-")
        try:
            out = Path(cls.tmp.name)
            objects = []
            for source in (ROOT / "src/ex_bcdbin.s", ROOT / "tests/fixtures/bcdbin_harness.s"):
                objects.append(str(out / (source.stem + ".o")))
                subprocess.run([ca65, "--cpu", "huc6280", "-o", objects[-1], str(source)], check=True)
            cls.rom = out / "bcdbin-harness-not-bios.pce"
            subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/bcdbin.cfg"), "-o", str(cls.rom),
                            *objects], check=True)
        except BaseException:
            cls.tmp.cleanup()
            raise

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

    def test_mutations_are_rejected_on_core(self):
        # Positive control first: the unmutated ROM must pass on the real core, or no mutation
        # result below means anything. A failure here is an error, never a PASS.
        for ram in self.run_core(self.rom):
            check_ram(ram)
        data = self.rom.read_bytes()
        for name, old, new in MUTATIONS:
            with self.subTest(name):
                self.assertEqual(data.count(bytes.fromhex(old)), 1)
                bad = Path(self.tmp.name) / "mutated.pce"
                bad.write_bytes(data.replace(bytes.fromhex(old), bytes.fromhex(new)))
                for ram in self.run_core(bad):
                    with self.assertRaises(ValueError):
                        check_ram(ram)


if __name__ == "__main__":
    outcome = unittest.main(exit=False).result
    if any(BLOCKED_TAG in text for _, text in outcome.errors + outcome.failures):
        sys.exit(BLOCKED_EXIT)
    sys.exit(0 if outcome.wasSuccessful() else 1)
