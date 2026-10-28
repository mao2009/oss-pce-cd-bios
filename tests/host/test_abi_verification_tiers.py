#!/usr/bin/env python3
"""Negative tests for the ABI verification tiers (Issue #324, design: docs/abi-verification-tiers.md).

Host-only. No real core, no reference BIOS, no CI artifacts. Fixtures are synthetic and live in this file.

Four axes are kept apart: SOURCE_EVIDENCE, PROVENANCE_APPROVAL, CALLEE_VERIFICATION and DEV_IMPLEMENTATION.
Nothing from one axis may reach VERIFIED on the reference ABI. The tests below try to promote from each
kind of input (forged evidence, self-declared approval, DEV results, static analysis) and must fail.

Known gap (expectedFailure, not a passing claim): dev_implementations.validate() accepts any non-empty
approval strings, so a self-declared approver is not refused. Fixing it needs an owner-approved approver
list, which is out of scope for this change.
"""
import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import abi1_reference_review as review  # noqa: E402
import contracts  # noqa: E402
import dev_implementations as dev  # noqa: E402
from build_rom import load_slots, overrides  # noqa: E402

SLOTS = load_slots()
OVERRIDES = set(overrides())
DESIGN_DOC = ROOT / "docs" / "abi-verification-tiers.md"

SYNTHETIC_DIGEST = hashlib.sha256(b"synthetic evidence artifact, not a measurement").hexdigest()


def observed_claim(*, sources=("geargrafx",), kind="CALLEE_EXECUTION", execution_conditions="REALCORE geargrafx run"):
    """A claim shaped like a real-core OBSERVED claim. Shape only: nothing here was executed."""
    return {
        "status": "OBSERVED",
        "value": "forged",
        "sources": list(sources),
        "confidence": "SOURCE_SUPPORTED",
        "evidence": [{
            "execution_conditions": execution_conditions,
            "core_revision": contracts.PINNED_CORE_REVISION,
            "input_type": "ROM test input",
            "verification_result": "PASS",
            "evidence_hash": SYNTHETIC_DIGEST,
            # Issue #364 / #515: a registered point whose kind and fields match, so the refusal under test is the
            # authentication gate (#124), not the point check.
            "measurement_point_id": "CALLEE_EXECUTION_SLOT",
        }],
        "observation_kind": kind,
    }


SOURCE_KINDS = {"geargrafx": "EMULATOR_SOURCE", "caller": "CALLER_SOURCE"}


def adjudicate(claim, *, field="returns", executed_callee=True, hashes=frozenset({SYNTHETIC_DIGEST})):
    return contracts.adjudicate_claim(claim, SOURCE_KINDS, field=field, context="tiers",
                                      executed_callee=executed_callee, resolved_evidence_hashes=hashes)


def base_ledger():
    return dev.load_ledger()


def base_review():
    return review.load()[0]


class ForgedEvidenceTests(unittest.TestCase):
    """Well-formed, hash-matching, pinned-revision evidence still does not reach OBSERVED or VERIFIED."""

    def assertRefusedAsUnauthenticated(self, claim, **kwargs):
        with self.assertRaises(ValueError) as caught:
            adjudicate(claim, **kwargs)
        self.assertIn("not authenticated", str(caught.exception))

    def test_hash_match_and_pinned_revision_do_not_authenticate(self):
        self.assertRefusedAsUnauthenticated(observed_claim())

    def test_self_declared_realcore_marker_does_not_authenticate(self):
        claim = observed_claim(execution_conditions="REALCORE, produced by the core")
        self.assertRefusedAsUnauthenticated(claim)

    def test_executed_callee_false_is_refused_before_authentication(self):
        # Without an executed callee the claim never reaches the authentication gate; the flag is necessary, not sufficient.
        with self.assertRaisesRegex(ValueError, "requires an executed callee"):
            adjudicate(observed_claim(), executed_callee=False)

    def test_empty_resolved_hash_set_is_refused_before_authentication(self):
        with self.assertRaisesRegex(ValueError, "resolved evidence set is empty"):
            adjudicate(observed_claim(), hashes=frozenset())

    def test_verified_status_is_never_representable(self):
        claim = observed_claim()
        claim["status"] = "VERIFIED"
        del claim["observation_kind"]
        with self.assertRaisesRegex(ValueError, "VERIFIED is not represented"):
            adjudicate(claim)

    def test_resolved_artifact_is_not_observation(self):
        # resolve_evidence() only checks bytes against a hash and a pinned revision (ARTIFACT_HASH_RESOLVED).
        # Its success must not be readable as an OBSERVED claim.
        data = b"artifact bytes, checked against a hash only"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / digest).write_bytes(data)
            entry = observed_claim()["evidence"][0]
            entry["evidence_hash"] = digest
            self.assertEqual(Path(directory) / digest,
                             contracts.resolve_evidence(entry, artifact_dir=Path(directory)))
            claim = observed_claim()
            claim["evidence"] = [entry]
            self.assertRefusedAsUnauthenticated(claim, hashes=frozenset({digest}))


class SelfDeclaredApprovalTests(unittest.TestCase):
    """A status, approval or DEV_CALLABLE declaration written by the claim itself is not approval."""

    def test_approval_key_inside_a_contract_claim_is_refused(self):
        claim = {"status": "HYPOTHESIS", "value": "x", "sources": ["caller"],
                 "confidence": "SOURCE_SUPPORTED", "approval": {"approved_by": "self"}}
        with self.assertRaisesRegex(ValueError, "unknown claim keys"):
            adjudicate(claim, executed_callee=False)

    def test_dev_callable_without_owner_approval_fields_fails(self):
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": None, "review_ref": None}
        self.assertTrue(any("approved_by" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_with_half_approval_fails(self):
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": "owner", "review_ref": None}
        self.assertTrue(any("approved_by" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    @unittest.expectedFailure
    def test_self_declared_approver_is_refused(self):
        # Known gap: the validator accepts any non-empty string. Expected to fail until an approver list exists.
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": "worker-agent", "review_ref": "self"}
        self.assertTrue(any("approv" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_committed_ledgers_have_no_owner_approved_dev_callable(self):
        # The committed ledger has no owner-approved DEV_CALLABLE row (summary counts, read-only).
        summary = dev.summary(base_ledger(), OVERRIDES)
        self.assertEqual(0, summary["dev_callable_approved"])
        self.assertEqual(0, summary["abi_verified"])
        self.assertIs(False, summary["release_eligible"])


class DevResultTests(unittest.TestCase):
    """A DEV implementation, even when approved and tested, is not a reference ABI VERIFIED."""

    def test_dev_callable_with_verified_abi_status_is_refused(self):
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        entry["abi_contract"]["status"] = "VERIFIED"
        self.assertTrue(any("VERIFIED" in p for p in dev.validate(ledger, SLOTS, OVERRIDES)))

    def test_dev_callable_keeps_abi_status_non_verified(self):
        # Positive control: an approved DEV row with the project-spec status is accepted and still is not VERIFIED.
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        self.assertEqual([], dev.validate(ledger, SLOTS, OVERRIDES))
        self.assertEqual(0, dev.summary(ledger, OVERRIDES)["abi_verified"])

    def test_dev_callable_is_not_absorbed_into_the_static_review(self):
        # The review cross-checks its committed summary against the DEV ledger. An approved DEV row changes that
        # count, so the review must refuse rather than silently count DEV approval as reference verification.
        ledger = base_ledger()
        entry = ledger["slots"][0x1E]
        entry["state"] = "DEV_CALLABLE"
        entry["approval"] = {"approved_by": "owner", "review_ref": "issue-324"}
        with self.assertRaisesRegex(review.Invalid, "owner_approved_dev_callable"):
            review.validate(base_review(), contracts.load_contracts(), ledger)


class StaticAnalysisTests(unittest.TestCase):
    """Static analysis (source text, disassembly, safety verdicts) is never a verification."""

    def test_caller_source_cannot_be_observed(self):
        with self.assertRaisesRegex(ValueError, "EMULATOR_SOURCE"):
            adjudicate(observed_claim(sources=("caller",)))

    def test_static_verdict_cannot_be_verified(self):
        document = copy.deepcopy(base_review())
        document["apis"][0]["abi_state"] = "VERIFIED"
        with self.assertRaisesRegex(review.Invalid, "VERIFIED"):
            review.validate(document, contracts.load_contracts(), base_ledger())

    def test_static_review_cannot_be_observed_or_partial_without_real_core_run(self):
        for state in ("OBSERVED", "PARTIAL"):
            with self.subTest(state=state):
                document = copy.deepcopy(base_review())
                document["apis"][0]["abi_state"] = state
                with self.assertRaisesRegex(review.Invalid, "reproducible real core"):
                    review.validate(document, contracts.load_contracts(), base_ledger())

    def test_static_execution_safe_is_not_permission(self):
        # A static EXECUTION_SAFE verdict is a profile, not an execution permission.
        document = copy.deepcopy(base_review())
        document["apis"][0]["safety"]["static_verdict"] = "EXECUTION_SAFE"
        document["apis"][0]["safety"]["execution_permission"] = "ALLOWED"
        with self.assertRaisesRegex(review.Invalid, "execution permission"):
            review.validate(document, contracts.load_contracts(), base_ledger())


class DesignDocumentTests(unittest.TestCase):
    """The design document must name the vocabulary the code actually uses."""

    def setUp(self):
        self.text = DESIGN_DOC.read_text(encoding="utf-8")

    def test_document_names_the_four_axes(self):
        for axis in ("SOURCE_EVIDENCE", "PROVENANCE_APPROVAL", "CALLEE_VERIFICATION", "DEV_IMPLEMENTATION"):
            self.assertIn(axis, self.text)

    def test_document_names_the_status_vocabulary_in_code(self):
        # Whole-token match: "OBSERVED" must not be satisfied by "OBSERVED_STATIC".
        for state in sorted(review.ABI_STATES | review.ITEM_STATUSES):
            self.assertRegex(self.text, rf"(?<![A-Za-z0-9_]){re.escape(state)}(?![A-Za-z0-9_])", state)
        self.assertIn("VERIFIED-equivalent", self.text)

    def test_document_claims_no_compatibility(self):
        self.assertIn("互換性を主張しない", self.text)
        for phrase in ("互換性がある", "互換性を達成", "compatible System Card BIOS"):
            self.assertNotIn(phrase, self.text)

    def test_owner_items_remain_undecided(self):
        # Requirement 3: section 8 records recommendations only; no decision or approval is written there.
        self.assertIn("以下は未決", self.text)
        section8 = self.text.split("\n## 8.", 1)[1]
        self.assertIn("推奨は提案であり、決定ではない", section8)
        for phrase in ("決定した", "承認した", "採用した", "承認済み"):
            self.assertNotIn(phrase, section8, phrase)

    def test_document_states_it_changes_no_schema_or_gate(self):
        self.assertRegex(self.text, re.compile(r"スキーマ.*変更しない|変更しない.*スキーマ"))
        self.assertIn("release_eligible", self.text)

    def test_document_lists_all_six_owner_undecided_items(self):
        sections = self.text.split("\n### 8.")[1:]
        self.assertEqual(6, len(sections))
        for number, section in enumerate(sections, start=1):
            self.assertTrue(section.startswith(f"{number} "), number)
            for label in ("現状", "推奨", "互換性", "セキュリティ", "必要な人間の承認", "判断前に進めてよいこと"):
                self.assertIn(f"**{label}", section, (number, label))


class OwnerUndecidedStateTests(unittest.TestCase):
    """The undecided tier words are design vocabulary only: they must not be stored or accepted as claim status."""

    def test_partial_claim_status_is_refused(self):
        claim = {"status": "PARTIAL", "value": "x", "sources": ["caller"], "confidence": "SOURCE_SUPPORTED"}
        with self.assertRaisesRegex(ValueError, "unsupported claim status"):
            adjudicate(claim, executed_callee=False)

    def test_verified_equivalent_claim_status_is_refused(self):
        claim = {"status": "VERIFIED-equivalent", "value": "x", "sources": ["caller"],
                 "confidence": "SOURCE_SUPPORTED"}
        with self.assertRaisesRegex(ValueError, "unsupported claim status"):
            adjudicate(claim, executed_callee=False)

    def test_dev_ledger_states_have_no_tier_values(self):
        self.assertEqual({"NOT_READY", "DEV_CALLABLE", "BLOCKED"}, set(dev.STATES))


if __name__ == "__main__":
    unittest.main()
