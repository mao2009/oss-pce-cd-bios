"""Producer -> consumer contract for the signed CD_READ verdict (#156 -> #153), both halves in one checkout.

Review finding on #166/#167: the producer (tools/cdread_verdict.py, #156) emitted `claims` straight from
`cd_read_contract.compat_status()`, so T1-T5 were always BLOCKED, while the consumer (tools/verdict_consumer.py,
#153) only promotes T1-T5 from a signed verdict whose claims are VERIFIED. The designed promotion path was then
unreachable from any real verdict, and the consumer's success test used a hand-built verdict. This file pins the
round trip with the real producer and defines the source of facts (authenticated #125 scenario coverage) and the
conditions (approval boundary, then VERIFIED_PATH_ENABLED) in one place.

Synthetic data only: no network, no gh, no ROM, no BIOS. The consumer's `_gh` is replaced by test_verdict_consumer's
FakeGh, which "signs" only digests these tests register; production has no way to reach it.
"""
import datetime
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "host"))
import cd_read_contract as crc  # noqa: E402
import cdread_verdict as cv  # noqa: E402
import evidence_provenance as ep  # noqa: E402
import verdict_consumer as vc  # noqa: E402
from provenance_guard import load_denylist  # noqa: E402
from test_evidence_provenance import ATTEMPT, ROM, RUN_ID, encode, make_artifact, make_attestation, make_run  # noqa: E402
from test_verdict_consumer import FakeGh  # noqa: E402

REPO = crc.REPOSITORY
WORKFLOW = ep.SIGNER_WORKFLOW
HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=crc.ROOT, capture_output=True, text=True,
                      check=True).stdout.strip()
OTHER_COMMIT = "b" * 40
FIXTURE = {"bin_sha256": "e" * 64, "cue_sha256": "f" * 64}
POLICY = {"repository": REPO, "commit": HEAD, "signer_workflow": WORKFLOW,
          "geargrafx_revision": crc.PINNED["revision"], "geargrafx_sha256": crc.PINNED["sha256"],
          "denied_sha256": load_denylist(crc.ROOT)}
ENV = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": ep.TRUSTED_REF, "GITHUB_REPOSITORY": REPO,
       "GITHUB_SHA": HEAD, "GITHUB_RUN_ID": str(RUN_ID), "GITHUB_RUN_ATTEMPT": str(ATTEMPT),
       "GITHUB_WORKFLOW_REF": f"{REPO}/{WORKFLOW}@{ep.TRUSTED_REF}"}
NOW = datetime.datetime(2026, 10, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)


def rules(approvals):
    return [{"type": "deletion"},
            {"type": "pull_request", "parameters": {"required_approving_review_count": approvals}}]


def evidence_bytes():
    return encode(make_artifact(geargrafx_revision=crc.PINNED["revision"], source_archive_sha256=crc.PINNED["sha256"],
                                disc=dict(FIXTURE)))


def produce(approvals):
    """A real producer verdict for this checkout: the same produce() the trusted main CI job runs."""
    data = evidence_bytes()
    attestation = make_attestation(data, commit=HEAD, repo=REPO, workflow=WORKFLOW, rom=ROM)
    run = make_run(head_sha=HEAD, path=WORKFLOW, repository={"full_name": REPO}, head_repository={"full_name": REPO})
    return cv.produce(data, ROM, attestation, run, rules(approvals), ENV, POLICY, FIXTURE, now=NOW)


class Base(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGh()
        patchers = (mock.patch.object(vc, "_gh", self.gh), mock.patch.object(vc, "_now", lambda: NOW))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def sign(self, verdict, run_id=RUN_ID, attempt=ATTEMPT, **cert):
        """Register a signature for the producer verdict (gh output shape) and return the exact verdict bytes."""
        data = cv.encode(verdict)
        return self.gh.sign(data, runInvocationURI=f"https://github.com/{REPO}/actions/runs/{run_id}/attempts/{attempt}",
                            **cert)

    def consume(self, data, enabled, rom=ROM):
        with mock.patch.object(vc, "VERIFIED_PATH_ENABLED", enabled):
            return vc.consume(data, rom)

    def assertBlocked(self, data):
        status, reasons = self.consume(data, enabled=True)
        self.assertNotIn("VERIFIED", status.values(), reasons)
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["BLOCKED"] * 5)
        self.assertTrue(reasons)
        return reasons


class RoundTripTests(Base):
    def test_real_producer_verdict_promotes_t1_to_t5_when_path_enabled(self):
        status, reasons = self.consume(self.sign(produce(approvals=1)), enabled=True)
        self.assertEqual(reasons, [])
        self.assertEqual([status[f"T{i}"] for i in range(1, 6)], ["VERIFIED"] * 5)
        self.assertEqual((status["T6"], status["T7"], status["T8"]), ("HYPOTHESIS", "UNKNOWN", "UNKNOWN"))

    def test_real_producer_verdict_is_blocked_in_production_while_the_switch_is_off(self):
        reasons = self.consume(self.sign(produce(approvals=1)), enabled=False)[1]
        self.assertIn("VERIFIED_PATH_ENABLED", " ".join(reasons))

    def test_producer_stays_blocked_and_so_does_the_consumer_below_the_approval_boundary(self):
        verdict = produce(approvals=0)
        self.assertEqual(verdict["claims"], {**{f"T{i}": "BLOCKED" for i in range(1, 6)},
                                             "T6": "HYPOTHESIS", "T7": "UNKNOWN", "T8": "UNKNOWN"})
        reasons = self.consume(self.sign(verdict), enabled=True)[1]
        self.assertIn("approv", " ".join(reasons).lower())  # the verdict's own main_rules gate also holds

    def test_same_producer_output_promotes_only_after_both_sides_agree(self):
        # The producer is the source of facts; the consumer re-derives the gate. Enabling the consumer flag without
        # the approval boundary never promotes, and the approval boundary without the flag never promotes either.
        signed = self.sign(produce(approvals=1))
        self.assertNotIn("VERIFIED", self.consume(signed, enabled=False)[0].values())
        self.assertIn("VERIFIED", self.consume(signed, enabled=True)[0].values())
        below = self.sign(produce(approvals=0))
        self.assertNotIn("VERIFIED", self.consume(below, enabled=True)[0].values())


class NegativeTests(Base):
    def test_forged_or_tampered_verdict_is_blocked(self):
        target = cv.encode(produce(approvals=1))
        self.sign(produce(approvals=0))  # the fake signer only knows a *different* verdict's digest
        self.assertBlocked(target)  # this verdict was never signed -> no matching attestation
        data = self.sign(produce(approvals=1))
        tampered = data.replace(b'"run_id":' + str(RUN_ID).encode(), b'"run_id":99')
        self.assertNotEqual(tampered, data)
        self.assertBlocked(tampered)  # one byte changed after signing -> subject digest mismatch

    def test_unsigned_verdict_is_blocked(self):
        self.assertBlocked(cv.encode(produce(approvals=1)))  # never registered with the fake signer

    def test_wrong_commit_workflow_or_run_signature_is_blocked(self):
        verdict = produce(approvals=1)
        self.assertBlocked(self.sign(verdict, sourceRepositoryDigest=OTHER_COMMIT))
        self.assertBlocked(self.sign(verdict, buildSignerURI=f"https://github.com/{REPO}/.github/workflows/ci.yml@refs/heads/main"))
        self.assertBlocked(self.sign(verdict, run_id=99))

    def test_expired_verdict_is_blocked(self):
        signed = self.sign(produce(approvals=1))
        beyond = NOW + datetime.timedelta(days=8)  # producer VALIDITY is 7 days
        with mock.patch.object(vc, "_now", lambda: beyond):
            self.assertBlocked(signed)

    def test_t6_to_t8_never_promote_in_the_round_trip(self):
        verdict = produce(approvals=1)
        for claim, value in (("T6", "VERIFIED"), ("T7", "VERIFIED"), ("T8", "HYPOTHESIS")):
            broken = json.loads(cv.encode(verdict))
            broken["claims"][claim] = value
            self.assertBlocked(self.gh.sign(cv.encode(broken)))

    def test_evidence_and_signature_are_separate(self):
        # A valid signature over a BLOCKED producer verdict never promotes: authenticity does not create facts.
        verdict = produce(approvals=0)
        broken = json.loads(cv.encode(verdict))
        broken["main_rules"]["required_approving_review_count"] = 1  # claim the boundary without the producer's facts
        # the verdict must be re-signed to reach the gate; the producer's own claims stay BLOCKED
        self.assertBlocked(self.gh.sign(cv.encode(broken)))


if __name__ == "__main__":
    unittest.main()
