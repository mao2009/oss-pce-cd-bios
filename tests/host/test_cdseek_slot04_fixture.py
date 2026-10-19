"""Positive/negative host tests for the slot-$04 (CD_SEEK) non-executable fixtures (Issue #216).

Synthetic only: pure validation/preflight functions and the ``--check`` CLI. No emulator, no
reference BIOS read, no reference API JSR. Every well-formed case must be ``BLOCKED_ABI`` (ABI
unreviewed), ROM mismatch and hardware-I/O requests must be ``REJECTED``, and malformed, unsafe or
permission-granting inputs must fail closed. Nothing may be presented as execution or permission.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cdseek_slot04_fixture", ROOT / "tools" / "cdseek_slot04_fixture.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

HASH_A, HASH_B = "a" * 64, "b" * 64


def good_config(**overrides):
    config = {"schema": M.CONFIG_SCHEMA, "rom_path": "local/syscard3.pce", "rom_sha256": HASH_A,
              "approved_rom_sha256": None, "unsafe_io_allow": False, "side_effects_allowed": False,
              "execution_allowed": False, "safety_gate_approved": False}
    config.update(overrides)
    return config


def approved_config(**overrides):
    return good_config(approved_rom_sha256=HASH_A, safety_gate_approved=True, **overrides)


def case_with(index=0, **overrides):
    case = dict(M.CASES[index], **overrides)
    return case


class Slot04FixtureTests(unittest.TestCase):
    def run_cli(self, config=None):
        argv = [sys.executable, str(ROOT / "tools" / "cdseek_slot04_fixture.py"), "--check"]
        if config is not None:
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(config, handle)
            handle.close()
            self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
            argv += ["--config", handle.name]
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    def test_four_cases_are_definition_only_and_unknown(self):
        self.assertEqual(sorted(case["category"] for case in M.CASES), sorted(M.CATEGORIES))
        self.assertEqual({case["media"] for case in M.CASES}, {"present", "absent"})
        for case in M.CASES:
            with self.subTest(case=case["case_id"]):
                self.assertEqual(M.validate_case(case), [])
                self.assertEqual((case["slot"], case["entry_candidate"], case["abi_status"],
                                  case["execution"]), ("$04", "$E00C", "BLOCKED_ABI", "NOT_RUN"))
                values = [case[field] for field in M.EVIDENCE_FIELDS] + list(case["registers"].values())
                self.assertTrue(all(value == M.UNKNOWN for value in values))  # no guessed fields
                self.assertEqual(case["io_writes"], M.UNKNOWN)
        status, reasons = M.preflight(list(M.CASES), good_config())
        self.assertEqual(status, "BLOCKED_ABI")
        joined = "; ".join(reasons)
        self.assertIn("PR #214", joined)
        self.assertIn("safety_gate_not_approved", joined)
        self.assertIn("rom_not_approved", joined)
        self.assertIn("no execution path", joined)

    def test_malformed_inputs_fail_closed_as_blocked(self):
        missing = {key: value for key, value in M.CASES[0].items() if key != "irq"}
        bad_cases = {"field drop": missing,
                     "extra field": dict(M.CASES[0], instructions="JSR $E00C"),
                     "guessed position": dict(M.CASES[2], position="0x000100"),
                     "guessed result": dict(M.CASES[1], expected_result="returns to JSR+3"),
                     "register type": dict(M.CASES[1], registers={"A": 0}),
                     "unknown schema": dict(M.CASES[0], schema="oss-pce-cd-bios/other/v9"),
                     "wrong slot": dict(M.CASES[0], slot="$4"),
                     "wrong entry": dict(M.CASES[0], entry_candidate="$E003"),
                     "abi asserted": dict(M.CASES[0], abi_status="READY"),
                     "bad io type": dict(M.CASES[0], io_writes="ALL"),
                     "not a case": None}
        for label, bad in bad_cases.items():
            with self.subTest(bad=label):
                status, reasons = M.preflight([bad], good_config())
                self.assertEqual(status, "BLOCKED")
                self.assertTrue(any("malformed_configuration" in reason for reason in reasons))
        rejected_configs = ((None, "ROM path/sha256 unspecified"),
                            ({}, "invalid run config fields"),
                            (good_config(schema="oss-pce-cd-bios/cdseek-slot04-run-config/v2"), "unknown schema"),
                            (good_config(rom_path=""), "rom_path missing"),
                            (good_config(rom_sha256="A" * 64), "rom_sha256 missing"),
                            (good_config(approved_rom_sha256="zz"), "approved_rom_sha256"),
                            (good_config(safety_gate_approved="yes"), "safety_gate_approved"))
        for config, marker in rejected_configs:
            with self.subTest(config=marker):
                status, reasons = M.preflight(list(M.CASES), config)
                self.assertEqual(status, "BLOCKED")
                self.assertIn(marker, "; ".join(reasons))

    def test_rom_mismatch_is_rejected_and_match_is_not_execution(self):
        status, reasons = M.preflight(list(M.CASES), good_config(approved_rom_sha256=HASH_B))
        self.assertEqual(status, "REJECTED")
        self.assertIn("rom_sha256_mismatch", "; ".join(reasons))
        status, _ = M.preflight(list(M.CASES), good_config(approved_rom_sha256=HASH_A))
        self.assertEqual(status, "BLOCKED_ABI")  # matching hash still never permits execution

    def test_unsafe_permissions_and_hardware_io_are_rejected(self):
        for field in M.PERMISSION_FIELDS:
            with self.subTest(permission=field):
                status, reasons = M.preflight(list(M.CASES), approved_config(**{field: True}))
                self.assertEqual(status, "REJECTED")
                self.assertIn(field, "; ".join(reasons))
        for address in (0x1800, 0x1804, 0x1BFF):
            with self.subTest(io=hex(address)):
                cases = [case_with(io_writes=[address])]
                status, reasons = M.preflight(cases, approved_config())
                self.assertEqual(status, "REJECTED")
                self.assertIn("hardware I/O page", "; ".join(reasons))
        status, _ = M.preflight([case_with(io_writes=[0x2000])], approved_config())
        self.assertEqual(status, "BLOCKED_ABI")  # non-hardware-page list is only unknown, not refused

    def test_gate_approval_and_matching_rom_never_grant_execution(self):
        status, reasons = M.preflight(list(M.CASES), approved_config())
        self.assertEqual(status, "BLOCKED_ABI")
        joined = "; ".join(reasons)
        self.assertIn("no execution path", joined)
        self.assertNotIn("safety_gate_not_approved", joined)
        self.assertNotIn("rom_not_approved", joined)
        self.assertNotIn("PASS", joined)
        self.assertNotIn("MODEL_ELIGIBLE", joined)
        self.assertEqual(self.run_cli(approved_config()).returncode, M.EXIT_FAIL_CLOSED)

    def test_rom_file_is_never_read(self):
        # A non-existent ROM path still yields a verdict: the bytes are never opened.
        result = self.run_cli(approved_config(rom_path="local/does-not-exist.pce"))
        self.assertEqual(result.returncode, M.EXIT_FAIL_CLOSED, result.stderr)
        self.assertTrue(result.stdout.startswith("BLOCKED_ABI: "), result.stdout)

    def test_public_ci_runs_only_synthetic_fixture_checks(self):
        for name in ("api-source.yml", "ci.yml", "nightly.yml", "pages.yml"):
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                self.assertNotIn("cdseek_slot04", text)
                self.assertNotIn("reference_bios_runner", text)
        source = (ROOT / "tools" / "cdseek_slot04_fixture.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "os.system", "popen(", ".read_bytes", "open("):
            self.assertNotIn(banned, source)

    def test_cli_fails_closed_with_exit_3(self):
        missing = self.run_cli()  # no run config: ROM path/sha256 unspecified
        self.assertEqual(missing.returncode, M.EXIT_FAIL_CLOSED, missing.stderr)
        self.assertTrue(missing.stdout.startswith("BLOCKED: "), missing.stdout)
        self.assertIn("ROM path/sha256 unspecified", missing.stdout)
        valid = self.run_cli(good_config())  # structure valid, ABI unreviewed: BLOCKED_ABI
        self.assertEqual(valid.returncode, M.EXIT_FAIL_CLOSED, valid.stderr)
        self.assertTrue(valid.stdout.startswith("BLOCKED_ABI: "), valid.stdout)
        for result in (missing, valid):
            for banned in ("MODEL_ELIGIBLE", "ALLOWED", "PASS", "VERIFIED", "270"):
                self.assertNotIn(banned, result.stdout)


if __name__ == "__main__":
    unittest.main()
