"""Host checks for the synthetic CD_FADE (slot $0F, candidate $E02D) fixture (Issue #283).

Synthetic only: the playback context and every expected value are author-defined model
expectations, not measured device or System Card behaviour. The grounded request is a
caller-defined value-domain membership check (``$00``/``$08``/``$0A``/``$0C``/``$0E``,
contract F06 as a caller-side list only): a POSITIVE verdict means the request is a
well-formed caller request inside that list with no semantic claim attached. Target
semantics (PCM/ADPCM note vs CD-DA contradiction, F11/F13), timing (F09/F10), the
``$00`` cancel effect (F08/F37) and stopped-context behaviour (F32-F34) are unmodelled
and stay UNKNOWN, never a refusal or an acceptance. Callee-dependent questions stay
BLOCKED_ABI. No emulator runs, no reference BIOS is read or called
(reference_jsr_count = 0), and nothing under ./rom/ is touched. This is a synthetic
model judgment, not a real-device PASS. Passing these tests validates only this
fixture and its judge. The fixture stays BLOCKED_ABI and release_eligible=false, and
must not be wired into any build, ROM or callable entry.

The global candidate-string occurrence check used by some fixtures is intentionally
not repeated here: $0F/$E02D/CD_FADE already have legitimate pre-existing references
(slot ledger, scenario table, Issue #282 contract tests). Instead the wiring tests
below pin the fixture filename (only this test may reference it) and the absence of
build/ROM/callable markers.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cdfade-slot0f-cases.json"
FIXTURE = ROOT / FIXTURE_REL
SELF_REL = "tests/host/test_cd_fade_fixture.py"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8). Editing the fixture requires updating it here.
FIXTURE_SHA256 = "da29a4fa9479804d17f505e98f74ac269977928a91f73910e14ddca3e3d792bb"
POSITIVE, NEGATIVE, UNKNOWN, BLOCKED = "POSITIVE", "NEGATIVE", "UNKNOWN", "BLOCKED"
TOP_LEVEL_KEYS = {"schema", "issue", "parent_issue", "slot", "entry_candidate", "candidate_name", "abi_status",
                  "release_eligible", "execution", "provenance", "callee_abi", "context", "request_model",
                  "cases"}
APPROVED_PROVENANCE = ("synthetic: author-defined playback context and cases only; no System Card BIOS byte, "
                       "disassembly, retail data, or ./rom/ content; no reference callee is executed")
GROUNDED_DOMAIN = (0x00, 0x08, 0x0A, 0x0C, 0x0E)
POSITIVE_IDS = {"pos_type_00", "pos_type_08", "pos_type_0a", "pos_type_0c", "pos_type_0e"}
NEGATIVE_IDS = {"neg_nocontext", "neg_type_01", "neg_type_09", "neg_type_ff", "neg_negative",
                "neg_bool_type", "neg_float_type", "neg_string_type", "neg_none_type"}
UNKNOWN_IDS = {"unk_target_cdda", "unk_target_pcm_adpcm", "unk_stopped", "unk_cancel_effect",
               "unk_bad_effect_flag"}
BLOCKED_IDS = {"blocked_callee_args", "blocked_callee_nocontext"}
# Negatives/unknowns/blocked that the deliberately lenient judge wrongly accepts (must be exactly these).
# The lenient judge ignores the caller-defined domain, so every in-byte-range out-of-domain
# value slips through, as do the playing-context unknowns and the callee-blocked case that
# still claims a present context. Out-of-byte-range, absent-context, None-type and stopped
# cases do not slip through.
LENIENT_CAUGHT_IDS = {"neg_type_01", "neg_type_09", "neg_type_ff", "neg_bool_type",
                      "neg_float_type", "neg_string_type", "unk_target_cdda",
                      "unk_target_pcm_adpcm", "unk_cancel_effect", "unk_bad_effect_flag",
                      "blocked_callee_args"}


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _well_formed(context):
    # Synthetic-model-only contract (not a device claim): the author-defined playback context
    # carries no measured state. It is well-formed only when it is a dict whose "synthetic"
    # flag is plain True. Anything outside this model (non-dict, non-True flag, wrong types)
    # is not well-formed; judge() reports it as UNKNOWN, never POSITIVE.
    if not isinstance(context, dict):
        return False
    return context.get("synthetic") is True


def judge(context, query, present=True, needs_callee=False):
    """Reference judge: (verdict, detail, result). Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI.

    Precedence (first matching rule decides): malformed control flag -> callee dependence ->
    no context -> malformed context -> malformed type value -> out-of-domain value ->
    unmodelled target -> unmodelled effect claim -> stopped context -> POSITIVE. Control
    flags must be plain bool; any other type is UNKNOWN, never POSITIVE. UNKNOWN is not a
    refusal: unmodelled semantics stay UNKNOWN even when the value is in-domain.
    """
    if type(present) is not bool or type(needs_callee) is not bool:
        return UNKNOWN, "malformed control flag: present and needs_callee must be plain bool", None
    if needs_callee:
        return BLOCKED, "BLOCKED_ABI: the answer would need the callee ABI; reference JSR not called", None
    if not present:
        return NEGATIVE, "no playback context present", None
    if not _well_formed(context):
        return UNKNOWN, "context is outside the synthetic model", None
    type_value = query.get("type_value")
    if type(type_value) is not int:
        return NEGATIVE, "malformed: type_value must be a plain int (bool, float, str, None refused)", None
    if type_value not in GROUNDED_DOMAIN:
        return NEGATIVE, "type value outside the caller-defined domain", None
    if query.get("target", "unspecified") != "unspecified":
        return UNKNOWN, "unmodelled target semantics: PCM/ADPCM note and CD-DA are undecided", None
    asserts_effect = query.get("asserts_effect", False)
    if type(asserts_effect) is not bool:
        return UNKNOWN, "malformed effect flag: asserts_effect must be plain bool", None
    if asserts_effect:
        return UNKNOWN, "unmodelled effect claim: cancel/timing effects are unmeasured", None
    if query.get("playing", True) is not True:
        if type(query.get("playing")) is not bool:
            return UNKNOWN, "malformed playing flag: playing must be plain bool", None
        return UNKNOWN, "stopped-context behavior undecided; not a refusal", None
    return POSITIVE, "well-formed caller request inside the caller-defined domain", {
        "type_value": type_value, "domain": "caller-defined", "return": "void-ignored"}


def lenient_judge(context, query, present=True, needs_callee=False):
    """Deliberately wrong judge: ignores the domain, target/effect/stopped claims and callee
    dependence, and loosely converts bool/float/str type values.

    Accepts any present query whose loose int conversion lands in the byte range while the
    context claims playing. Negatives/unknowns/blocked that slip through must be exactly
    LENIENT_CAUGHT_IDS.
    """
    if not present:
        return NEGATIVE
    raw = query.get("type_value")
    try:
        value = int(raw) if isinstance(raw, (int, float, str)) and not isinstance(raw, bool) else None
        if isinstance(raw, bool):
            value = int(raw)
    except (ValueError, TypeError):
        return NEGATIVE
    if isinstance(value, int) and 0 <= value <= 255 and query.get("playing", True) is True:
        return POSITIVE
    return NEGATIVE


class CdFadeSlot0fFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.context = cls.fixture["context"]
        cls.cases = cls.fixture["cases"]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cdfade-slot0f-fixture/v1")
        self.assertEqual((f["issue"], f["parent_issue"], f["slot"], f["entry_candidate"], f["candidate_name"]),
                         (283, 24, "$0F", "$E02D", "CD_FADE"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertIs(self.context["synthetic"], True)
        self.assertEqual(f["callee_abi"]["reference_jsr"], "NOT_CALLED")
        self.assertEqual(f["callee_abi"]["reference_jsr_count"], 0)
        for field in ("arguments", "returns", "registers", "timing", "target_effect"):
            self.assertEqual(f["callee_abi"][field], "UNKNOWN")

    def test_fixture_hash_and_provenance_are_pinned(self):
        self.assertEqual(canonical_sha256(self.fixture), FIXTURE_SHA256)
        self.assertEqual(set(self.fixture), TOP_LEVEL_KEYS)
        self.assertEqual(self.fixture["provenance"], APPROVED_PROVENANCE)

    def test_grounded_domain_matches_the_caller_side_list(self):
        model = self.fixture["request_model"]
        self.assertEqual(tuple(model["grounded_domain"]), GROUNDED_DOMAIN)
        self.assertEqual(model["grounded_domain_hex"], ["$00", "$08", "$0A", "$0C", "$0E"])
        for key in ("unmodelled_target", "unmodelled_timing", "unmodelled_stopped_behavior",
                    "unmodelled_cancel_effect"):
            self.assertEqual(model[key], "UNKNOWN")
        self.assertEqual(model["callee_dependent"], "BLOCKED_ABI")

    def test_every_case_matches_its_expected_verdict_and_result(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                verdict, detail, result = judge(self.context, case, case.get("present", True),
                                                case.get("needs_callee", False))
                self.assertEqual(verdict, case["expected"])
                if verdict == POSITIVE:
                    self.assertEqual(result, case["result"])
                else:
                    self.assertIsNone(result)
                    self.assertTrue(detail)

    def test_case_mix_covers_all_four_verdicts_and_boundaries(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({c["id"] for c in self.cases if c["expected"] == POSITIVE}, POSITIVE_IDS)
        self.assertEqual({c["id"] for c in self.cases if c["expected"] == NEGATIVE}, NEGATIVE_IDS)
        self.assertEqual({c["id"] for c in self.cases if c["expected"] == UNKNOWN}, UNKNOWN_IDS)
        self.assertEqual({c["id"] for c in self.cases if c["expected"] == BLOCKED}, BLOCKED_IDS)
        by_id = {c["id"]: c["expected"] for c in self.cases}
        for boundary in ("pos_type_00", "pos_type_08", "pos_type_0e"):
            self.assertEqual(by_id[boundary], POSITIVE)
        for boundary in ("neg_nocontext", "neg_type_01", "neg_type_09", "neg_type_ff"):
            self.assertEqual(by_id[boundary], NEGATIVE)
        self.assertEqual(len(self.cases), 21)

    def test_callee_dependence_outranks_everything_and_unknown_is_not_a_refusal(self):
        query = {"type_value": 0x08, "target": "unspecified", "playing": True, "asserts_effect": False}
        verdict, _, _ = judge(self.context, query, present=False, needs_callee=True)
        self.assertEqual(verdict, BLOCKED)
        verdict, _, _ = judge(self.context, dict(query, target="cd_da"))
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge(self.context, dict(query, playing=False))
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge(self.context, dict(query, asserts_effect=True))
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge({"synthetic": False}, query)
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge("not-a-context", query)
        self.assertEqual(verdict, UNKNOWN)

    def test_malformed_control_flags_are_never_positive(self):
        query = {"type_value": 0x08, "target": "unspecified", "playing": True, "asserts_effect": False}
        bad_flags = ("false", "true", "0", "1", 0, 1, None, 0.0, 1.0, [], {}, b"true")
        for bad in bad_flags:
            with self.subTest(present=repr(bad)):
                verdict, _, result = judge(self.context, query, present=bad, needs_callee=False)
                self.assertNotEqual(verdict, POSITIVE)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
            with self.subTest(needs_callee=repr(bad)):
                verdict, _, result = judge(self.context, query, present=True, needs_callee=bad)
                self.assertNotEqual(verdict, POSITIVE)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
        # Malformed needs_callee must not fall through to BLOCKED; malformed present
        # must not fall through to NEGATIVE-as-absent.
        verdict, _, _ = judge(self.context, query, present=False, needs_callee="true")
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge(self.context, query, present="false", needs_callee=True)
        self.assertEqual(verdict, UNKNOWN)
        # Valid bool precedence is preserved: callee dependence outranks no-context.
        verdict, _, _ = judge(self.context, query, present=False, needs_callee=True)
        self.assertEqual(verdict, BLOCKED)
        verdict, _, result = judge(self.context, query, present=True, needs_callee=False)
        self.assertEqual(verdict, POSITIVE)
        self.assertIsNotNone(result)

    def test_malformed_query_flags_are_unknown_and_never_positive(self):
        base = {"type_value": 0x08, "target": "unspecified", "playing": True, "asserts_effect": False}
        malformed = [
            dict(base, playing="yes"),
            dict(base, playing=1),
            dict(base, playing=None),
            dict(base, asserts_effect="yes"),
            dict(base, asserts_effect=1),
        ]
        for query in malformed:
            with self.subTest(query=query):
                verdict, detail, result = judge(self.context, query)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
                self.assertTrue(detail)

    def test_no_negative_case_is_accepted_and_lenient_judge_is_caught(self):
        accepted = [c["id"] for c in self.cases if c["expected"] != POSITIVE
                    and judge(self.context, c, c.get("present", True),
                              c.get("needs_callee", False))[0] == POSITIVE]
        self.assertEqual(accepted, [])
        caught = [c["id"] for c in self.cases if c["expected"] != POSITIVE
                  and lenient_judge(self.context, c, c.get("present", True),
                                    c.get("needs_callee", False)) == POSITIVE]
        self.assertEqual(set(caught), LENIENT_CAUGHT_IDS)

    def test_slot_0f_contract_row_is_not_run_and_not_executed(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        row = next(e for e in contracts["apis"] if e.get("slot") == 15)
        self.assertEqual(row["candidate_name"], "CD_FADE")
        self.assertEqual(row["candidate_entry"], "0xE02D")
        self.assertEqual(row["mapping_status"], "HYPOTHESIS")
        self.assertEqual(row["verification"], {"status": "NOT_RUN", "executed_callee": False, "evidence": []})
        dev = json.loads((ROOT / "spec" / "dev_implementations.json").read_text(encoding="utf-8"))
        dev_row = next(e for e in dev["slots"] if e.get("id") == 15)
        self.assertEqual(dev_row["candidate_name"], "CD_FADE")
        self.assertEqual(dev_row["state"], "NOT_READY")

    def test_fixture_is_not_wired_into_build_rom_or_callable_entries(self):
        self.assertNotIn("DEV_CALLABLE", FIXTURE.read_text(encoding="utf-8"))
        out = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "-F", "cdfade-slot0f", "--", "."],
                             capture_output=True, text=True)
        self.assertIn(out.returncode, (0, 1))
        hits = {p for p in out.stdout.splitlines() if p not in (FIXTURE_REL, SELF_REL)}
        self.assertEqual(sorted(hits), [], "fixture referenced outside its test")


if __name__ == "__main__":
    unittest.main()
