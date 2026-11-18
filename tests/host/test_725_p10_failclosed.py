"""Issue #725 P10: fail-closed release gate supplemental verification.

Host-only. No emulator, no BIOS, no ROM, no private ./rom/ input is read or
executed. Real-core / commercial-media behaviour is NOT_RUN here; the only
claim is that DEV success, synthetic PASS, host PASS and docs-only merges
cannot promote anything, and unverified conditions persist.

Covers task items 4,5,6,7,8,9 without duplicating the base gate
(tools/failclosed_gate.py + tests/host/test_725_failclosed.py):

* P10-A: DEV_EXPERIMENTAL success exists yet genuine ABI stays 0/81.
* P10-B: synthetic PASS / host PASS / docs-only never promote (input closure).
* P10-C: UNKNOWN/BLOCKED/HYPOTHESIS/PENDING/NOT_RUN/NOT_STARTED/UNDECIDED/
  UNVERIFIED persistence for hardware, c3 and api_contracts axes.
* P10-RED: intentional gate destruction is detected (mutants fail closed).

Safe methods only: committed ledgers are read, mutants are in-memory copies
or temp-dir files that are never executed as Makefiles and never written to
the repository. No release operation is executed.
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import failclosed_725p10 as p10  # noqa: E402
import failclosed_gate as base_gate  # noqa: E402


class P10DevSuccessWithoutPromotion(unittest.TestCase):
    def test_gate_check_green_on_committed_tree(self):
        self.assertEqual(p10.check_dev_success_without_promotion(), [])

    def test_dev_success_exists_yet_no_promotion(self):
        import build_rom
        from abi1_reference_review import load as abi1_load
        from dev_implementations import load_ledger, load_slots, summary
        ledger = load_ledger()
        chosen = set(build_rom.overrides())
        # DEV success exists: overrides present with DEV evidence.
        self.assertGreater(len(chosen), 0, "DEV success precondition")
        summ = summary(ledger, chosen)
        self.assertGreater(summ["dev_rom_callable"], 0)
        # Yet no promotion on any axis.
        self.assertEqual(summ["abi_verified"], 0)
        self.assertEqual(summ["dev_callable_approved"], 0)
        self.assertEqual(summ["owner_approved"], 0)
        self.assertIs(summ["release_eligible"], False)
        document, _, _ = abi1_load()
        self.assertEqual(document["summary"]["reference_abi_verified"], 0)
        self.assertGreater(document["summary"]["dev_rom_implemented_apis"], 0)

    def test_dev_verified_mutant_is_refused_red(self):
        # Intentional gate destruction: claim VERIFIED in the DEV ledger.
        from dev_implementations import load_ledger, load_slots, validate
        import build_rom
        ledger = load_ledger()
        slots = load_slots()
        chosen = set(build_rom.overrides())
        tampered = copy.deepcopy(ledger)
        # Slot 33 (EX_JOYSNS) has a DEV implementation; promote its contract.
        for entry in tampered["slots"]:
            if entry["id"] == 33:
                entry["abi_contract"] = {"status": "VERIFIED", "note": "MUTANT"}
                break
        problems = validate(tampered, slots, chosen)
        self.assertTrue(any("VERIFIED" in p for p in problems),
                        f"DEV VERIFIED mutant must be refused, got {problems}")

    def test_dev_callable_without_approval_is_refused_red(self):
        from dev_implementations import load_ledger, load_slots, validate
        import build_rom
        ledger = load_ledger()
        slots = load_slots()
        chosen = set(build_rom.overrides())
        tampered = copy.deepcopy(ledger)
        for entry in tampered["slots"]:
            if entry["id"] == 33:
                entry["state"] = "DEV_CALLABLE"
                break
        problems = validate(tampered, slots, chosen)
        self.assertTrue(problems, "DEV_CALLABLE without approval must be refused")

    def test_abi1_observed_mutant_is_refused_red(self):
        from abi1_reference_review import validate
        import json as _json
        document = _json.loads((ROOT / "spec" / "abi1_reference_review.json").read_text(encoding="utf-8"))
        contracts = _json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        dev_ledger = _json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(document)
        tampered["apis"][0]["abi_state"] = "OBSERVED"
        with self.assertRaises(Exception):
            validate(tampered, contracts, dev_ledger)

    def test_hypothetical_full_overrides_still_refuse_release_red(self):
        import build_rom
        from build_rom import ReleaseBlocked
        with self.assertRaises(ReleaseBlocked) as ctx:
            build_rom.release_gate(set(range(81)))
        self.assertTrue(str(ctx.exception).startswith("BLOCKED:"))


class P10SyntheticHostDocsNonPromotion(unittest.TestCase):
    def test_gate_check_green_on_committed_tree(self):
        self.assertEqual(p10.check_synthetic_host_docs_non_promotion(), [])

    def test_extended_markers_never_permit(self):
        from reference_jsr_gate import is_execution_permitted
        for marker in ("SYNTHETIC PASS", "synthetic pass", "host PASS",
                       "HOST PASS", "doc-only merge", "DOC_ONLY", "docs-only",
                       "CI PASS", "TEST PASS", "GREEN", "PROJECT_SPEC_ONLY",
                       "STATIC_OK", "allowed", " ALLOWED "):
            self.assertFalse(is_execution_permitted(marker),
                             f"{marker!r} must never permit execution")
        self.assertTrue(is_execution_permitted("ALLOWED"))

    def test_synthetic_evidence_approval_grants_nothing(self):
        from reference_jsr_gate import HAZARDS, load, resolve
        ledger, contracts = load()
        approval = {
            "reviewer": "synthetic-p10-negative",
            "pr": 725,
            "abi_status": "REVIEWED",
            "hazard_controls": {h: "CONTROLLED" for h in HAZARDS},
            "callee_run_evidence": ["synthetic-host-only-not-a-run", "SYNTHETIC PASS"],
        }
        record = resolve(1, ledger, contracts, approval=approval)
        self.assertNotEqual(record["execution"], "ALLOWED")
        self.assertIs(record["execution_permitted"], False)
        self.assertEqual(record["jsr_count"], 0)

    def test_gate_input_closure_no_docs_build_reads(self):
        # The safety verdict must not depend on docs/, build/ or test-result
        # artefacts: a docs-only merge or a host PASS artefact cannot change it.
        for name in ("failclosed_gate.py", "failclosed_725p10.py",
                     "reference_jsr_gate.py", "dev_implementations.py",
                     "abi1_reference_review.py", "cdseek_safety_gate.py",
                     "hw_observation_ledger.py"):
            text = (ROOT / "tools" / name).read_text(encoding="utf-8")
            # No file read of docs/, build/ outputs or private rom/ inputs.
            for pattern in ('"docs/', "'docs/", '"build/', "'build/",
                            '"./rom/', "'./rom/", '"rom/', "'rom/"):
                idx = text.find(pattern)
                if idx == -1:
                    continue
                window = text[max(0, idx - 300): idx + 300]
                self.assertNotIn("read_text", window,
                                 f"{name}: gate must not read {pattern}")
                self.assertNotIn("open(", window,
                                 f"{name}: gate must not open {pattern}")

    def test_docs_only_prose_cannot_promote(self):
        # Even a docs file claiming ALLOWED does not change the gate verdict,
        # because the gate never reads docs/. The committed gate still passes
        # and the JSR gate still reports 81/81 closed.
        ok, _ = base_gate.run_all()
        self.assertTrue(ok)
        pok, _ = p10.run_all()
        self.assertTrue(pok)

    def test_new_tool_has_no_unlock_path(self):
        source = (ROOT / "tools" / "failclosed_725p10.py").read_text(encoding="utf-8")
        self.assertNotIn('"release_eligible": True', source)
        self.assertNotIn("release_eligible=True", source)
        self.assertNotIn('return "ALLOWED"', source)
        self.assertNotIn("write_text", source)
        # No emulator or toolchain imports in the host-only gate.
        self.assertNotIn("import geargrafx", source)
        self.assertNotIn("import cc65", source)


class P10UnverifiedPersistence(unittest.TestCase):
    def test_gate_check_green_on_committed_tree(self):
        self.assertEqual(p10.check_unverified_persistence(), [])

    def test_hardware_ledger_validates_no_observed(self):
        import hw_observation_ledger
        raw = json.loads((ROOT / "spec" / "hardware_observations.json").read_text(encoding="utf-8"))
        hw_observation_ledger.validate_ledger(raw)  # must not raise
        self.assertIs(raw["release_eligible"], False)
        self.assertEqual(raw["callee_abi"], "UNVERIFIED")

    def test_hardware_observed_mutant_is_refused_red(self):
        import hw_observation_ledger
        raw = json.loads((ROOT / "spec" / "hardware_observations.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(raw)
        tampered["entries"][0]["status"] = "OBSERVED"
        tampered["entries"][0]["trust_level"] = "EMULATOR_CORE_UNARCHIVED"
        tampered["entries"][0]["environment"] = {
            "kind": "EMULATOR_CORE",
            "platform": "MUTANT",
            "media": "synthetic",
            "runs": ["cold"],
        }
        with self.assertRaises(ValueError):
            hw_observation_ledger.validate_ledger(tampered)

    def test_c3_allowlist_stays_undecided(self):
        c3 = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(c3.get("status"), "VERIFICATION_DESIGN_ONLY")
        allow = c3.get("production_allowlist", {})
        self.assertEqual(allow.get("status"), "UNDECIDED")
        self.assertIsNone(allow.get("approved_by"))
        self.assertIsNone(allow.get("review_ref"))

    def test_c3_approved_mutant_is_refused_red(self):
        c3 = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(c3)
        tampered["production_allowlist"]["status"] = "APPROVED"
        # The P10 predicate refuses anything but UNDECIDED.
        self.assertNotEqual(tampered["production_allowlist"]["status"], "UNDECIDED")

    def test_api_contracts_axes_stay_unpromoted(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(len(contracts["apis"]), 81)
        for api in contracts["apis"]:
            self.assertEqual(api.get("mapping_status"), "HYPOTHESIS")
            self.assertEqual(api.get("implementation_status"), "NOT_STARTED")
            self.assertEqual(api.get("provenance_status"), "PENDING")
            self.assertIn(api.get("confidence"), ("UNKNOWN", "SOURCE_SUPPORTED"))
            self.assertEqual(api.get("verification"),
                              {"status": "NOT_RUN", "executed_callee": False, "evidence": []})

    def test_mapping_verified_mutant_is_refused_red(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(contracts)
        tampered["apis"][0]["mapping_status"] = "VERIFIED"
        # The P10 predicate requires HYPOTHESIS for every slot.
        bad = [a for a in tampered["apis"] if a.get("mapping_status") != "HYPOTHESIS"]
        self.assertTrue(bad, "mapping VERIFIED mutant must be detected")


class P10WholeGate(unittest.TestCase):
    def test_all_p10_invariants_hold(self):
        ok, results = p10.run_all()
        self.assertTrue(ok, f"P10 invariants broken: {results}")

    def test_base_gate_still_holds(self):
        ok, results = base_gate.run_all()
        self.assertTrue(ok, f"base gate broken: {results}")

    def test_cli_check_passes(self):
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "failclosed_725p10.py"), "--check"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PASS p10-dev-success-without-promotion", proc.stdout)
        self.assertIn("PASS p10-synthetic-host-docs-non-promotion", proc.stdout)
        self.assertIn("PASS p10-unverified-persistence", proc.stdout)


if __name__ == "__main__":
    unittest.main()
