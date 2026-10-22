"""Real-core check of the EX_GETVER observation contract (Issue #474, Phase ABI-2).

Runs the committed core-harness plug-in fixture against the DEV ROM (our own build, never the
reference BIOS) on the pinned Geargrafx core, converts the evidence into observation records, and
requires every record to pass the validator. It then requires realistic mutations of those real
records to be refused. A missing core or ROM is a failure, never a skip.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
import getver_observation as g  # noqa: E402
from geargrafx_core_harness import parse_lbl  # noqa: E402

MODE = "debug"
BUILD = ROOT / "build" / MODE
DEV_ROM = BUILD / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
DEV_LABELS = BUILD / "bios-dev" / "dev.lbl"
EVIDENCE = BUILD / "core-harness" / "core-harness-evidence.json"
FIXTURE = ROOT / "tests" / "fixtures" / "core-harness-plugin.json"
FIXTURE_CASE = "plugin_ex_getver_expect_a_x"  # the case in FIXTURE; the harness also runs its own api_slot_1E self-case
HARNESS_FILES = (ROOT / "tests" / "integration" / "geargrafx_core_harness.py",
                 ROOT / "tests" / "fixtures" / "core-harness.s")
RECORDS = BUILD / "getver-observation" / "records.jsonl"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealCoreObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise AssertionError("BLOCKED: real-core observation needs the Linux-pinned Geargrafx core")
        if not DEV_ROM.exists():  # same steps as `make dev-rom`; never a skip
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_rom.py"), "--mode", MODE], check=True)
        completed = subprocess.run([sys.executable, str(ROOT / "tests" / "integration" / "geargrafx_core_harness.py"),
                                    "--mode", MODE, "--cases", str(FIXTURE)], capture_output=True, text=True)
        if completed.returncode != 0:
            raise AssertionError(f"core harness FAIL/BLOCKED:\n{completed.stdout}{completed.stderr}")
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        entry = parse_lbl(DEV_LABELS.read_text(encoding="utf-8"))["api_slot_1E"]
        cls.kwargs = dict(rom_sha256=_sha256(DEV_ROM), entry=entry,
                          core_revision=evidence["geargrafx_revision"],
                          harness_revision=hashlib.sha256(b"".join(p.read_bytes() for p in HARNESS_FILES)).hexdigest(),
                          step_budget=evidence["steps_per_cycle"])
        cls.records = g.from_core_evidence(evidence, case_name=FIXTURE_CASE, rom_sha256=cls.kwargs["rom_sha256"], entry=entry,
                                           harness_revision=cls.kwargs["harness_revision"],
                                           step_budget=cls.kwargs["step_budget"])
        RECORDS.parent.mkdir(parents=True, exist_ok=True)
        RECORDS.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in cls.records),
                           encoding="utf-8")

    def check(self, record):
        return g.validate(record, **self.kwargs)

    def test_real_core_runs_produce_cold_and_warm_records_that_validate(self):
        self.assertEqual(sorted(r["temperature"] for r in self.records), ["cold", "warm"])
        for record in self.records:
            self.assertEqual(self.check(record), "OBSERVED_SYNTHETIC")
            self.assertEqual(record["source"]["kind"], "SYNTHETIC_DEV_ROM")
            self.assertEqual(record["io"]["observation"], g.NOT_MEASURED)
        reloaded = g.load_jsonl(RECORDS.read_text(encoding="utf-8"))
        self.assertEqual(reloaded, self.records)

    def test_mutated_real_records_are_refused(self):
        base = self.records[1]

        def resealed(change):
            value = deepcopy(base)
            change(value)
            return g.seal(value)

        cases = {
            "ROM_MISMATCH": resealed(lambda r: r["source"].update(rom_sha256="0" * 64)),
            "ENTRY_MISMATCH": resealed(lambda r: r.update(entry=r["entry"] + 2)),
            "REFERENCE_UNAPPROVED": resealed(lambda r: r.update(source={"kind": "REFERENCE_ROM",
                                                                        "rom_sha256": self.kwargs["rom_sha256"]})),
            "MPR": resealed(lambda r: r["after"].update(mpr=[0xFF] + r["after"]["mpr"][1:7] + [0x01])),
            "STACK": resealed(lambda r: r["after"].update(sp=(r["after"]["sp"] - 1) & 0xFF)),
            "IO": resealed(lambda r: r["io"].update(observation=g.IO_MEASURED_NONE,
                                                    accesses=[{"addr": 0x0000, "kind": "read"}])),
            "BUDGET": resealed(lambda r: r.update(status="TIMEOUT")),
            "ABI_CLAIM": resealed(lambda r: r.update(abi_claim="VERIFIED")),
            "TAMPERED": dict(base, after=dict(base["after"], a=(base["after"]["a"] + 1) & 0xFF)),
        }
        for code, item in cases.items():
            with self.subTest(code=code), self.assertRaises(g.Refused) as caught:
                self.check(item)
            self.assertEqual(caught.exception.code, code)

    def test_decision_stays_blocked_for_real_synthetic_observations(self):
        decision = g.decide_execution({"verdict": "SAFE"}, self.records)
        self.assertEqual(decision["execution"], "BLOCKED")
        counts = g.abi_status(self.records)
        self.assertEqual(counts["reference_abi_verified"], 0)
        self.assertEqual(counts["reproducible_reference_observations"], 0)
        self.assertEqual(counts["synthetic_observations"], len(self.records))


if __name__ == "__main__":
    unittest.main()
