"""Issue #12: CD_READ contract ledger + transport outcome model (host-only; real core is authoritative)."""
import collections.abc
import contextlib
import copy
import hashlib
import inspect
import io
import json
import re
import shutil
import subprocess
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

    def test_status_is_read_once_per_claim(self):
        """#155: a Mapping whose status changes per access must not turn a non-VERIFIED check into VERIFIED."""
        class Flip(collections.abc.Mapping):
            def __init__(self):
                self.reads = 0
            def __getitem__(self, key):
                if key == "status":
                    self.reads += 1
                    return "UNKNOWN" if self.reads == 1 else "VERIFIED"
                return {"id": "TX", "scenarios": []}[key]
            def __iter__(self):
                return iter(("id", "status", "scenarios"))
            def __len__(self):
                return 3
        flip = Flip()
        out = effective_status(bundle_for(), ROM_SHA, [flip])
        self.assertEqual(flip.reads, 1)
        self.assertNotIn("VERIFIED", out.values())

    def test_abnormal_status_never_passes_through(self):
        class Liar(str):
            def __eq__(self, other):
                return False
            def __ne__(self, other):
                return True
            __hash__ = str.__hash__
        for name, status in {"liar_str": Liar("VERIFIED"), "none": None, "bytes": b"VERIFIED", "unlisted": "PASS"}.items():
            with self.subTest(case=name):
                out = effective_status(bundle_for(), ROM_SHA, [{"id": "TX", "status": status, "scenarios": []}])
                self.assertEqual(out, {"TX": "BLOCKED"})

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


# Synthetic #125 inputs (no ROM, disc or BIOS data). The attacker controls every one of them: the artifact, the
# `gh attestation verify` JSON, the run record and any branch-rules JSON are plain data handed to the function.
GATE_ROM = b"synthetic rom"
SIGNER = ".github/workflows/api-source.yml"
VERIFIED_IDS = [c["id"] for c in TRANSPORT_CLAIMS if c["status"] == "VERIFIED"]


def head_commit():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def gate_artifact(fixture=None, **overrides):
    ep = cd_read_contract.ep
    artifact = {"status": "PASS", "geargrafx_revision": PINNED["revision"], "source_archive_sha256": PINNED["sha256"],
                "rom_sha256": hashlib.sha256(GATE_ROM).hexdigest(), "disc": fixture or ep.recompute_fixture(),
                "tests": {name: {"status": kind} for name, kind in ep.SCENARIO_KIND.items()},
                "primary_observations": {"RUN": [{"cycle": "cold", "reason": "cd_halt_ok"},
                                                 {"cycle": "warm", "reason": "cd_halt_ok"}]}}
    artifact.update(overrides)
    return json.dumps(artifact, sort_keys=True).encode()


def forged_attestation(artifact_bytes, repo=None, commit=None, **cert):
    """Hand-written JSON shaped like `gh attestation verify --format json`; nothing here is signed."""
    repo, commit = repo or cd_read_contract.REPOSITORY, commit or head_commit()
    certificate = {
        "sourceRepositoryURI": f"https://github.com/{repo}", "sourceRepositoryDigest": commit,
        "sourceRepositoryRef": "refs/heads/main", "buildSignerDigest": commit,
        "buildSignerURI": f"https://github.com/{repo}/{SIGNER}@refs/heads/main",
        "githubWorkflowTrigger": "push", "runnerEnvironment": "github-hosted",
        "runInvocationURI": f"https://github.com/{repo}/actions/runs/1/attempts/1"}
    certificate.update(cert)
    subjects = [{"digest": {"sha256": hashlib.sha256(artifact_bytes).hexdigest()}},
                {"digest": {"sha256": hashlib.sha256(GATE_ROM).hexdigest()}}]
    return [{"verificationResult": {"statement": {"subject": subjects}, "signature": {"certificate": certificate}}}]


def forged_run(repo=None, commit=None, **overrides):
    repo = repo or cd_read_contract.REPOSITORY
    run = {"id": 1, "run_attempt": 1, "head_sha": commit or head_commit(), "head_branch": "main", "event": "push",
           "path": SIGNER, "repository": {"full_name": repo}, "head_repository": {"full_name": repo}}
    run.update(overrides)
    return run


def reason_for(artifact, attestation="forged", run="forged", rom=GATE_ROM):
    attestation = forged_attestation(artifact) if attestation == "forged" else attestation
    run = forged_run() if run == "forged" else run
    return cd_read_contract.compat_reason(artifact, attestation, rom, run)


class TrustBoundaryTests(unittest.TestCase):
    """No caller-supplied data reaches VERIFIED: attestation authenticity cannot be checked in-process (#153)."""

    def assertBlocked(self, status):
        self.assertEqual([status[i] for i in VERIFIED_IDS], ["BLOCKED"] * len(VERIFIED_IDS), status)
        self.assertNotIn("VERIFIED", status.values())
        self.assertNotIn("PASS", status.values())

    def test_gate_takes_no_external_attestation_run_rules_or_fixture(self):
        # BLOCKER-1/2, attacks 2, 3b, 4: no parameter lets gh output, a run record, branch rules, a fixture hash or an
        # expected repository/commit reach the gate's status.
        self.assertEqual(set(inspect.signature(compat_status).parameters), {"bundle", "rom_sha256", "claims"})
        self.assertEqual(set(inspect.signature(cd_read_contract.compat_reason).parameters),
                         {"artifact_bytes", "attestation", "rom_bytes", "run"})

    def test_attack1_forged_attestation_with_correct_identity_is_blocked(self):
        # Every field a real attestation would carry, for this repository and this checkout commit, hand-written.
        artifact = gate_artifact()
        reason = reason_for(artifact)
        self.assertEqual(reason, cd_read_contract.IN_PROCESS_REASON)  # #125 finds the JSON consistent ...
        self.assertIn("in-process", reason)
        rom_sha = hashlib.sha256(GATE_ROM).hexdigest()
        self.assertBlocked(compat_status(json.loads(artifact), rom_sha))  # ... and the gate is still BLOCKED
        forged = gate_artifact(attestation={"verified": True}, verified=True, trust="VERIFIED")
        self.assertBlocked(compat_status(json.loads(forged), rom_sha))

    def test_attack1b_other_repository_or_commit_is_rejected_by_the_checkout_pins(self):
        artifact = gate_artifact()
        for repo, commit in (("mao2009/oss-pce-cd-bios", "0" * 40), ("attacker/oss-pce-cd-bios", None)):
            with self.subTest(repo=repo, commit=commit):
                reason = reason_for(artifact, forged_attestation(artifact, repo, commit), forged_run(repo, commit))
                self.assertTrue(reason.startswith("#125 verifier result 'FAIL'"), reason)

    def test_attack3b_caller_fixture_hash_is_not_used(self):
        artifact = gate_artifact(fixture={"bin_sha256": "e" * 64, "cue_sha256": "f" * 64})
        self.assertIn("recomputed synthetic fixture", reason_for(artifact))

    def test_attack4_forged_or_mismatched_run_record_is_not_evidence(self):
        artifact = gate_artifact()
        for run in (forged_run(id=2), forged_run(head_branch="feature"), None, "x"):
            with self.subTest(run=run):
                self.assertTrue(reason_for(artifact, run=run).startswith("#125 verifier result"))

    def test_attack8b_claims_cannot_promote_t6_to_t8(self):
        claims = copy.deepcopy(TRANSPORT_CLAIMS)
        for claim in claims[5:]:
            claim["status"] = "VERIFIED"
        status = compat_status(bundle_for(), ROM_SHA, claims)
        self.assertEqual([status[i] for i in ("T6", "T7", "T8")], ["BLOCKED"] * 3)
        self.assertNotIn("VERIFIED", status.values())

    def test_claim_status_is_read_once_and_must_be_an_exact_unverified_str(self):
        class Str(str):
            pass

        class Flip(collections.abc.Mapping):
            """Answers HYPOTHESIS on the first read of status and VERIFIED afterwards."""
            def __init__(self, base):
                self.base, self.reads = base, 0

            def __getitem__(self, key):
                if key == "status":
                    self.reads += 1
                    return "HYPOTHESIS" if self.reads == 1 else "VERIFIED"
                return self.base[key]

            def __iter__(self):
                return iter(self.base)

            def __len__(self):
                return len(self.base)

        class DictFlip(dict):
            def __getitem__(self, key):
                return "VERIFIED" if key == "status" else super().__getitem__(key)

        base = TRANSPORT_CLAIMS[5]  # T6, HYPOTHESIS
        for value in ("VERIFIED", "VERIFIED ", " HYPOTHESIS", "VERIFIED​", "HYPOTHESIS​", "hypothesis",
                      Str("HYPOTHESIS"), Str("VERIFIED"), None, 1, ["HYPOTHESIS"], "BLOCKED", "LOCAL_ONLY", "PASS"):
            with self.subTest(value=value):
                self.assertEqual(compat_status(claims=[dict(base, status=value)]), {"T6": "BLOCKED"})
        for claim in (Flip(dict(base)), DictFlip(base)):
            with self.subTest(claim=type(claim).__name__):
                self.assertEqual(compat_status(claims=[claim]), {"T6": "BLOCKED"})
        self.assertEqual(compat_status(claims=[dict(base)]), {"T6": "HYPOTHESIS"})
        for status in compat_status().values():
            self.assertIs(type(status), str)

    def test_compat_reason_never_says_pass_or_verified(self):
        artifact = gate_artifact()
        reasons = [reason_for(artifact), reason_for(artifact, attestation=None), reason_for(b"{}"),
                   reason_for(gate_artifact(status="FAIL", tests={})), cd_read_contract.compat_reason()]
        with mock.patch.object(cd_read_contract.ep, "verify", return_value="PASS"):
            reasons.append(reason_for(artifact))
        with mock.patch.object(cd_read_contract.ep, "verify", side_effect=RuntimeError("PASS VERIFIED")):
            reasons.append(reason_for(artifact))
        for reason in reasons:
            with self.subTest(reason=reason[:60]):
                self.assertIsNone(re.search(r"\b(PASS|VERIFIED)\b", reason, re.I), reason)

    def test_compat_reason_without_arguments_names_the_missing_input(self):
        reason = cd_read_contract.compat_reason()
        self.assertTrue(reason.startswith("#125 verifier could not run: "), reason)
        self.assertIn("artifact_bytes", reason)
        self.assertNotIn("AttributeError", reason)

    def test_declared_unverified_items_and_callee_abi_stay_as_declared(self):
        status = compat_status(bundle_for(), ROM_SHA)
        self.assertBlocked(status)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))
        api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
        validate(api)
        self.assertNotIn("VERIFIED", {v["status"] for v in api["apis"][3]["contract"].values()})

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

    def test_verifier_failure_exception_or_malformed_input_is_a_reason_not_a_crash(self):
        ep = cd_read_contract.ep
        artifact = gate_artifact()
        for error in (RuntimeError("boom"), KeyError("x"), RecursionError()):
            with self.subTest(error=error), mock.patch.object(ep, "verify", side_effect=error):
                self.assertTrue(reason_for(artifact).startswith("#125 verifier could not run"))
        for data in (None, "x", b"{not json", b"\xff\xfe", b"[" * 100000 + b"]" * 100000, b"null"):
            with self.subTest(data=str(data)[:20]):
                self.assertNotEqual(reason_for(data, attestation=None, run=None), cd_read_contract.IN_PROCESS_REASON)
        self.assertIn("LOCAL_ONLY", reason_for(artifact, attestation=None))

    def test_integration_id_is_not_an_identity(self):
        # #125 and this ledger define no integration_id. A self-declared one neither authenticates a bundle nor lets
        # one attestation cover another artifact (binding is the subject digest of the exact bytes).
        tagged, retagged = gate_artifact(integration_id="run-1"), gate_artifact(integration_id="run-2")
        self.assertIn("subjects", reason_for(retagged, attestation=forged_attestation(tagged)))
        self.assertBlocked(compat_status(json.loads(tagged), hashlib.sha256(GATE_ROM).hexdigest()))
        self.assertNotIn("integration_id", (ROOT / "tools/evidence_provenance.py").read_text(encoding="utf-8"))

    def test_compat_gate_never_returns_verified_or_pass(self):
        for bundle, rom in ((None, None), (bundle_for(), ROM_SHA), (bundle_for(), None)):
            with self.subTest(bundle=bundle is not None, rom=rom is not None):
                self.assertBlocked(compat_status(bundle, rom))

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
        self.assertIsNone(re.search(r"\b(PASS|VERIFIED)\b", text, re.I), text)

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
