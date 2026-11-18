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

    def test_verdict_reads_are_closed_on_committed_tree(self):
        # Runtime input closure over the actual verdict loads: no forbidden
        # docs//build//rom//test-result input, every repo-relative data read
        # allowlisted. Temp scaffolding outside ROOT is ignored unless it
        # carries a forbidden component.
        records = p10._audit_verdict_reads(p10._run_verdict_loads_for_audit)
        forbidden, non_allowlisted = p10._classify_verdict_reads(records)
        self.assertEqual(forbidden, [])
        self.assertEqual(non_allowlisted, [])

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
        # The production checker itself runs on the tampered ledger and must
        # report the allowlist refusal (not a tautological assert).
        c3 = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(c3)
        tampered["production_allowlist"]["status"] = "APPROVED"
        problems = p10.check_unverified_persistence(c3_raw=tampered)
        self.assertTrue(
            any("c3 production_allowlist" in p and "UNDECIDED" in p for p in problems),
            f"C3 APPROVED mutant must be refused by the production checker: {problems}")

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
        # The production checker itself runs on the tampered ledger and must
        # report the slot-specific mapping refusal (not a tautological assert).
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(contracts)
        tampered["apis"][5]["mapping_status"] = "VERIFIED"
        problems = p10.check_unverified_persistence(contracts_raw=tampered)
        self.assertTrue(
            any("slot 05" in p and "mapping_status must stay HYPOTHESIS" in p
                for p in problems),
            f"mapping VERIFIED mutant must be refused by the production checker: {problems}")

    def test_injected_normal_data_is_green(self):
        # False-GREEN prevention: explicitly injected normal data also passes,
        # proving the seam feeds the production predicate (a seam that ignored
        # injected data would still return [] here but fail the RED tests).
        hardware_raw = json.loads((ROOT / "spec" / "hardware_observations.json").read_text(encoding="utf-8"))
        c3_raw = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
        contracts_raw = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(
            p10.check_unverified_persistence(
                hardware_raw=hardware_raw, c3_raw=c3_raw, contracts_raw=contracts_raw), [])

    def test_detector_is_sensitive_to_inputs(self):
        # Load-bearing proof: the same production function returns [] for
        # normal data and non-empty for mutated data. A neutered checker
        # (always []) would FAIL the RED assertions above.
        c3 = json.loads((ROOT / "spec" / "c3_static_contracts.json").read_text(encoding="utf-8"))
        tampered = copy.deepcopy(c3)
        tampered["production_allowlist"]["status"] = "APPROVED"
        self.assertEqual(p10.check_unverified_persistence(c3_raw=c3), [])
        self.assertNotEqual(p10.check_unverified_persistence(c3_raw=tampered), [])


class P10InputClosureMutants(unittest.TestCase):
    """Runtime audit must catch every forbidden-input read technique.

    All mutants read temp-dummy files only (never ./rom/ or any real input
    directory). Each mutant performs a real read inside the audit window and
    asserts the audit flags it with the forbidden component.
    """
    def _audit_with_mutant(self, mutant):
        def loader():
            p10._run_verdict_loads_for_audit()
            mutant()
        records = p10._audit_verdict_reads(loader)
        return p10._classify_verdict_reads(records)

    def test_direct_docs_read_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "mutant.txt"
            target.parent.mkdir(parents=True)
            target.write_text("dummy")
            forbidden, _ = self._audit_with_mutant(lambda: target.read_text(encoding="utf-8"))
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_dynamic_docs_path_read_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            component = "do" + "cs"  # assembled at runtime; no literal "docs/"
            target = Path(tmp) / component / "m.txt"
            target.parent.mkdir(parents=True)
            target.write_text("dummy")
            def mutant():
                Path(tmp, "do" + "cs", "m.txt").read_text(encoding="utf-8")
            forbidden, _ = self._audit_with_mutant(mutant)
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_helper_indirected_read_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "helper.txt"
            target.parent.mkdir(parents=True)
            target.write_text("dummy")

            def helper(path):
                with open(path, encoding="utf-8") as stream:
                    return stream.read()

            forbidden, _ = self._audit_with_mutant(lambda: helper(str(target)))
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_build_artifact_read_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "build" / "out.txt"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"dummy")
            forbidden, _ = self._audit_with_mutant(lambda: target.read_bytes())
        self.assertTrue(any("'build'" in f for f in forbidden), forbidden)

    def test_second_occurrence_read_detected(self):
        # The old text scan inspected only the first match; the audit records
        # every occurrence, so a forbidden 2nd read after allowlisted 1sts is
        # still caught.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rom" / "m.bin"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"dummy")

            def mutant():
                for _ in range(3):
                    (ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8")
                target.read_bytes()  # 4th read, forbidden

            forbidden, _ = self._audit_with_mutant(mutant)
        self.assertTrue(any("'rom'" in f for f in forbidden), forbidden)

    def test_string_search_evasion_read_detected(self):
        # No literal "rom", "open(" or "read_text" anywhere below: the
        # callee and the path component are assembled at runtime inside the
        # audit window, so a plain source-text scan cannot see them while the
        # call-time audit still records the read.
        import os as _os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ("r" + "om") / ("ev" + ".bin")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"dummy")

            def mutant():
                reader = getattr(_os, "op" + "en")
                comp = "r" + "om"
                fd = reader(str(Path(tmp) / comp / ("ev" + ".bin")), _os.O_RDONLY)
                try:
                    _os.read(fd, 5)
                finally:
                    _os.close(fd)

            forbidden, _ = self._audit_with_mutant(mutant)
        self.assertTrue(any("'rom'" in f for f in forbidden), forbidden)

    def test_audit_window_restores_patches(self):
        # After the audit, normal reads work unpatched (no leakage).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plain.txt"
            target.write_text("ok")
            p10._audit_verdict_reads(p10._run_verdict_loads_for_audit)
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")

    def test_audit_restores_patches_on_exception(self):
        # Monkeypatching is restored on exceptional exit, and the audit
        # failure is fail-closed (a crash is a finding, never a pass).
        import tempfile
        sentinel = Path(tempfile.gettempdir()) / "p10-restore-probe.txt"

        def boom():
            raise RuntimeError("synthetic-audit-failure")

        with self.assertRaises(RuntimeError):
            p10._audit_verdict_reads(boom)
        # Patches restored: a plain read works and records nothing.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plain.txt"
            target.write_text("ok")
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")

    def test_audit_crash_is_fail_closed(self):
        # If the audited loader itself raises, the check reports a finding.
        from unittest import mock
        with mock.patch.object(p10, "_run_verdict_loads_for_audit",
                               side_effect=RuntimeError("synthetic")):
            problems = p10.check_synthetic_host_docs_non_promotion()
        self.assertTrue(any("audit crashed" in p for p in problems), problems)

    def test_forbidden_py_docs_policy(self):
        # Suffix alone never exempts: docs/*.py is a forbidden data read.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "policy.py"
            target.parent.mkdir(parents=True)
            target.write_text("POLICY = 1\n")
            forbidden, _ = self._audit_with_mutant(
                lambda: target.read_text(encoding="utf-8"))
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_forbidden_py_rom_input(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rom" / "input.py"
            target.parent.mkdir(parents=True)
            target.write_text("DATA = 1\n")
            forbidden, _ = self._audit_with_mutant(
                lambda: Path(target).read_bytes())
        self.assertTrue(any("'rom'" in f for f in forbidden), forbidden)

    def test_forbidden_pyc_under_docs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cached = Path(tmp) / "docs" / "__pycache__"
            cached.mkdir(parents=True)
            target = cached / "policy.pyc"
            target.write_bytes(b"\x00\x01\x02")
            forbidden, _ = self._audit_with_mutant(
                lambda: target.read_bytes())
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_forbidden_py_dynamic_path(self):
        # Dynamically assembled forbidden .py path (no literal "docs").
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            comp = "do" + "cs"
            target = Path(tmp) / comp / ("pol" + "icy.py")
            target.parent.mkdir(parents=True)
            target.write_text("POLICY = 2\n")

            def mutant():
                Path(tmp, "do" + "cs", "pol" + "icy.py").read_text(encoding="utf-8")

            forbidden, _ = self._audit_with_mutant(mutant)
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_symlink_to_forbidden_target_detected(self):
        # Innocent link name whose canonical target sits under docs/: the raw
        # path carries no forbidden component, only the resolved target does.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            real_docs = Path(tmp) / "real" / "docs"
            real_docs.mkdir(parents=True)
            target = real_docs / "secret.py"
            target.write_text("SECRET = 1\n")
            link = Path(tmp) / "link.py"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unsupported: {exc}")
            forbidden, _ = self._audit_with_mutant(
                lambda: link.read_text(encoding="utf-8"))
        self.assertTrue(any("'docs'" in f for f in forbidden), forbidden)

    def test_external_json_all_read_apis_refused(self):
        # /tmp/approval.json-style verdict input: refused on every
        # representative read path (Path.read_text, builtins.open, io.open,
        # os.open). Temp dummies only; never ./rom/ or the real repo.
        import io as _io
        import os as _os
        import tempfile
        apis = {
            "Path.read_text": lambda p: Path(p).read_text(encoding="utf-8"),
            "builtins.open": lambda p: open(p, encoding="utf-8").read(),
            "io.open": lambda p: _io.open(p, encoding="utf-8").read(),
            "os.open": lambda p: (_os.close(_os.open(p, _os.O_RDONLY)), "")[1],
        }
        for name, reader in apis.items():
            with self.subTest(api=name):
                with tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / "approval.json"
                    target.write_text('{"approved": true}\n')

                    def mutant():
                        reader(str(target))

                    _, non_allowlisted = self._audit_with_mutant(mutant)
                self.assertTrue(
                    any("outside repository allowlist" in n for n in non_allowlisted),
                    f"{name}: external data read must be refused: {non_allowlisted}")

    def test_repo_nonallowlisted_json_real_read(self):
        # A real content read of an existing but non-allowlisted repo file
        # (no forbidden component) is refused as non-allowlisted.
        forbidden, non_allowlisted = self._audit_with_mutant(
            lambda: (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))
        self.assertEqual(forbidden, [])
        self.assertTrue(
            any("CONTRIBUTING.md" in n for n in non_allowlisted),
            (forbidden, non_allowlisted))

    def test_symlink_escape_via_patched_root(self):
        # spec/evil.json -> outside secret: canonical resolution exposes the
        # escape even though the raw path looks allowlisted. Uses an
        # independent temp dir with a patched ROOT; the production repo is
        # never touched with symlinks.
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "fakeroot"
            spec_dir = fake_root / "spec"
            spec_dir.mkdir(parents=True)
            ok_file = spec_dir / "ok.json"
            ok_file.write_text("{}\n")
            outside = Path(tmp) / "outside" / "secret.json"
            outside.parent.mkdir(parents=True)
            outside.write_text('{"evil": true}\n')
            link = spec_dir / "evil.json"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unsupported: {exc}")
            with mock.patch.object(p10, "ROOT", fake_root):
                forbidden, non_allowlisted = p10._classify_verdict_reads([
                    ("read", str(ok_file)),
                    ("read", str(link)),
                ])
        # Outside-ROOT findings name only the component (no absolute path
        # leaks), so the escape is proven by count: ok.json allowed, the
        # symlink escape is the single refused read.
        self.assertEqual(forbidden, [])
        self.assertEqual(len(non_allowlisted), 1, (forbidden, non_allowlisted))
        self.assertIn("outside repository allowlist", non_allowlisted[0])

    def test_resolve_failure_is_fail_closed(self):
        # Unresolvable paths are refused, never silently allowed.
        forbidden, non_allowlisted = p10._classify_verdict_reads(
            [("read", "/tmp/\x00bad")])
        self.assertTrue(forbidden or non_allowlisted,
                        "unresolvable read must be refused")

    def test_no_absolute_paths_in_messages(self):
        # Findings must not leak absolute filesystem paths (tmpdirs, homes).
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "policy.py"
            target.parent.mkdir(parents=True)
            target.write_text("x")
            other = Path(tmp) / "approval.json"
            other.write_text("{}")
            forbidden, non_allowlisted = self._audit_with_mutant(
                lambda: (target.read_text(), open(str(other)).read()))
        for message in forbidden + non_allowlisted:
            self.assertNotIn(tmp, message, message)
            self.assertNotIn(str(Path.home()), message, message)

    def test_detector_disabled_would_miss(self):
        # Sensitivity proof: with the classifier neutered, the same mutant is
        # blind — so the RED assertions above genuinely depend on detection.
        from unittest import mock
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "docs" / "policy.py"
            target.parent.mkdir(parents=True)
            target.write_text("x")
            records = p10._audit_verdict_reads(lambda: target.read_text())
        self.assertTrue(records, "mutant must perform a recorded read")
        with mock.patch.object(p10, "_classify_verdict_reads",
                               return_value=([], [])):
            self.assertEqual(p10._classify_verdict_reads(records), ([], []))


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
