"""Positive/negative host tests for the CD_RESET pre-execution safety preflight (Issue #178).

These tests are synthetic only: they exercise the pure decision function in
``tools/cdreset_safety_preflight.py`` and its ``--check`` CLI. No emulator is launched, no
reference BIOS is read, and no reference API JSR is executed. The current posture/ABI must be
BLOCKED; a synthetic posture that covers every hazard with a reviewed ABI must be ALLOWED, so
the decision is falsifiable and not a tautology.
"""
import copy
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load("cdreset_safety_preflight")
REVIEWED = {"status": "REVIEWED", "entry_environment": "KNOWN"}


def complete_posture():
    """A synthetic posture that covers every hazard before a side effect."""
    return {
        "physical_bank_alias_rejected": True,
        "mpr_change_detected": True,
        "hardware_page_policy": "intercept",
        "st_instruction_intercept": True,
        "irq_register_intercept": True,
        "ram_write_allowlist_enforced": True,
        "instruction_budget_bounded": True,
        "entry_environment_reproduced": True,
    }


class CdResetSafetyPreflightTests(unittest.TestCase):
    def test_current_posture_and_unknown_abi_are_blocked(self):
        verdict, reasons = M.decide(M.CURRENT_POSTURE, M.CURRENT_ABI)
        self.assertEqual(verdict, "BLOCKED")
        joined = "; ".join(reasons)
        self.assertIn("abi_status_unknown", joined)
        self.assertIn("entry_environment_not_reproduced", joined)
        # H3 is a specific rejection reason in the current #170 posture (forbid $FF vs MPR0=$FF).
        self.assertIn("H3:", joined)
        # ST/IRQ/$1804 coverage is absent today: each is reported separately, never merged.
        for hazard in M.HAZARDS:
            with self.subTest(hazard=hazard.id):
                if not M.hazard_covered(hazard, M.CURRENT_POSTURE):
                    self.assertIn(hazard.id, joined)

    def test_complete_synthetic_posture_is_falsifiable(self):
        # If a posture could cover every hazard with a reviewed ABI, the decision is not a
        # tautology. This synthetic case must return MODEL_ELIGIBLE (not execution permission).
        verdict, reasons = M.decide(complete_posture(), REVIEWED)
        self.assertEqual((verdict, reasons), ("MODEL_ELIGIBLE", []))

    def test_model_eligible_is_not_execution_permission(self):
        # MODEL_ELIGIBLE is a synthetic-model result; this module never returns execution
        # permission ("ALLOWED"). Granting execution needs independent real-core enforcement plus
        # approved ABI evidence (Issue #179 / #173).
        verdict, reasons = M.decide(complete_posture(), REVIEWED)
        self.assertEqual(verdict, "MODEL_ELIGIBLE")
        self.assertNotEqual(verdict, "ALLOWED")
        self.assertEqual(reasons, [])
        rendered = M.format_verdict(verdict, reasons)
        self.assertIn("not execution permission", rendered)
        self.assertIn("Issue #179 / #173", rendered)

    def test_each_missing_capability_blocks_with_its_own_reason(self):
        mutations = {
            "H1": {"physical_bank_alias_rejected": False},
            "H2": {"mpr_change_detected": False},
            "H3": {"hardware_page_policy": "allow"},
            "H4": {"st_instruction_intercept": False},
            "H5": {"irq_register_intercept": False},
            "H6": {"ram_write_allowlist_enforced": False},
            "H7": {"instruction_budget_bounded": False},
        }
        self.assertEqual(set(mutations), {hazard.id for hazard in M.HAZARDS})
        for hazard_id, change in mutations.items():
            with self.subTest(hazard=hazard_id):
                verdict, reasons = M.decide(dict(complete_posture(), **change), REVIEWED)
                self.assertEqual(verdict, "BLOCKED")
                self.assertTrue(any(reason.startswith(hazard_id + ":") for reason in reasons))

    def test_unknown_abi_is_treated_as_worst_case(self):
        for abi in (M.CURRENT_ABI, {"status": "UNKNOWN", "entry_environment": "KNOWN"},
                    {"status": "REVIEWED", "entry_environment": "UNKNOWN"}):
            with self.subTest(abi=abi):
                verdict, reasons = M.decide(complete_posture(), abi)
                self.assertEqual(verdict, "BLOCKED")
                self.assertTrue(any("unknown" in reason for reason in reasons))

    def test_entry_environment_must_be_reproduced(self):
        posture = complete_posture()
        posture["entry_environment_reproduced"] = False
        verdict, reasons = M.decide(posture, REVIEWED)
        self.assertEqual(verdict, "BLOCKED")
        self.assertIn("entry_environment_not_reproduced", reasons)

    def test_forbid_policy_contradicts_reproduced_entry_environment(self):
        # The guard's only way to cover $1804 (forbid $FF) cannot coexist with the MPR0=$FF entry.
        posture = dict(complete_posture(), hardware_page_policy="forbid",
                       entry_environment_reproduced=True)
        verdict, reasons = M.decide(posture, REVIEWED)
        self.assertEqual(verdict, "BLOCKED")
        self.assertTrue(any(reason.startswith("H3:") and "contradiction" in reason for reason in reasons))

    def test_unsafe_io_allow_policy_is_rejected(self):
        # A configuration that permits unmonitored I/O must never be accepted.
        verdict, reasons = M.decide(dict(complete_posture(), hardware_page_policy="allow"), REVIEWED)
        self.assertEqual(verdict, "BLOCKED")
        self.assertTrue(any(reason.startswith("H3:") for reason in reasons))

    def test_malformed_postures_fail_closed(self):
        good = complete_posture()
        missing = copy.deepcopy(good)
        del missing["irq_register_intercept"]
        extra = copy.deepcopy(good)
        extra["allow_unmonitored_io"] = True
        bad_values = [None, [], "posture", 0]
        for bad in [missing, extra, *bad_values]:
            with self.subTest(bad=bad):
                self.assertEqual(M.decide(bad, REVIEWED)[0], "BLOCKED")
        for field, value in (("physical_bank_alias_rejected", 1), ("hardware_page_policy", "ALLOW"),
                             ("instruction_budget_bounded", "yes")):
            with self.subTest(field=field):
                verdict, reasons = M.decide(dict(good, **{field: value}), REVIEWED)
                self.assertEqual(verdict, "BLOCKED")
                self.assertTrue(any("malformed_configuration" in reason for reason in reasons))

    def test_malformed_abi_fails_closed(self):
        for bad in (None, {}, {"status": "REVIEWED"}, {"status": "VERIFIED", "entry_environment": "KNOWN"},
                    {"status": "UNKNOWN", "entry_environment": "unknown"},
                    {"status": "REVIEWED", "entry_environment": "KNOWN", "extra": 1}):
            with self.subTest(bad=bad):
                self.assertEqual(M.decide(complete_posture(), bad)[0], "BLOCKED")

    def test_hazard_table_is_distinct_and_references_the_existing_guard(self):
        self.assertEqual(len(M.HAZARDS), 7)
        self.assertEqual(len({hazard.id for hazard in M.HAZARDS}), 7)
        self.assertEqual(len({hazard.name for hazard in M.HAZARDS}), 7)
        for hazard in M.HAZARDS:
            with self.subTest(hazard=hazard.id):
                self.assertIn(hazard.posture_key, M.POSTURE_FIELDS)
                # #173/#170 already enforce these; this PR must not re-implement them.
                if hazard.id in ("H1", "H2", "H6", "H7"):
                    self.assertIn("#170", hazard.enforced_by)
                if hazard.id in ("H4", "H5"):
                    self.assertEqual(hazard.enforced_by, "NONE")

    def test_cli_check_prints_blocked_and_exits_3(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdreset_safety_preflight.py"),
                                 "--check"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, M.EXIT_BLOCKED, result.stderr)
        self.assertTrue(result.stdout.startswith("BLOCKED: "), result.stdout)
        self.assertIn("H4", result.stdout)
        self.assertIn("H5", result.stdout)

    def test_cli_requires_check(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdreset_safety_preflight.py")],
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
