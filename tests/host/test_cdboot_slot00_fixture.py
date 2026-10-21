"""Host tests for the slot-$00 (CD_BOOT, candidate $E000) non-executing fixtures (Issue #290).

Synthetic only: media is built in memory, no ROM, disc or reference BIOS is read, and the slot is
never called. Truthful self-reports must be MODEL_CONSISTENT (never PASS); spoofed or contradictory
records must be REJECTED; malformed, missing or unknown records must be BLOCKED (fail closed).
"""
import copy
import hashlib
import importlib.util
import itertools
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cdboot_slot00_fixture", ROOT / "tools" / "cdboot_slot00_fixture.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def truthful(case):
    """Evidence an honest self-report would carry for this case (derived from the case itself)."""
    boot, error = M.EXPECTED[case["model_classification"]]
    return {"schema": M.EVIDENCE_SCHEMA, "slot": M.SLOT, "media_sha256": case["expected_media_sha256"],
            "boot_claimed": boot, "error_code": error, "executed_callee": False}


CASE = {kind: next(c for c in M.CASES if c["media_kind"] == kind) for kind in M.MEDIA_KINDS}


class Slot00FixtureTests(unittest.TestCase):
    def test_three_cases_are_definition_only_and_unknown(self):
        self.assertEqual([c["media_kind"] for c in M.CASES], list(M.MEDIA_KINDS))
        for case in M.CASES:
            with self.subTest(case=case["case_id"]):
                self.assertEqual(M.validate_case(case), [])
                self.assertEqual((case["slot"], case["entry_candidate"], case["execution"]),
                                 ("$00", "$E000", "NOT_RUN"))

    def test_media_classification_is_independent_of_the_fixture_label(self):
        self.assertEqual(M.classify_media(M.synthetic_media("valid_synthetic")), "BOOT_CANDIDATE")
        self.assertEqual(M.classify_media(M.synthetic_media("malformed")), "MALFORMED")
        self.assertEqual(M.classify_media(None), "NO_MEDIA")
        self.assertIsNone(CASE["absent"]["expected_media_sha256"])
        self.assertNotEqual(CASE["valid_synthetic"]["expected_media_sha256"],
                            CASE["malformed"]["expected_media_sha256"])

    def test_synthetic_media_is_not_an_iso_or_sync_pattern(self):
        sector = M.synthetic_media("valid_synthetic")
        self.assertEqual(len(sector), M.SECTOR)
        self.assertNotIn(b"CD001", sector)
        self.assertNotIn(bytes([0x00]) + bytes([0xFF]) * 10, sector)

    def test_truthful_self_report_is_model_consistent_never_pass(self):
        for kind, case in CASE.items():
            with self.subTest(kind=kind):
                status, reasons = M.judge(case, truthful(case))
                self.assertEqual(status, "MODEL_CONSISTENT")
                self.assertIn("not execution evidence", "; ".join(reasons))
                self.assertNotEqual(status, "PASS")

    def test_spoofed_or_contradictory_evidence_is_rejected(self):
        cases = {
            "valid media claimed for absent": ("absent", {"media_sha256": CASE["valid_synthetic"]["expected_media_sha256"], "boot_claimed": True, "error_code": None}),
            "boot claimed for absent media": ("absent", {"boot_claimed": True, "error_code": None}),
            "absent media without NO_MEDIA": ("absent", {"error_code": None}),
            "boot claimed for malformed media": ("malformed", {"boot_claimed": True, "error_code": None}),
            "malformed claimed as valid sha": ("malformed", {"media_sha256": CASE["valid_synthetic"]["expected_media_sha256"]}),
            "valid media not booting": ("valid_synthetic", {"boot_claimed": False, "error_code": "NO_MEDIA"}),
            "valid media with error": ("valid_synthetic", {"error_code": "MALFORMED"}),
        }
        for label, (kind, overrides) in cases.items():
            with self.subTest(label):
                record = dict(truthful(CASE[kind]), **overrides)
                status, _ = M.judge(CASE[kind], record)
                self.assertEqual(status, "REJECTED")

    def test_malformed_missing_or_unknown_evidence_is_blocked(self):
        base = truthful(CASE["valid_synthetic"])
        dropped = {k: v for k, v in base.items() if k != "error_code"}
        bad_records = {
            "no record": None,
            "field dropped": dropped,
            "extra field": dict(base, instructions="JSR $E000"),
            "not a dict": ["boot"],
            "executed claim": dict(base, executed_callee=True),
            "executed as int": dict(base, executed_callee=0),
            "unknown result": dict(base, error_code=M.UNKNOWN),
            "unrecognised error": dict(base, error_code="PASS"),
            "boot as string": dict(base, boot_claimed="yes"),
            "uppercase sha": dict(base, media_sha256=str(base["media_sha256"]).upper()),
            "short sha": dict(base, media_sha256="ab"),
            "wrong schema": dict(base, schema="oss-pce-cd-bios/other/v9"),
            "wrong slot": dict(base, slot="$01"),
        }
        for label, record in bad_records.items():
            with self.subTest(label):
                status, _ = M.judge(CASE["valid_synthetic"], record)
                self.assertEqual(status, "BLOCKED")

    def test_malformed_case_fails_closed(self):
        broken = copy.deepcopy(CASE["valid_synthetic"])
        broken["execution"] = "PASS"
        broken["boot_target"] = "$4000"
        status, _ = M.judge(broken, truthful(CASE["valid_synthetic"]))
        self.assertEqual(status, "BLOCKED")
        self.assertTrue(M.validate_case(broken))

    def test_unhashable_or_non_string_results_fail_closed_not_raise(self):
        base = truthful(CASE["valid_synthetic"])
        for label, value in (("list", []), ("dict", {}), ("int", 0)):
            with self.subTest(label):
                status, _ = M.judge(CASE["valid_synthetic"], dict(base, error_code=value))
                self.assertEqual(status, "BLOCKED")

    def test_unhashable_or_mismatched_model_classification_is_blocked_by_gate(self):
        for kind in M.MEDIA_KINDS:
            with self.subTest(kind=kind, label="unhashable"):
                case = copy.deepcopy(CASE[kind])
                case["model_classification"] = []
                self.assertTrue(M.validate_case(case))
                self.assertEqual(M.judge(case, truthful(CASE[kind]))[0], "BLOCKED")
        case = copy.deepcopy(CASE["malformed"])
        case["model_classification"] = "BOOT_CANDIDATE"
        self.assertTrue(M.validate_case(case), "classification must match its media")
        self.assertEqual(M.judge(case, truthful(CASE["malformed"]))[0], "BLOCKED")

    def test_tampered_expected_media_sha_is_blocked_by_gate(self):
        for kind in M.MEDIA_KINDS:
            for tampered in (None, "ab", CASE["valid_synthetic"]["expected_media_sha256"]):
                if tampered == CASE[kind]["expected_media_sha256"]:
                    continue
                with self.subTest(kind=kind, tampered=tampered):
                    case = copy.deepcopy(CASE[kind])
                    case["expected_media_sha256"] = tampered
                    self.assertTrue(M.validate_case(case), "expected SHA must be derived from the media")
                    record = dict(truthful(CASE[kind]), media_sha256=tampered)
                    self.assertEqual(M.judge(case, record)[0], "BLOCKED")

    def test_expectations_are_literal_not_derived_from_judge_table(self):
        literal = {"valid_synthetic": (True, None), "absent": (False, "NO_MEDIA"), "malformed": (False, "MALFORMED")}
        sector = (b"OSS-PCE-SYNTH-CD-BOOT\0" + b"synthetic-payload-only").ljust(2048, b"\0")
        for kind, (boot, error) in literal.items():
            with self.subTest(kind=kind):
                case = CASE[kind]
                if kind == "valid_synthetic":
                    self.assertEqual(case["expected_media_sha256"], hashlib.sha256(sector).hexdigest())
                record = {"schema": M.EVIDENCE_SCHEMA, "slot": "$00", "media_sha256": case["expected_media_sha256"],
                          "boot_claimed": boot, "error_code": error, "executed_callee": False}
                self.assertEqual(M.judge(case, record)[0], "MODEL_CONSISTENT")

    def test_slot_execution_is_always_blocked(self):
        status, reasons = M.execution_status()
        self.assertEqual(status, "BLOCKED")
        self.assertIn("BLOCKED_ABI", "; ".join(reasons))
        self.assertIn("no System Card compatibility claimed", "; ".join(reasons))

    def test_module_has_no_execution_or_media_read_path(self):
        source = (ROOT / "tools" / "cdboot_slot00_fixture.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "os.system", "popen(", "read_bytes", "open("):
            self.assertNotIn(banned, source)

    def test_outputs_never_claim_pass_or_compatibility(self):
        outputs = [M.execution_status()[1], [M.judge(c, truthful(c))[1][0] for c in M.CASES]]
        text = repr(outputs)
        for banned in ("VERIFIED", "SYSCARD_COMPAT", "PASS", "ALLOWED"):
            self.assertNotIn(banned, text)

    def test_cli_is_definition_only_and_exits_blocked(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "cdboot_slot00_fixture.py"), "--check"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, M.EXIT_BLOCKED, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("NOT_RUN: "), result.stdout)
        self.assertTrue(lines[1].startswith("BLOCKED: "), result.stdout)
        self.assertNotIn("PASS", result.stdout)


def boundary_variants():
    """Media edge cases around the one valid sector: (name, raw bytes or None, literal model class)."""
    sector = M.synthetic_media("valid_synthetic")
    return [
        ("absent", None, "NO_MEDIA"),
        ("empty_file", b"", "MALFORMED"),
        ("magic_only", M.MAGIC, "MALFORMED"),
        ("short_by_one", sector[:-1], "MALFORMED"),
        ("long_by_one", sector + b"\0", "MALFORMED"),
        ("magic_offset_one", b"\0" + sector[:-1], "MALFORMED"),
        ("exact_sector", sector, "BOOT_CANDIDATE"),
        ("last_byte_changed", sector[:-1] + b"\x01", "BOOT_CANDIDATE"),
    ]


def sha_or_none(media):
    return None if media is None else hashlib.sha256(media).hexdigest()


UNKNOWN_FIELDS = ("boot_target", "return_path", "stack", "irq")


class Slot00MediaBoundaryTests(unittest.TestCase):
    def test_classification_at_sector_edges(self):
        for name, media, expected in boundary_variants():
            with self.subTest(name):
                self.assertEqual(M.classify_media(media), expected)

    def test_absent_and_empty_media_are_distinct(self):
        self.assertEqual(M.classify_media(None), "NO_MEDIA")
        self.assertEqual(M.classify_media(b""), "MALFORMED")
        self.assertNotEqual(sha_or_none(b""), CASE["absent"]["expected_media_sha256"])
        # An empty file is not "no medium": a NO_MEDIA claim backed by its hash must be rejected.
        record = dict(truthful(CASE["absent"]), media_sha256=sha_or_none(b""))
        self.assertEqual(M.judge(CASE["absent"], record)[0], "REJECTED")
        # Absent media reported for the malformed case contradicts its identity.
        record = {"schema": M.EVIDENCE_SCHEMA, "slot": M.SLOT, "media_sha256": None,
                  "boot_claimed": False, "error_code": "NO_MEDIA", "executed_callee": False}
        self.assertEqual(M.judge(CASE["malformed"], record)[0], "REJECTED")

    def test_only_the_exact_valid_sector_is_boot_consistent(self):
        valid = CASE["valid_synthetic"]
        for name, media, _ in boundary_variants():
            with self.subTest(name):
                status, _ = M.judge(valid, dict(truthful(valid), media_sha256=sha_or_none(media)))
                self.assertEqual(status, "MODEL_CONSISTENT" if name == "exact_sector" else "REJECTED")

    def test_neighbouring_media_hashes_are_distinct(self):
        hashes = [sha_or_none(media) for _, media, _ in boundary_variants() if media is not None]
        self.assertEqual(len(hashes), len(set(hashes)))


class Slot00UnknownVsBlockedTests(unittest.TestCase):
    def test_unmeasured_fields_stay_unknown_and_valid(self):
        for kind, case in CASE.items():
            with self.subTest(kind=kind):
                for field in UNKNOWN_FIELDS:
                    self.assertEqual(case[field], M.UNKNOWN)
                self.assertEqual(M.validate_case(case), [])

    def test_measured_value_in_an_unknown_field_blocks_the_judge(self):
        for field in UNKNOWN_FIELDS:
            for value in ("$4000", None, 0, ""):
                with self.subTest(field=field, value=value):
                    case = copy.deepcopy(CASE["valid_synthetic"])
                    case[field] = value
                    self.assertTrue(M.validate_case(case))
                    self.assertEqual(M.judge(case, truthful(CASE["valid_synthetic"]))[0], "BLOCKED")

    def test_unknown_result_is_blocked_while_a_wrong_value_is_rejected(self):
        absent = CASE["absent"]
        self.assertEqual(M.judge(absent, dict(truthful(absent), error_code=M.UNKNOWN))[0], "BLOCKED")
        self.assertEqual(M.judge(absent, dict(truthful(absent), error_code="MALFORMED"))[0], "REJECTED")
        self.assertEqual(M.judge(absent, dict(truthful(absent), boot_claimed=True))[0], "REJECTED")
        self.assertEqual(M.judge(absent, truthful(absent))[0], "MODEL_CONSISTENT")

    def test_missing_or_empty_evidence_is_blocked_for_every_kind(self):
        for kind, case in CASE.items():
            with self.subTest(kind=kind):
                self.assertEqual(M.judge(case, None)[0], "BLOCKED")
                self.assertEqual(M.judge(case, {})[0], "BLOCKED")

    def test_unknown_result_is_blocked_for_every_kind(self):
        for kind, case in CASE.items():
            with self.subTest(kind=kind):
                self.assertEqual(M.judge(case, dict(truthful(case), error_code=M.UNKNOWN))[0], "BLOCKED")


class Slot00NeverReportsSuccessTests(unittest.TestCase):
    def test_exhaustive_grid_only_exact_truth_is_model_consistent(self):
        sha_values = (None, "ab", sha_or_none(b"unrelated"), "0" * 64, "A" * 64, "__expected__")
        boot_values = (True, False, "yes", None)
        error_values = (None, "NO_MEDIA", "MALFORMED", M.UNKNOWN, "PASS", [])
        executed_values = (False, True, 0)
        seen = set()
        for kind, case in CASE.items():
            base = truthful(case)
            for sha, boot, error, executed in itertools.product(sha_values, boot_values, error_values,
                                                                executed_values):
                sha = case["expected_media_sha256"] if sha == "__expected__" else sha
                record = dict(base, media_sha256=sha, boot_claimed=boot, error_code=error, executed_callee=executed)
                status, _ = M.judge(case, record)
                seen.add(status)
                if status == "MODEL_CONSISTENT":
                    self.assertEqual(record, base, f"{kind}: non-truthful record reported consistent")
        self.assertEqual(seen, {"BLOCKED", "REJECTED", "MODEL_CONSISTENT"})

    def test_boot_claim_without_valid_media_is_never_consistent(self):
        for kind in ("absent", "malformed"):
            for error in (None, "NO_MEDIA", "MALFORMED"):
                with self.subTest(kind=kind, error=error):
                    record = dict(truthful(CASE[kind]), boot_claimed=True, error_code=error)
                    self.assertNotEqual(M.judge(CASE[kind], record)[0], "MODEL_CONSISTENT")


if __name__ == "__main__":
    unittest.main()
