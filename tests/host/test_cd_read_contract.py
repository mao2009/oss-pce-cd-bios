"""Issue #12: CD_READ contract ledger + transport outcome model (host-only; real core is authoritative)."""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.cd_read_contract as cd_read_contract

from tools.cd_read_contract import (
    COMPAT_REASON, EXIT_BLOCKED, EXIT_LOCAL_ONLY, INTEGRATION, LBA_LIMIT, PINNED, ROOT, SECTOR_BYTES, TRANSPORT_CLAIMS, TRUST_LABEL,
    compat_status, effective_status, real_core_requests, transport_outcome, validate,
)
from tools.make_cd_image import DEFAULT_SECTORS

ROM_SHA = "ab" * 32


# The expected result kind per cited scenario, written independently of tools/cd_read_contract.py.
# Positive reads must be PASS; deliberate bad requests must be PASS_EXPECTED_FAILURE; the CD-DA read is an
# observation. test_scenario_kinds_match_checker and test_scenario_kinds_match_integration_source tie these
# to the checker and to the integration suite.
EXPECTED_KIND = {
    "single_sector_lba5": "PASS", "first_sector_lba0": "PASS", "last_sector_lba15": "PASS",
    "three_sectors_lba5": "PASS", "disc_end_boundary_lba13_x3": "PASS",
    "wrong_sector_requested_lba6": "PASS_EXPECTED_FAILURE",
    "invalid_sector_lba16": "PASS_EXPECTED_FAILURE", "invalid_sector_lba_max": "PASS_EXPECTED_FAILURE",
    "read_past_disc_end_lba14_x3": "PASS_EXPECTED_FAILURE",
    "read6_on_audio_track_lba": "OBSERVED_EMULATOR_BEHAVIOUR",
}


def bundle_for(rom_sha256=ROM_SHA, **overrides):
    """A bundle as tests/integration/geargrafx_cdread.py writes it after a full PASS run."""
    names = {s for c in TRANSPORT_CLAIMS if c["status"] == "VERIFIED" for s in c["scenarios"]}
    bundle = {"status": "PASS", "geargrafx_revision": PINNED["revision"], "source_archive_sha256": PINNED["sha256"],
              "rom_sha256": rom_sha256, "tests": {n: {"status": EXPECTED_KIND[n]} for n in names}}
    bundle.update(overrides)
    return bundle


class TransportOutcomeTests(unittest.TestCase):
    def test_positive_full_delivery(self):
        for lba, count in ((5, 1), (0, 1), (15, 1), (5, 3), (13, 3)):
            self.assertEqual(transport_outcome(16, lba, count), (count, False), (lba, count))

    def test_negative_start_at_or_beyond_disc_end(self):
        for lba in (16, 17, LBA_LIMIT - 1):
            self.assertEqual(transport_outcome(16, lba, 1), (0, True), lba)

    def test_boundary_run_off_end(self):
        self.assertEqual(transport_outcome(16, 14, 3), (2, True))
        self.assertEqual(transport_outcome(16, 15, 2), (1, True))
        self.assertEqual(transport_outcome(1, 0, 1), (1, False))

    def test_outside_executed_domain_is_refused_not_guessed(self):
        for disc, lba, count in ((0, 0, 1), (16, -1, 1), (16, LBA_LIMIT, 1), (16, 5, 0), (16, 5, 4), (16, 5, 256)):
            with self.subTest(disc=disc, lba=lba, count=count), self.assertRaises(ValueError):
                transport_outcome(disc, lba, count)

    def test_model_matches_every_real_core_request(self):
        rows = real_core_requests(INTEGRATION.read_text(encoding="utf-8"))
        names = {r[0] for r in rows}
        self.assertTrue({"single_sector_lba5", "disc_end_boundary_lba13_x3", "invalid_sector_lba16",
                         "invalid_sector_lba_max", "read_past_disc_end_lba14_x3"} <= names, names)
        for name, lba, count, transferred in rows:
            delivered, early = transport_outcome(DEFAULT_SECTORS, lba, count)
            if transferred is None:
                self.assertEqual((delivered, early), (count, False), name)
            else:
                self.assertEqual((delivered * SECTOR_BYTES, early), (transferred, True), name)

    def test_parser_sees_every_real_core_call_site(self):
        text = INTEGRATION.read_text(encoding="utf-8")
        positive_calls = len(re.findall(r"suite\.positive\(", text))
        end_of_disc_calls = sum("suite.failing_guest(" in line and ", 5, 5," in line for line in text.splitlines())
        self.assertEqual(len(real_core_requests(text)), positive_calls + end_of_disc_calls)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))

    def test_structure_and_callee_abi_are_unpromoted(self):
        validate(self.api)

    def test_source_text_and_missing_bundle_never_verify(self):
        status = effective_status(None, None)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["BLOCKED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_matching_bundle_is_local_only_never_verified(self):
        status = effective_status(bundle_for(), ROM_SHA)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["LOCAL_ONLY"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_bundle_from_other_rom_emulator_or_run_is_refused(self):
        cases = {
            "other_rom": (bundle_for(rom_sha256="cd" * 32), ROM_SHA),
            "rom_missing": (bundle_for(), None),
            "other_revision": (bundle_for(geargrafx_revision="0" * 40), ROM_SHA),
            "other_archive": (bundle_for(source_archive_sha256="0" * 64), ROM_SHA),
            "failed_run": (bundle_for(status="FAIL"), ROM_SHA),
            "no_bundle": (None, ROM_SHA),
        }
        for name, (bundle, rom) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(effective_status(bundle, rom)["T1"], "BLOCKED")

    def test_cited_scenario_missing_or_failing_downgrades_to_unknown(self):
        missing = bundle_for()
        del missing["tests"]["single_sector_lba5"]
        failing = bundle_for()
        failing["tests"]["invalid_sector_lba16"] = {"status": "FAIL"}
        self.assertEqual(effective_status(missing, ROM_SHA)["T1"], "UNKNOWN")
        self.assertEqual(effective_status(failing, ROM_SHA)["T3"], "UNKNOWN")

    def test_asserted_observation_counts_but_not_started_or_untested_does_not(self):
        observed = bundle_for()
        observed["tests"]["read6_on_audio_track_lba"] = {"status": "OBSERVED_EMULATOR_BEHAVIOUR"}
        self.assertEqual(effective_status(observed, ROM_SHA)["T5"], "LOCAL_ONLY")
        untested = bundle_for()
        untested["tests"]["read6_on_audio_track_lba"] = {"status": "NOT_TESTED"}
        self.assertEqual(effective_status(untested, ROM_SHA)["T5"], "UNKNOWN")

    def test_scenario_recorded_with_wrong_kind_is_not_verified(self):
        # A positive read rewritten as an expected failure, a deliberate bad request recorded as a plain PASS,
        # and a CD-DA observation recorded as a plain PASS must each leave their claim unverified.
        cases = {
            "positive_as_expected_failure": ("single_sector_lba5", "PASS_EXPECTED_FAILURE", "T1"),
            "negative_as_plain_pass": ("wrong_sector_requested_lba6", "PASS", "T1"),
            "bad_request_as_plain_pass": ("invalid_sector_lba16", "PASS", "T3"),
            "observation_as_plain_pass": ("read6_on_audio_track_lba", "PASS", "T5"),
        }
        for name, (scenario, kind, claim) in cases.items():
            with self.subTest(case=name):
                bundle = bundle_for()
                bundle["tests"][scenario] = {"status": kind}
                self.assertEqual(effective_status(bundle, ROM_SHA)[claim], "UNKNOWN")

    def test_scenario_kinds_match_checker(self):
        from tools.cd_read_contract import SCENARIO_KIND
        self.assertEqual(dict(SCENARIO_KIND), EXPECTED_KIND)
        for claim in TRANSPORT_CLAIMS:
            if claim["status"] == "VERIFIED":
                self.assertTrue(set(claim["scenarios"]) <= set(EXPECTED_KIND), claim["id"])

    def test_scenario_kinds_match_integration_source(self):
        # The integration suite decides each scenario's kind; the table must agree with its call sites.
        text = INTEGRATION.read_text(encoding="utf-8")
        declared = {}
        for call, name in re.findall(r'suite\.(positive|failing_guest|must_be_rejected|tampered_disc)\("(\w+)"', text):
            declared[name] = "PASS" if call == "positive" else "PASS_EXPECTED_FAILURE"
        declared.update(re.findall(r'results\["(\w+)"\] = \{\s*"status": "(\w+)"', text))
        for name, kind in EXPECTED_KIND.items():
            self.assertEqual(declared.get(name), kind, name)

    def test_malformed_bundle_is_blocked_not_an_exception(self):
        cases = {
            "test_entry_string": bundle_for(tests={"single_sector_lba5": "PASS"}),
            "test_entry_list": bundle_for(tests={"single_sector_lba5": ["PASS"]}),
            "bundle_list": [bundle_for()],
            "bundle_string": "PASS",
        }
        for name, bundle in cases.items():
            with self.subTest(case=name):
                self.assertEqual(effective_status(bundle, ROM_SHA)["T1"], "BLOCKED")

    def test_claim_citing_unrun_scenario_is_not_verified(self):
        claims = copy.deepcopy(TRANSPORT_CLAIMS)
        claims[0]["scenarios"] = ["no_such_run"]
        validate(self.api, claims)
        self.assertEqual(effective_status(bundle_for(), ROM_SHA, claims)["T1"], "UNKNOWN")

    def test_malformed_or_foreign_promotions_are_rejected(self):
        for change in ("unknown_to_verified", "bad_status", "foreign_evidence"):
            with self.subTest(change=change):
                c = copy.deepcopy(TRANSPORT_CLAIMS)
                if change == "unknown_to_verified": c[6]["status"] = "VERIFIED"
                if change == "bad_status": c[0]["status"] = "PASS"
                if change == "foreign_evidence": c[0]["evidence"] = "hugo bios.c HLE"
                with self.assertRaises(ValueError):
                    validate(self.api, c)

    def test_callee_abi_cannot_be_verified_or_implemented_silently(self):
        for change in ("verified_field", "executed", "implemented", "renamed"):
            with self.subTest(change=change):
                api = copy.deepcopy(self.api)
                slot = api["apis"][3]
                if change == "verified_field": slot["contract"]["arguments"]["status"] = "VERIFIED"
                if change == "executed": slot["verification"]["executed_callee"] = True
                if change == "implemented": slot["implementation_status"] = "DONE"
                if change == "renamed": slot["candidate_name"] = "CD_SEEK"
                with self.assertRaises(ValueError):
                    validate(api)


# Synthetic #125 inputs (no ROM, disc or BIOS data): an artifact shaped like tests/integration/geargrafx_cdread.py
# output, and gh outputs shaped like tests/host/test_evidence_provenance.py builds them.
GATE_REPO, GATE_COMMIT, RUN_ID = "example-owner/example-repo", "a" * 40, 4242
GATE_ROM = b"synthetic rom"
GATE_FIXTURE = {"bin_sha256": "e" * 64, "cue_sha256": "f" * 64}
SIGNER = ".github/workflows/api-source.yml"
MAIN_RULES = [{"type": "deletion"}, {"type": "non_fast_forward"},
              {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
              {"type": "required_status_checks"}]
DECLARED = {c["id"]: c["status"] for c in TRANSPORT_CLAIMS}
VERIFIED_IDS = [c["id"] for c in TRANSPORT_CLAIMS if c["status"] == "VERIFIED"]


def gate_artifact(**overrides):
    ep = cd_read_contract.ep
    artifact = {"status": "PASS", "geargrafx_revision": PINNED["revision"], "source_archive_sha256": PINNED["sha256"],
                "rom_sha256": hashlib.sha256(GATE_ROM).hexdigest(), "disc": dict(GATE_FIXTURE),
                "tests": {name: {"status": kind} for name, kind in ep.SCENARIO_KIND.items()},
                "primary_observations": {"RUN": [{"cycle": "cold", "reason": "cd_halt_ok"},
                                                 {"cycle": "warm", "reason": "cd_halt_ok"}]}}
    artifact.update(overrides)
    return json.dumps(artifact, sort_keys=True).encode()


def gate_attestation(artifact_bytes, rom=GATE_ROM, **cert):
    certificate = {
        "sourceRepositoryURI": f"https://github.com/{GATE_REPO}", "sourceRepositoryDigest": GATE_COMMIT,
        "sourceRepositoryRef": "refs/heads/main", "buildSignerDigest": GATE_COMMIT,
        "buildSignerURI": f"https://github.com/{GATE_REPO}/{SIGNER}@refs/heads/main",
        "githubWorkflowTrigger": "push", "runnerEnvironment": "github-hosted",
        "runInvocationURI": f"https://github.com/{GATE_REPO}/actions/runs/{RUN_ID}/attempts/1"}
    certificate.update(cert)
    subjects = [{"digest": {"sha256": hashlib.sha256(artifact_bytes).hexdigest()}},
                {"digest": {"sha256": hashlib.sha256(rom).hexdigest()}}]
    return [{"verificationResult": {"statement": {"subject": subjects}, "signature": {"certificate": certificate}}}]


def gate_run(**overrides):
    run = {"id": RUN_ID, "run_attempt": 1, "head_sha": GATE_COMMIT, "head_branch": "main", "event": "push",
           "path": SIGNER, "repository": {"full_name": GATE_REPO}, "head_repository": {"full_name": GATE_REPO}}
    run.update(overrides)
    return run


def gate_inputs(**overrides):
    artifact = gate_artifact()
    inputs = {"artifact_bytes": artifact, "attestation": gate_attestation(artifact), "rom_bytes": GATE_ROM,
              "run": gate_run(), "fixture": dict(GATE_FIXTURE), "repository": GATE_REPO, "commit": GATE_COMMIT,
              "branch_rules": copy.deepcopy(MAIN_RULES)}
    inputs.update(overrides)
    return inputs


class TrustBoundaryTests(unittest.TestCase):
    """compat_status() accepts only #125-attested evidence from protected main; everything else stays BLOCKED."""

    def assertBlocked(self, status):
        self.assertEqual([status[i] for i in VERIFIED_IDS], ["BLOCKED"] * len(VERIFIED_IDS), status)
        self.assertEqual({k: v for k, v in status.items() if k not in VERIFIED_IDS},
                         {k: v for k, v in DECLARED.items() if k not in VERIFIED_IDS})

    def test_attested_main_evidence_verifies_transport_claims_only(self):
        inputs = gate_inputs()
        verdict = cd_read_contract.ep.verify(
            inputs["artifact_bytes"], inputs["attestation"],
            {"repository": GATE_REPO, "commit": GATE_COMMIT, "signer_workflow": SIGNER,
             "geargrafx_revision": PINNED["revision"], "geargrafx_sha256": PINNED["sha256"]},
            GATE_ROM, inputs["run"], GATE_FIXTURE)
        self.assertEqual(verdict["result"], "PASS", verdict)  # the #125 verifier itself accepts these inputs
        status = compat_status(**inputs)
        self.assertEqual(set(status), set(DECLARED))
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["VERIFIED"] * 5)
        # Unverified items stay as declared, and the callee ABI (#139, slot 3) is untouched and not VERIFIED.
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))
        api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
        validate(api)
        self.assertNotIn("VERIFIED", {v["status"] for v in api["apis"][3]["contract"].values()})

    def test_local_only_or_self_declared_bundle_is_never_accepted(self):
        # The #125 LOCAL_ONLY result (consistent JSON, no attestation) is not compatibility evidence.
        self.assertBlocked(compat_status(**gate_inputs(attestation=None)))
        # The bare self-declared #118 bundle, positional as before, never passes either.
        self.assertBlocked(compat_status(bundle_for(), ROM_SHA))
        self.assertBlocked(compat_status())
        forged = gate_artifact(attestation={"verified": True}, verified=True, trust="VERIFIED")
        self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=forged, attestation=None)))
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "GITHUB_SHA": GATE_COMMIT}):
            self.assertBlocked(compat_status(**gate_inputs(attestation=None)))

    def test_forged_bundle_with_pinned_hashes_is_local_only(self):
        # A hand-written tests map that copies the pinned revision and the ROM hash is consistent locally
        # (that is the documented limit): it is LOCAL_ONLY, never VERIFIED/PASS, and BLOCKED for compatibility.
        forged = bundle_for()
        self.assertEqual(set(effective_status(forged, ROM_SHA).values()) & {"VERIFIED", "PASS"}, set())
        self.assertEqual(effective_status(forged, ROM_SHA)["T1"], "LOCAL_ONLY")
        self.assertEqual(compat_status(forged, ROM_SHA)["T1"], "BLOCKED")
        self.assertEqual(TRUST_LABEL, "LOCAL_ONLY")
        self.assertIn("SELF-DECLARED", COMPAT_REASON)
        self.assertIn("LOCAL_ONLY", COMPAT_REASON)

    def test_other_build_identity_is_blocked(self):
        artifact = gate_artifact()
        cases = {
            "attested_commit": {"attestation": gate_attestation(artifact, sourceRepositoryDigest="b" * 40,
                                                                buildSignerDigest="b" * 40)},
            "expected_commit": {"commit": "b" * 40},
            "run_head_sha": {"run": gate_run(head_sha="b" * 40)},
            "attested_ref": {"attestation": gate_attestation(artifact, sourceRepositoryRef="refs/heads/feature")},
            "signer_workflow": {"attestation": gate_attestation(
                artifact, buildSignerURI=f"https://github.com/{GATE_REPO}/.github/workflows/evil.yml@refs/heads/main")},
            "runner": {"attestation": gate_attestation(artifact, runnerEnvironment="self-hosted")},
            "trigger": {"attestation": gate_attestation(artifact, githubWorkflowTrigger="pull_request")},
            "repository": {"repository": "other/fork"},
            "rom_bytes": {"rom_bytes": b"other rom"},
            "fixture": {"fixture": dict(GATE_FIXTURE, bin_sha256="0" * 64)},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                self.assertBlocked(compat_status(**gate_inputs(**override)))
        # Geargrafx revision / archive differ from the pin, even when the attestation signs exactly these bytes.
        for field in ("geargrafx_revision", "source_archive_sha256"):
            other = gate_artifact(**{field: "0" * (40 if field == "geargrafx_revision" else 64)})
            with self.subTest(case=field):
                self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=other,
                                                                attestation=gate_attestation(other))))

    def test_caller_cannot_relax_the_pins_or_signer(self):
        with self.assertRaises(TypeError):
            compat_status(**gate_inputs(), policy={"geargrafx_revision": "0" * 40})

    def test_incomplete_ledger_or_artifact_is_not_verified(self):
        tests = {name: {"status": kind} for name, kind in cd_read_contract.ep.SCENARIO_KIND.items()}
        del tests["single_sector_lba5"]
        partial = gate_artifact(tests=tests)
        self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=partial, attestation=gate_attestation(partial))))
        claims = copy.deepcopy(TRANSPORT_CLAIMS)
        claims[0]["scenarios"] = ["no_such_run"]
        status = compat_status(**gate_inputs(), claims=claims)
        self.assertEqual(status["T1"], "UNKNOWN")
        self.assertEqual(status["T2"], "VERIFIED")

    def test_verifier_failure_or_exception_is_blocked(self):
        ep = cd_read_contract.ep
        for returned in ({"result": ep.FAIL, "reasons": ["x"]}, {"result": ep.BLOCKED, "reasons": ["x"]},
                         {"result": ep.LOCAL_ONLY, "reasons": ["x"]}, {"result": "pass"}, {}, None, "PASS"):
            with self.subTest(returned=returned), mock.patch.object(ep, "verify", return_value=returned):
                self.assertBlocked(compat_status(**gate_inputs()))
        for error in (RuntimeError("boom"), KeyError("geargrafx_revision"), RecursionError()):
            with self.subTest(error=error), mock.patch.object(ep, "verify", side_effect=error):
                self.assertBlocked(compat_status(**gate_inputs()))

    def test_missing_or_malformed_inputs_are_blocked_without_exception(self):
        for key in ("artifact_bytes", "attestation", "rom_bytes", "run", "fixture", "repository", "commit",
                    "branch_rules"):
            with self.subTest(missing=key):
                self.assertBlocked(compat_status(**gate_inputs(**{key: None})))
        weird = (0, 1.5, "x", [], {}, [None], True)
        for key in ("artifact_bytes", "attestation", "rom_bytes", "run", "fixture", "repository", "commit",
                    "branch_rules"):
            for value in weird:
                with self.subTest(key=key, value=value):
                    self.assertBlocked(compat_status(**gate_inputs(**{key: value})))
        for data in (b"{not json", b"\xff\xfe", b"[" * 100000 + b"]" * 100000, b"[]", b"null"):
            with self.subTest(data=data[:20]):
                inputs = gate_inputs(artifact_bytes=data, attestation=gate_attestation(data))
                self.assertBlocked(compat_status(**inputs))
        self.assertBlocked(compat_status(**gate_inputs(commit="A" * 40)))
        self.assertBlocked(compat_status(**gate_inputs(repository="not a repo")))

    def test_tampered_or_swapped_artifact_is_blocked(self):
        signed = gate_artifact()
        tampered = signed.replace(b'"PASS_EXPECTED_FAILURE"', b'"PASS_EXPECTED_FAILURE" ', 1)
        self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=tampered, attestation=gate_attestation(signed))))
        self.assertBlocked(compat_status(**gate_inputs(attestation=gate_attestation(signed, rom=b"other rom"))))

    def test_main_without_pr_and_force_push_rules_is_blocked(self):
        # #125 does not see branch protection; docs/cd-read-contract.md section 4 condition (4) requires it.
        for rules in ([], [{"type": "deletion"}], [{"type": "pull_request"}], [{"type": "non_fast_forward"}],
                      [{"type": "pull_request"}, "non_fast_forward"], {"type": "pull_request"}):
            with self.subTest(rules=rules):
                self.assertBlocked(compat_status(**gate_inputs(branch_rules=rules)))

    def test_integration_id_is_not_an_identity(self):
        # #125 and this ledger do not define an integration_id: the binding is the attested subject digest of the
        # exact artifact bytes plus the runInvocationURI run. A self-declared integration_id neither authenticates
        # a bundle nor lets one attestation cover another artifact.
        tagged = gate_artifact(integration_id="run-4242")
        self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=tagged, attestation=None)))
        retagged = gate_artifact(integration_id="run-9999")
        self.assertBlocked(compat_status(**gate_inputs(artifact_bytes=retagged, attestation=gate_attestation(tagged))))
        # An extra integration_id field does not break genuine attested evidence (it is just unauthenticated data).
        status = compat_status(**gate_inputs(artifact_bytes=tagged, attestation=gate_attestation(tagged)))
        self.assertEqual(status["T1"], "VERIFIED")
        self.assertNotIn("integration_id", (ROOT / "tools/evidence_provenance.py").read_text(encoding="utf-8"))
        self.assertEqual(compat_status(**gate_inputs(run=gate_run(id=RUN_ID + 1)))["T1"], "BLOCKED")

    def test_compat_gate_never_returns_verified_or_pass_without_attestation(self):
        for bundle, rom in ((None, None), (bundle_for(), ROM_SHA), (bundle_for(), None)):
            with self.subTest(bundle=bundle is not None, rom=rom is not None):
                self.assertNotIn("VERIFIED", compat_status(bundle, rom).values())
        self.assertNotIn("PASS", compat_status(**gate_inputs()).values())

    def run_cli(self, bundle):
        # Run main() against a temporary ROOT holding a ROM and a forged bundle with the correct ROM hash.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spec").mkdir()
            shutil.copy(ROOT / "spec/api_contracts.json", root / "spec/api_contracts.json")
            out = root / "build/debug/cdread-probe"
            out.mkdir(parents=True)
            rom = b"forged rom"
            (out / "cdread-probe-not-bios.pce").write_bytes(rom)
            if bundle is not None:
                bundle = bundle(hashlib.sha256(rom).hexdigest())
                (out / "cdread-evidence.json").write_text(json.dumps(bundle), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(cd_read_contract, "ROOT", root), contextlib.redirect_stdout(stdout), \
                    self.assertRaises(SystemExit) as exit_:
                cd_read_contract.main([])
            return exit_.exception.code, stdout.getvalue()

    def test_cli_forged_bundle_is_not_pass_and_exits_nonzero(self):
        code, text = self.run_cli(lambda sha: bundle_for(rom_sha256=sha))
        self.assertEqual(code, EXIT_LOCAL_ONLY)
        self.assertNotEqual(code, 0)
        self.assertTrue(text.startswith("LOCAL_ONLY "), text)
        self.assertNotIn("PASS ", text)
        self.assertNotIn("VERIFIED'", text)

    def test_cli_malformed_tests_entry_is_blocked(self):
        code, text = self.run_cli(lambda sha: bundle_for(rom_sha256=sha, tests={"single_sector_lba5": "PASS"}))
        self.assertEqual(code, EXIT_BLOCKED)
        self.assertTrue(text.startswith("BLOCKED "), text)

    def test_blocked_exit_code_is_fixed_and_matches_harness_119(self):
        # #119 tests/integration/geargrafx_harness.py: EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "SKIP": 4}
        self.assertEqual(EXIT_BLOCKED, 3)

    def test_end_of_disc_runs_use_premature_status_code_of_115(self):
        # #115 tools/cd_status.py CODES: 5 -> PREMATURE_STATUS. Every end-of-disc failing_guest call uses code 5.
        text = INTEGRATION.read_text(encoding="utf-8")
        codes = set(re.findall(r'suite\.failing_guest\("\w+", suite\.variant\([^)]*\), \w+, \w+, (\d+), 5,', text))
        self.assertEqual(codes, {"5"})
        self.assertTrue(TRUST_LABEL.startswith("LOCAL_ONLY"))  # #119 artifact_policy and #116 trust both say local-only


if __name__ == "__main__":
    unittest.main()
