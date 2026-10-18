"""Positive/negative host tests for the slot-$01 non-executable fixtures (Issue #180).

Synthetic only: pure validation/preflight functions and the ``--check`` CLI. No emulator, no
reference BIOS read, no reference API JSR. A well-formed fixture must be ``NOT_RUN`` with
execution ``BLOCKED`` (exit 3); malformed, unknown-schema, ROM-unspecified, unsafe-I/O-allowing
and permission-granting inputs must fail closed; neither public CI nor any output may present
these cases as measured execution or as execution permission.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("cdreset_slot01_fixture")


def good_config(**overrides):
    config = {"schema": M.CONFIG_SCHEMA, "rom_path": "local/syscard3.pce", "rom_sha256": "a" * 64,
              "unsafe_io_allow": False, "side_effects_allowed": False, "execution_allowed": False,
              "safety_gate_approved": False}
    config.update(overrides)
    return config


class Slot01FixtureTests(unittest.TestCase):
    def run_cli(self, config=None):
        argv = [sys.executable, str(ROOT / "tools" / "cdreset_slot01_fixture.py"), "--check"]
        if config is not None:
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(config, handle)
            handle.close()
            self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
            argv += ["--config", handle.name]
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    def test_two_media_cases_are_definition_only_and_unknown(self):
        self.assertEqual({case["media"] for case in M.CASES}, {"present", "absent"})
        self.assertEqual(len(M.CASES), 2)
        for case in M.CASES:
            with self.subTest(case=case["case_id"]):
                self.assertEqual(M.validate_case(case), [])
                self.assertEqual((case["slot"], case["entry_candidate"], case["execution"]),
                                 ("$01", "$E003", "NOT_RUN"))
                values = [case[field] for field in M.EVIDENCE_FIELDS] + list(case["registers"].values())
                self.assertTrue(all(value == M.UNKNOWN for value in values))  # no guessed fields
        status, reasons = M.preflight(list(M.CASES), good_config())
        self.assertEqual(status, "NOT_RUN")
        joined = "; ".join(reasons)
        self.assertIn("safety_gate_not_approved", joined)
        self.assertIn("no execution path", joined)
        self.assertNotIn("MODEL_ELIGIBLE", joined)

    def test_rejected_inputs_fail_closed(self):
        missing = {key: value for key, value in M.CASES[0].items() if key != "irq"}
        bad_cases = {"field drop": missing,
                     "extra field": dict(M.CASES[0], instructions="STA $1804"),
                     "guessed result": dict(M.CASES[1], expected_result="returns to JSR+3"),
                     "register type": dict(M.CASES[1], registers={"A": 0}),
                     "unknown schema": dict(M.CASES[0], schema="oss-pce-cd-bios/other/v9"),
                     "wrong slot": dict(M.CASES[0], slot="$1E"),
                     "not a case": None}
        rejected_configs = ((None, "ROM path/sha256 unspecified"),
                            ({}, "invalid run config fields"),
                            (good_config(schema="oss-pce-cd-bios/cdreset-slot01-run-config/v2"), "unknown schema"),
                            (good_config(rom_path=""), "rom_path missing"),
                            (good_config(rom_sha256="A" * 64), "rom_sha256 missing"),
                            (good_config(unsafe_io_allow=True), "unsafe_io_allow"),
                            (good_config(side_effects_allowed=True), "side_effects_allowed"),
                            (good_config(execution_allowed=True), "execution_allowed"),
                            (good_config(safety_gate_approved="yes"), "safety_gate_approved"))
        for label, bad in bad_cases.items():
            with self.subTest(bad=label):
                status, reasons = M.preflight([bad], good_config())
                self.assertEqual(status, "BLOCKED")
                self.assertTrue(any("malformed_configuration" in reason for reason in reasons))
        for config, marker in rejected_configs:
            with self.subTest(config=marker):
                status, reasons = M.preflight(list(M.CASES), config)
                self.assertEqual(status, "BLOCKED")
                self.assertIn(marker, "; ".join(reasons))

    def test_gate_approval_and_model_eligible_never_grant_execution(self):
        # #191/#193: MODEL_ELIGIBLE is synthetic-model eligibility, never execution permission.
        safety = load("cdreset_safety_preflight")
        posture = dict(safety.CURRENT_POSTURE, hardware_page_policy="intercept",
                       st_instruction_intercept=True, irq_register_intercept=True,
                       entry_environment_reproduced=True)
        reviewed = {"status": "REVIEWED", "entry_environment": "KNOWN"}
        self.assertEqual(safety.decide(posture, reviewed)[0], "MODEL_ELIGIBLE")
        # Even with the safety gate approved this fixture has no execution path: NOT_RUN + BLOCKED.
        status, reasons = M.preflight(list(M.CASES), good_config(safety_gate_approved=True))
        self.assertEqual(status, "NOT_RUN")
        joined = "; ".join(reasons)
        self.assertIn("no execution path", joined)
        self.assertNotIn("safety_gate_not_approved", joined)
        self.assertNotIn("MODEL_ELIGIBLE", joined)
        self.assertEqual(self.run_cli(good_config(safety_gate_approved=True)).returncode, M.EXIT_BLOCKED)

    def test_public_ci_runs_only_synthetic_fixture_checks(self):
        for name in ("api-source.yml", "ci.yml", "nightly.yml", "pages.yml"):
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                # The fixture reaches public CI only as synthetic host unittest checks.
                self.assertNotIn("cdreset_slot01", text)
                self.assertNotIn("reference_bios_runner", text)
        source = (ROOT / "tools" / "cdreset_slot01_fixture.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "os.system", "popen(", ".read_bytes"):
            self.assertNotIn(banned, source)

    def test_cli_fails_closed_with_exit_3_and_no_measured_count_display(self):
        missing = self.run_cli()  # no run config: ROM path/sha256 unspecified
        self.assertEqual(missing.returncode, M.EXIT_BLOCKED, missing.stderr)
        self.assertTrue(missing.stdout.startswith("BLOCKED: "), missing.stdout)
        self.assertIn("ROM path/sha256 unspecified", missing.stdout)
        valid = self.run_cli(good_config())  # structure valid: NOT_RUN, execution still BLOCKED
        self.assertEqual(valid.returncode, M.EXIT_BLOCKED, valid.stderr)
        lines = valid.stdout.splitlines()
        self.assertTrue(lines[0].startswith("NOT_RUN: "), valid.stdout)
        self.assertTrue(lines[1].startswith("BLOCKED: "), valid.stdout)
        self.assertIn("safety_gate_not_approved", valid.stdout)
        # Neither permission, approval nor a measured-scenario count may ever be displayed.
        for result in (missing, valid):
            for banned in ("MODEL_ELIGIBLE", "ALLOWED", "270"):
                self.assertNotIn(banned, result.stdout)


if __name__ == "__main__":
    unittest.main()
