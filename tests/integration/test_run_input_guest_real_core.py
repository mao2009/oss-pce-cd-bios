"""Synthetic HuCARD RUN controller-I/O check on the pinned real Geargrafx core.

The guest, not a tracer, polls $1000 and leaves only its own WRAM result block.
Input changes use GeargrafxCore::KeyPressed/KeyReleased.  These observations cover
the pinned emulator's controller path only; they are not hardware or BIOS evidence.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import executable  # noqa: E402
import geargrafx_api as api  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/run_input_guest.s"
CFG = ROOT / "tests/fixtures/caller_stack_contract.cfg"
CASES = {
    "no-input": "-",
    "one-frame-hold": "1p,2r",
    "two-frame-hold": "1p,3r",
    "multi-frame-hold": "1p,5r",
    "release-repress": "1p,3r,4p,6r",
}
FRAMES = 8


def build_rom(folder):
    obj, rom = folder / "run-input.o", folder / "run-input.pce"
    subprocess.run([str(executable("ca65")), "--cpu", "huc6280", "-o", str(obj), str(FIXTURE)], check=True)
    subprocess.run([str(executable("ld65")), "-C", str(CFG), "-o", str(rom), str(obj)], check=True)
    if rom.stat().st_size != 8192:
        raise ValueError("RUN fixture is not an 8 KiB HuCARD")
    return rom


def run_case(probe, rom, cycle, schedule):
    completed = subprocess.run([str(probe), str(rom), cycle, str(FRAMES), schedule], check=True,
                               capture_output=True, text=True, timeout=120)
    rows = [json.loads(line[7:]) for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    if len(rows) != 1 or rows[0].get("cycle") != cycle or rows[0].get("frames") != FRAMES:
        raise ValueError("missing or malformed fixture result")
    result = bytes.fromhex(rows[0].get("result_hex", ""))
    if len(result) != 16:
        raise ValueError("fixture result is not exactly 16 bytes")
    return result


def u32(result, offset):
    return int.from_bytes(result[offset:offset + 4], "little")


class RunInputGuestRealCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-run-input-guest-")
        folder = Path(cls.tmp.name)
        rom = build_rom(folder)
        probe = api.compile_probe(folder, source_name="geargrafx_run_input_guest.cpp",
                                  target_name="geargrafx-run-input-guest")
        cls.results = {cycle: {name: run_case(probe, rom, cycle, schedule) for name, schedule in CASES.items()}
                       for cycle in ("cold", "warm")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_input_has_no_pressed_polls_or_transitions(self):
        for cycle in self.results:
            result = self.results[cycle]["no-input"]
            self.assertGreater(u32(result, 0), 0)
            self.assertEqual(result[4:6], b"\0\0")
            self.assertGreater(u32(result, 6), 0)
            self.assertEqual(u32(result, 10), 0)
            self.assertEqual(result[14], 0)

    def test_holds_are_observed_and_grow_with_scheduled_duration(self):
        for cycle in self.results:
            counts = [u32(self.results[cycle][name], 10) for name in
                      ("one-frame-hold", "two-frame-hold", "multi-frame-hold")]
            self.assertTrue(all(count > 0 for count in counts))
            self.assertLess(counts[0], counts[1])
            self.assertLess(counts[1], counts[2])
            for name in ("one-frame-hold", "two-frame-hold", "multi-frame-hold"):
                result = self.results[cycle][name]
                self.assertEqual(result[14], 1, name)
                self.assertEqual(result[15], 1, name)

    def test_release_and_repress_records_two_press_and_release_transitions(self):
        for cycle in self.results:
            result = self.results[cycle]["release-repress"]
            self.assertEqual(result[14], 2)
            self.assertEqual(result[15], 2)
            self.assertGreater(u32(result, 10), 0)

    def test_cold_and_warm_have_the_same_fixture_classification(self):
        for name in CASES:
            cold, warm = self.results["cold"][name], self.results["warm"][name]
            self.assertEqual(cold[4:6], warm[4:6], name)
            self.assertEqual(cold[14:16], warm[14:16], name)


if __name__ == "__main__":
    unittest.main()
