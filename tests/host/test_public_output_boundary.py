"""Public output boundary (owner rulings, Issues #324 and #476).

Checks, kept apart:

B. Structure. A public JSON document must not carry a field that holds private-image detail.
C. Claims. A line that asserts the private reference BIOS was run or observed is refused (heuristic, not the
   security boundary; the allowlist in tools/public_api_ledger.py is the boundary for the public ledger).
D. Candidate entries and the stdout/stderr boundary of the local tools.

Value leakage is NOT detected here. A detector over short private values (identity prefixes, addresses, flags) is
invertible by enumeration, so it cannot be stored in the repository as a digest, and a literal would itself be a
leak. The public boundary for values is the allowlist (tools/public_api_ledger.py) and the rule that private
values are never written into tracked files. This is recorded as a residual risk, not as a passed check.

Host only. No ROM and no private image is read.
"""
from pathlib import Path
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import abi1_reference_review as review  # noqa: E402
import reference_rom_static_audit as audit  # noqa: E402
import reproduce_static_triage  # noqa: E402
from build_rom import FIRST_ENTRY  # noqa: E402

class CandidateEntryRefused(Exception):
    """A candidate_entry value that the layout rule does not support."""


# The only files that may carry candidate_entry. A value there is allowed only when its slot matches and the value is
# the DEV ROM generated-table entry for that slot (tools/build_rom.py FIRST_ENTRY + 3 x slot). Owner ruling (2026-10-30):
# this is a DEV table structure rule, not reference-BIOS ABI evidence, and it does not verify any entry of the reference BIOS.
CANDIDATE_ENTRY_CARRIERS = {"spec/api_contracts.json", "spec/reference_jsr_gate.json"}


def layout_entry(slot):
    return f"0x{FIRST_ENTRY + 3 * slot:04X}"


def without_verified_candidate_entries(name, document):
    """Remove candidate_entry only from a carrier, and only where it is the slot's layout entry. Refused otherwise.

    A file that is not a carrier is returned unchanged, so a candidate_entry in it is refused by the key check.
    """
    if name not in CANDIDATE_ENTRY_CARRIERS:
        return document
    if name == "spec/api_contracts.json":
        slots = [(index, api) for index, api in enumerate(document["apis"])]
        for index, api in slots:
            if api.get("slot") != index:
                raise CandidateEntryRefused(f"{name}: slot number differs from its position")
    else:
        slots = [(int(key), record) for key, record in document["slots"].items()]
    for slot, record in slots:
        if "candidate_entry" in record:
            if not 0 <= slot < 81:
                raise CandidateEntryRefused(f"{name}: slot outside the 81 slots")
            if record["candidate_entry"] != layout_entry(slot):
                raise CandidateEntryRefused(f"{name}: candidate_entry differs from the layout rule for its slot")
            del record["candidate_entry"]
    return document


class PublicJsonStructure(unittest.TestCase):
    def test_public_json_carries_no_private_detail_field(self):
        public = list((ROOT / "spec").glob("*.json")) + list((ROOT / "docs" / "evidence").glob("*.json"))
        self.assertTrue(public)
        for path in public:
            name = path.relative_to(ROOT).as_posix()
            with self.subTest(path=name):
                document = json.loads(path.read_text(encoding="utf-8"))
                review._public_keys(without_verified_candidate_entries(name, document))

    def test_the_candidate_entry_carriers_are_exactly_the_recorded_set(self):
        carriers = set()
        for path in list((ROOT / "spec").glob("*.json")) + list((ROOT / "docs" / "evidence").glob("*.json")):
            if '"candidate_entry"' in path.read_text(encoding="utf-8"):
                carriers.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(CANDIDATE_ENTRY_CARRIERS, carriers)

    def test_a_private_detail_field_is_refused_at_any_depth(self):
        for key in ("callee_instructions", "hazard_counts", "static_verdict", "static_items", "mpr_write"):
            with self.subTest(key=key):
                with self.assertRaises(review.Invalid):
                    review._public_keys({"deep": [{"node": {key: 1}}]})


# --- C. claims of execution or observation ---------------------------------------------------------------------------
BIOS_SUBJECT = re.compile(
    r"reference BIOS|private BIOS|private reference|retail BIOS|private callee|"
    r"参照 ?BIOS|私的 ?BIOS|私有の?参照|私的参照|非公開 ?BIOS|非公開の参照", re.IGNORECASE)
CLAIM_VERB = re.compile(
    r"\bmeasured\b|\bobserved\b|\bexecuted\b|\bexercised\b|\breproduced\b|\bconfirmed\b|\bran\b|\breturned\b|"
    r"\breached\b|実測|観測|実行|到達|戻る|戻り|再現|確認", re.IGNORECASE)
NEGATED = re.compile(
    r"\bnot\b|\bno\b|\bnone\b|\bnever\b|withheld|withdrawn|must not|BLOCKED|UNVERIFIED|NOT[ _]RUN|"
    r"取り下げ|非公開|公開しない|記載しない|公開せ|含めない|ではない|しない|未|不可|禁止|除く|拒否|撤回",
    re.IGNORECASE)
PUBLIC_RECORD_FILES = ("docs/abi-contracts.md", "spec/abi1_reference_review.json")


def behavior_claims(text):
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        if BIOS_SUBJECT.search(line) and CLAIM_VERB.search(line) and not NEGATED.search(line):
            hits.append((number, line))
    return hits


class BehaviorClaims(unittest.TestCase):
    def test_public_record_asserts_no_execution_or_observation_of_the_private_bios(self):
        hits = []
        for name in PUBLIC_RECORD_FILES:
            for number, _ in behavior_claims((ROOT / name).read_text(encoding="utf-8")):
                hits.append(f"{name}:{number}")
        self.assertEqual([], hits)

    def test_detector_refuses_a_claim_of_execution(self):
        for sample in ("The reference BIOS was measured locally and returned X=3.",
                       "The private BIOS callee was executed on the pinned core.",
                       "実測: 参照 BIOS の入口で戻りを確認した。"):
            with self.subTest(sample=sample):
                self.assertEqual(1, len(behavior_claims(sample)))

    def test_detector_accepts_negated_and_dev_statements(self):
        for sample in ("No reference BIOS result is stated here.",
                       "The reference BIOS was not executed; NOT_RUN for every slot.",
                       "The DEV implementation returned X=3 and Y=0 for slot $1E.",
                       "参照 BIOS の観測値は取り下げ、本書に記載しない。"):
            with self.subTest(sample=sample):
                self.assertEqual([], behavior_claims(sample))


# --- candidate entries: the layout rule, and refusal of anything the rule does not support --------------------------
# The author's own labels only. Slot 74 is an unresolved pair, checked by the public ledger tests, not here.
UNCONFIRMED_NAME_SLOTS = (77, 78, 79, 80)


class CandidateEntryRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        cls.gate = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))

    def test_every_candidate_entry_follows_the_layout_rule(self):
        self.assertEqual(len(self.contracts["apis"]), 81)
        for slot, api in enumerate(self.contracts["apis"]):
            with self.subTest(slot=slot):
                self.assertEqual(api["candidate_entry"], layout_entry(slot))

    def test_the_reference_gate_entries_follow_the_layout_rule_for_their_own_slot(self):
        checked = 0
        for key, record in self.gate["slots"].items():
            if "candidate_entry" in record:
                checked += 1
                with self.subTest(slot=key):
                    self.assertEqual(record["candidate_entry"], layout_entry(int(key)))
        self.assertGreaterEqual(checked, 1)

    def test_unconfirmed_names_are_marked_unknown_in_the_existing_ledger(self):
        for slot in UNCONFIRMED_NAME_SLOTS:
            with self.subTest(slot=slot):
                self.assertEqual(self.contracts["apis"][slot]["confidence"], "UNKNOWN")

    def test_a_value_for_another_slot_is_refused(self):
        document = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        document["apis"][6]["candidate_entry"] = layout_entry(5)
        with self.assertRaises(CandidateEntryRefused):
            without_verified_candidate_entries("spec/api_contracts.json", document)

    def test_an_unknown_value_is_refused(self):
        document = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        document["apis"][0]["candidate_entry"] = "0xEFFF"
        with self.assertRaises(CandidateEntryRefused):
            without_verified_candidate_entries("spec/api_contracts.json", document)

    def test_an_unknown_gate_slot_key_is_refused(self):
        document = json.loads((ROOT / "spec" / "reference_jsr_gate.json").read_text(encoding="utf-8"))
        document["slots"]["99"] = {"candidate_entry": layout_entry(99)}
        with self.assertRaises(CandidateEntryRefused):
            without_verified_candidate_entries("spec/reference_jsr_gate.json", document)

    def test_a_candidate_entry_in_a_non_carrier_file_is_refused_by_the_key_check(self):
        document = {"apis": [{"slot": 0, "candidate_entry": layout_entry(0)}]}
        stripped = without_verified_candidate_entries("spec/some_other.json", document)
        with self.assertRaises(review.Invalid):
            review._public_keys(stripped)

    def test_a_verified_entry_is_removed_and_the_other_fields_stay(self):
        document = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        stripped = without_verified_candidate_entries("spec/api_contracts.json", document)
        self.assertNotIn("candidate_entry", stripped["apis"][0])
        self.assertEqual(stripped["apis"][0]["candidate_name"], document["apis"][0]["candidate_name"])


# --- stdout, stderr and the local output boundary --------------------------------------------------------------------
class LocalOutputStaysOutside(unittest.TestCase):
    def test_auditor_refuses_an_output_inside_the_repository(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = audit.main(["--rom", str(ROOT / "missing.bin"), "--output", str(ROOT / "out.json")])
        self.assertEqual(rc, 3)

    def test_the_auditor_never_prints_a_result_to_stdout(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cases = (
            ["--rom", str(ROOT / "missing.bin"), "--output", str(Path(tmp.name) / "a.json")],
            ["--rom", str(ROOT / "missing.bin")],
            ["--rom", str(ROOT / "missing.bin"), "--output", str(ROOT / "out.json")],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    audit.main(argv)
                self.assertEqual("", out.getvalue())

    def test_an_exception_message_is_not_echoed_to_stderr(self):
        # A synthetic secret planted in an exception message must not reach the terminal or a log.
        secret = "SYNTHETIC-SECRET-7f3a-not-a-real-value"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(audit, "audit_image", side_effect=OSError(secret)), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = audit.main(["--rom", str(ROOT / "missing.bin"), "--output", str(Path(tmp.name) / "a.json")])
        self.assertEqual(rc, 2)
        self.assertNotIn(secret, err.getvalue() + out.getvalue())

    def test_reproduce_refuses_a_baseline_inside_the_repository(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = reproduce_static_triage.main(["--rom", str(ROOT / "missing.bin"),
                                               "--baseline", str(ROOT / "README.md")])
        self.assertEqual(rc, 2)

    def test_without_a_baseline_the_result_is_not_verified(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rom = Path(tmp.name) / "rom.bin"
        rom.write_bytes(bytes(range(256)) * 1024)

        def auditor(cmd, **kw):
            Path(cmd[cmd.index("--output") + 1]).write_text(
                '{"apis": [], "jump_table": {}, "schema": "x"}', encoding="utf-8")
            return mock.Mock(returncode=0, stderr="")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(reproduce_static_triage, "BASELINE", None), \
                mock.patch("reproduce_static_triage.subprocess.run", side_effect=auditor), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = reproduce_static_triage.main(["--rom", str(rom)])
        self.assertEqual(rc, 3)
        self.assertIn("NOT_VERIFIED", err.getvalue())
        self.assertNotIn("PASS:", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
