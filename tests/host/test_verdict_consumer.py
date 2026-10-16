"""Issue #153: consumer of the signed CD_READ verdict (docs/verdict-schema.md). Synthetic data only.

gh is simulated by patching verdict_consumer._gh. TEST-ONLY: the fake "signs" only digests a test registers; it is
never used outside these tests and production has no way to reach it.
"""
import copy
import datetime
import hashlib
import inspect
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import tools.cd_read_contract as cd_read_contract
import tools.verdict_consumer as vc

ROM = b"synthetic test-only rom"
REPO = "mao2009/oss-pce-cd-bios"
SIGNER = ".github/workflows/api-source.yml"
NOW = datetime.datetime(2026, 10, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)
HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cd_read_contract.ROOT, capture_output=True, text=True,
                      check=True).stdout.strip()
ALL_VERIFIED = {f"T{i}": "VERIFIED" for i in range(1, 6)}


def verdict(**over):
    v = {
        "schema": "oss-pce-cd-bios/cdread-verdict/v1", "repository": REPO, "commit": HEAD, "ref": "refs/heads/main",
        "producer": {"workflow": SIGNER, "run_id": 7, "run_attempt": 1},
        "issued_at": "2026-10-16T00:00:00Z", "expires_at": "2026-10-23T00:00:00Z",
        "geargrafx": {"revision": cd_read_contract.PINNED["revision"],
                      "source_archive_sha256": cd_read_contract.PINNED["sha256"]},
        "evidence": {"artifact_sha256": "a" * 64, "rom_sha256": hashlib.sha256(ROM).hexdigest(),
                     "bin_sha256": "b" * 64, "cue_sha256": "c" * 64},
        "provenance": {"result": "PASS", "verifier": "tools/evidence_provenance.py"},
        "main_rules": {"source": "github-api", "required_approving_review_count": 1},
        "claims": {**ALL_VERIFIED, "T6": "HYPOTHESIS", "T7": "UNKNOWN", "T8": "UNKNOWN"},
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(v.get(key), dict):
            v[key] = {**v[key], **value}
        else:
            v[key] = value
    return json.dumps(v).encode()


def certificate(**over):
    c = {"issuer": "https://token.actions.githubusercontent.com",
         "sourceRepositoryURI": f"https://github.com/{REPO}", "sourceRepositoryDigest": HEAD,
         "sourceRepositoryRef": "refs/heads/main", "buildSignerDigest": HEAD,
         "buildSignerURI": f"https://github.com/{REPO}/{SIGNER}@refs/heads/main",
         "githubWorkflowTrigger": "push", "runnerEnvironment": "github-hosted",
         "runInvocationURI": f"https://github.com/{REPO}/actions/runs/7/attempts/1"}
    c.update(over)
    return c


class FakeGh:
    """TEST-ONLY stand-in for `gh attestation verify`: exit 0 only for a registered (signed) digest."""

    def __init__(self):
        self.signed, self.calls = {}, []

    def sign(self, data, subject=None, **cert):
        digest = hashlib.sha256(data).hexdigest()
        self.signed[digest] = [{"verificationResult": {
            "statement": {"subject": [{"digest": {"sha256": subject or digest}}],
                          "predicateType": "https://slsa.dev/provenance/v1"},
            "signature": {"certificate": certificate(**cert)}}}]
        return data

    def __call__(self, args):
        self.calls.append(args)
        digest = hashlib.sha256(Path(args[2]).read_bytes()).hexdigest()
        if digest not in self.signed:
            return 1, "no matching attestation"
        return 0, json.dumps(self.signed[digest])


class Base(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGh()
        for p in (mock.patch.object(vc, "_gh", self.gh), mock.patch.object(vc, "_now", lambda: NOW)):
            p.start()
            self.addCleanup(p.stop)

    def enabled(self):
        return mock.patch.object(vc, "VERIFIED_PATH_ENABLED", True)

    def assertNoVerified(self, data, rom=ROM, **kw):
        status, reasons = vc.consume(data, rom, **kw)
        self.assertNotIn("VERIFIED", status.values(), reasons)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["BLOCKED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))
        self.assertTrue(reasons)
        return reasons


class PositivePathTests(Base):
    def test_signed_valid_verdict_promotes_only_t1_to_t5_when_path_enabled(self):
        data = self.gh.sign(verdict())
        with self.enabled():
            status, reasons = vc.consume(data, ROM)
        self.assertEqual(reasons, [])
        self.assertEqual({k: status[k] for k in ALL_VERIFIED}, ALL_VERIFIED)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))
        args = self.gh.calls[0]
        for flag, value in (("--repo", REPO), ("--signer-workflow", f"{REPO}/{SIGNER}"),
                            ("--source-ref", "refs/heads/main"), ("--source-digest", HEAD),
                            ("--cert-oidc-issuer", "https://token.actions.githubusercontent.com"),
                            ("--predicate-type", "https://slsa.dev/provenance/v1")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--deny-self-hosted-runners", args)

    def test_verdict_blocked_claim_stays_blocked(self):
        data = self.gh.sign(verdict(claims={"T2": "BLOCKED"}))
        with self.enabled():
            status, _ = vc.consume(data, ROM)
        self.assertEqual(status["T2"], "BLOCKED")
        self.assertEqual(status["T1"], "VERIFIED")

    def test_bundle_path_is_passed_to_gh(self):
        data = self.gh.sign(verdict())
        with self.enabled():
            vc.consume(data, ROM, bundle_path="b.jsonl")
        self.assertEqual(self.gh.calls[0][self.gh.calls[0].index("--bundle") + 1], "b.jsonl")


class ProductionFailClosedTests(Base):
    def test_production_switch_is_off(self):
        self.assertIs(vc.VERIFIED_PATH_ENABLED, False)

    def test_perfect_verdict_is_blocked_while_switch_is_off(self):
        reasons = self.assertNoVerified(self.gh.sign(verdict()))
        self.assertIn("VERIFIED_PATH_ENABLED", " ".join(reasons))

    def test_zero_approvals_is_blocked_even_when_switch_is_on(self):
        for count in (0, -1):
            with self.subTest(count=count), self.enabled():
                self.assertNoVerified(self.gh.sign(verdict(main_rules={"required_approving_review_count": count})))

    def test_no_caller_supplied_attestation_run_rules_commit_or_time(self):
        self.assertEqual(set(inspect.signature(vc.consume).parameters), {"verdict_bytes", "rom_bytes", "bundle_path"})


class SignatureTests(Base):
    def test_unsigned_tampered_and_gh_failures_are_blocked(self):
        signed = self.gh.sign(verdict())
        tampered = signed.replace(b'"run_id": 7', b'"run_id": 8')
        cases = {"unsigned": verdict(main_rules={"required_approving_review_count": 2}), "tampered": tampered}
        with self.enabled():
            for name, data in cases.items():
                with self.subTest(name):
                    self.assertNoVerified(data)
            for name, fake in (("missing gh", mock.Mock(side_effect=FileNotFoundError("gh"))),
                               ("timeout", mock.Mock(side_effect=subprocess.TimeoutExpired("gh", 1))),
                               ("bad json", mock.Mock(return_value=(0, "{not json"))),
                               ("empty list", mock.Mock(return_value=(0, "[]"))),
                               ("not a list", mock.Mock(return_value=(0, '{"verified": true}')))):
                with self.subTest(name), mock.patch.object(vc, "_gh", fake):
                    self.assertNoVerified(signed)

    def test_gh_success_with_other_subject_digest_is_blocked(self):
        # Defence in depth: even if gh said yes, the subject must be the digest we computed ourselves.
        with self.enabled():
            self.assertNoVerified(self.gh.sign(verdict(), subject="0" * 64))

    def test_wrong_certificate_identity_is_blocked(self):
        cases = {"issuer": "https://evil.example", "sourceRepositoryURI": "https://github.com/attacker/oss-pce-cd-bios",
                 "sourceRepositoryDigest": "0" * 40, "sourceRepositoryRef": "refs/pull/1/merge",
                 "buildSignerURI": f"https://github.com/{REPO}/.github/workflows/other.yml@refs/heads/main",
                 "buildSignerDigest": "1" * 40, "githubWorkflowTrigger": "pull_request",
                 "runnerEnvironment": "self-hosted",
                 "runInvocationURI": f"https://github.com/{REPO}/actions/runs/8/attempts/1"}
        with self.enabled():
            for field, value in cases.items():
                with self.subTest(field):
                    self.assertNoVerified(self.gh.sign(verdict(), **{field: value}))
            with self.subTest("issuer missing"):
                fake = FakeGh()
                data = fake.sign(verdict())
                del fake.signed[hashlib.sha256(data).hexdigest()][0]["verificationResult"]["signature"][
                    "certificate"]["issuer"]
                with mock.patch.object(vc, "_gh", fake):
                    self.assertNoVerified(data)


class ContentTests(Base):
    def check(self, data, rom=ROM):
        with self.enabled():
            return self.assertNoVerified(self.gh.sign(data), rom)

    def test_wrong_commit_repo_ref_producer(self):
        for name, data in (("commit", verdict(commit="0" * 40)), ("commit upper", verdict(commit=HEAD.upper())),
                           ("repo", verdict(repository="attacker/oss-pce-cd-bios")), ("ref", verdict(ref="refs/heads/x")),
                           ("workflow", verdict(producer={"workflow": ".github/workflows/x.yml"})),
                           ("run id bool", verdict(producer={"run_id": True})),
                           ("run id str", verdict(producer={"run_id": "7"})),
                           ("run id 0", verdict(producer={"run_id": 0})),
                           ("schema", verdict(schema="oss-pce-cd-bios/cdread-verdict/v2"))):
            with self.subTest(name):
                self.check(data)

    def test_wrong_rom_or_denylisted_rom(self):
        self.check(verdict(), rom=b"another rom")
        self.check(verdict(), rom=None)
        with mock.patch.object(vc, "load_denylist", return_value={hashlib.sha256(ROM).hexdigest()}):
            self.check(verdict())

    def test_pins_provenance_and_rules(self):
        for name, data in (("revision", verdict(geargrafx={"revision": "0" * 40})),
                           ("archive", verdict(geargrafx={"source_archive_sha256": "0" * 64})),
                           ("provenance", verdict(provenance={"result": "LOCAL_ONLY"})),
                           ("verifier", verdict(provenance={"verifier": "x.py"})),
                           ("rules source", verdict(main_rules={"source": "caller"})),
                           ("rules bool", verdict(main_rules={"required_approving_review_count": True})),
                           ("rules str", verdict(main_rules={"required_approving_review_count": "1"})),
                           ("evidence hex", verdict(evidence={"bin_sha256": "B" * 64}))):
            with self.subTest(name):
                self.check(data)

    def test_t6_to_t8_cannot_be_promoted_and_claims_must_be_exact(self):
        for name, claims in (("T6", {"T6": "VERIFIED"}), ("T7", {"T7": "VERIFIED"}), ("T8", {"T8": "HYPOTHESIS"}),
                             ("lookalike", {"T1": "VERIFIED "}), ("lower", {"T1": "verified"}),
                             ("PASS", {"T1": "PASS"})):
            with self.subTest(name):
                self.check(verdict(claims=claims))
        for name, claims in (("missing", ALL_VERIFIED),
                             ("extra", {**json.loads(verdict())["claims"], "T9": "VERIFIED"}), ("list", [])):
            with self.subTest(name):
                v = json.loads(verdict())
                v["claims"] = claims  # replace whole, not merged
                self.check(json.dumps(v).encode())

    def test_malformed_json_unknown_fields_duplicates_and_size(self):
        base = verdict()
        cases = {"not json": b"{", "not utf8": b"\xff", "array": b"[]", "null": b"null",
                 "unknown field": verdict(extra=1), "missing field": json.dumps(
                     {k: v for k, v in json.loads(base).items() if k != "main_rules"}).encode(),
                 "duplicate key": base[:-1] + b', "commit": "' + b"0" * 40 + b'"}',
                 "nan": base.replace(b'"run_attempt": 1', b'"run_attempt": NaN'),
                 "too big": base[:-1] + b"," + b" " * vc.MAX_VERDICT_BYTES + b'"pad": 1}',
                 "deep": b"[" * 100000 + b"]" * 100000}
        for name, data in cases.items():
            with self.subTest(name):
                self.check(data)
        for data in (None, "str", bytearray(base)):
            with self.subTest(type=type(data).__name__), self.enabled():
                self.assertNoVerified(data)


class ExpiryTests(Base):
    def at(self, now, **over):
        data = self.gh.sign(verdict(**over))
        with self.enabled(), mock.patch.object(vc, "_now", lambda: now):
            return vc.consume(data, ROM)[0]["T1"]

    def test_validity_window_boundaries(self):
        start = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 10, 23, tzinfo=datetime.timezone.utc)
        second = datetime.timedelta(seconds=1)
        self.assertEqual(self.at(start), "VERIFIED")
        self.assertEqual(self.at(end - second), "VERIFIED")
        self.assertEqual(self.at(end), "BLOCKED")
        self.assertEqual(self.at(start - second), "BLOCKED")

    def test_lifetime_and_format(self):
        self.assertEqual(self.at(NOW, expires_at="2026-11-15T00:00:00Z"), "VERIFIED")  # exactly 30 days
        self.assertEqual(self.at(NOW, expires_at="2026-11-15T00:00:01Z"), "BLOCKED")
        for name, over in (("reversed", {"expires_at": "2026-10-15T00:00:00Z"}),
                           ("equal", {"expires_at": "2026-10-16T00:00:00Z"}),
                           ("offset", {"issued_at": "2026-10-16T00:00:00+00:00"}),
                           ("no Z", {"expires_at": "2026-10-23T00:00:00"}),
                           ("int", {"expires_at": 1})):
            with self.subTest(name):
                self.assertEqual(self.at(NOW, **over), "BLOCKED")


class AdversarialTests(Base):
    """PR #166 security review: each attack must end BLOCKED (never raise, never VERIFIED)."""

    def blocked(self, data, rom=ROM):
        with self.enabled():
            return self.assertNoVerified(data, rom)

    def test_signature_swapped_between_two_valid_verdicts(self):
        a = self.gh.sign(verdict())
        b = self.gh.sign(verdict(producer={"run_id": 9}), runInvocationURI=f"https://github.com/{REPO}/actions/runs/9/attempts/1")
        self.gh.signed[hashlib.sha256(a).hexdigest()] = self.gh.signed[hashlib.sha256(b).hexdigest()]
        self.blocked(a)

    def test_certificate_of_another_run_or_attempt(self):
        for uri in ("runs/9/attempts/1", "runs/7/attempts/2", "runs/07/attempts/1", "runs/7/attempts/1/x"):
            with self.subTest(uri):
                self.blocked(self.gh.sign(verdict(), runInvocationURI=f"https://github.com/{REPO}/actions/{uri}"))

    def test_issuer_lookalike_and_wrong_predicate_type(self):
        self.blocked(self.gh.sign(verdict(), issuer="https://token.actions.githubusercontent.com/"))
        data = self.gh.sign(verdict())
        self.gh.signed[hashlib.sha256(data).hexdigest()][0]["verificationResult"]["statement"]["predicateType"] = \
            "https://in-toto.io/attestation/release/v0.1"
        self.blocked(data)

    def test_one_second_past_expiry(self):
        with self.enabled(), mock.patch.object(
                vc, "_now", lambda: datetime.datetime(2026, 10, 23, 0, 0, 1, tzinfo=datetime.timezone.utc)):
            self.assertNoVerified(self.gh.sign(verdict()))

    def test_far_future_dates_do_not_crash(self):
        for issued, expires in (("9999-12-31T00:00:00Z", "9999-12-31T12:00:00Z"),
                                ("9999-12-01T00:00:00Z", "9999-12-31T00:00:00Z")):
            with self.subTest(issued):
                self.blocked(self.gh.sign(verdict(issued_at=issued, expires_at=expires)))

    def test_nested_duplicate_keys(self):
        base = verdict()
        for old, new in ((b'"T8": "UNKNOWN"', b'"T8": "UNKNOWN", "T6": "VERIFIED"'),
                         (b'"run_attempt": 1', b'"run_attempt": 1, "run_id": 9')):
            with self.subTest(new):
                self.assertIn(old, base)
                self.blocked(self.gh.sign(base.replace(old, new)))

    def test_size_boundary_and_encodings(self):
        base = verdict()
        cases = {"max+1": base[:-1] + b" " * (vc.MAX_VERDICT_BYTES - len(base) + 1) + b"}",
                 "utf-16": base.decode().encode("utf-16"), "bom": b"\xef\xbb\xbf" + base,
                 "latin-1 in string": base.replace(b'"ref": "refs/heads/main"', b'"ref": "refs/heads/m\xe1in"')}
        self.assertEqual(len(cases["max+1"]), vc.MAX_VERDICT_BYTES + 1)
        for name, data in cases.items():
            with self.subTest(name):
                self.blocked(self.gh.sign(data))

    def test_bool_float_and_zero_as_int(self):
        for over in ({"run_attempt": True}, {"run_attempt": 0}, {"run_id": 7.0}, {"run_attempt": False}):
            with self.subTest(over):
                self.blocked(self.gh.sign(verdict(producer=over)))

    def test_input_cannot_flip_verified_path_enabled(self):
        import contextlib, io, os, tempfile
        for data in (verdict(VERIFIED_PATH_ENABLED=True), verdict(main_rules={"VERIFIED_PATH_ENABLED": True})):
            self.assertNoVerified(self.gh.sign(data))
        with mock.patch.dict(os.environ, {"VERIFIED_PATH_ENABLED": "1"}):
            self.assertNoVerified(self.gh.sign(verdict()))
        self.assertIs(vc.VERIFIED_PATH_ENABLED, False)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            path, rom = Path(tmp) / "v.json", Path(tmp) / "r.pce"
            path.write_bytes(self.gh.sign(verdict()))
            rom.write_bytes(ROM)
            with self.assertRaises(SystemExit):
                vc.main([str(path), "--rom", str(rom), "--enable"])
            self.assertEqual(vc.main([str(path), "--rom", str(rom)]), vc.EXIT_BLOCKED)
        self.assertIs(vc.VERIFIED_PATH_ENABLED, False)


class CliTests(Base):
    def test_cli_exit_codes(self):
        import contextlib, io, tempfile
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            path, rom = Path(tmp) / "v.json", Path(tmp) / "r.pce"
            path.write_bytes(self.gh.sign(verdict()))
            rom.write_bytes(ROM)
            self.assertEqual(vc.main([str(path), "--rom", str(rom)]), vc.EXIT_BLOCKED)
            with self.enabled():
                self.assertEqual(vc.main([str(path), "--rom", str(rom)]), 0)
                self.assertEqual(vc.main([str(path), "--rom", str(Path(tmp) / "missing")]), vc.EXIT_BLOCKED)


if __name__ == "__main__":
    unittest.main()
