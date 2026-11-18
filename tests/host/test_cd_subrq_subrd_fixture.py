"""Host checks for the synthetic CD_SUBRQ / CD_SUBRD (slot $0D, candidate $E027) fixture (Issue #272).

Synthetic only: the medium (data track 1 + audio track 2) and every expected value are author-defined model
expectations, not measured device or System Card behaviour. The single grounded query kind (``current_q``) is
an unverified author choice (HYPOTHESIS); no public source proves it is a real CD_SUBRQ/CD_SUBRD call mode
and Issue #272 grounded-kind acceptance remains open. Any other request string is an unmodelled call mode;
when the query reaches the request-kind check it returns UNKNOWN, never a refusal or an acceptance.
Earlier precedence (malformed control flag, callee dependence, no medium, malformed medium, malformed
query) may return a different verdict first. No emulator runs, no reference BIOS is read or called
(reference_jsr_count = 0), and nothing under ./rom/ is touched. This is a synthetic model judgment,
not a real-device PASS. Passing these tests validates only this fixture and its judge. The fixture stays
BLOCKED_ABI and release_eligible=false, and must not be wired into any build, ROM or callable entry.

The global candidate-string occurrence check used by sibling fixtures is intentionally not repeated here:
$0D/$E027/CD_SUBRQ/CD_SUBRD already have legitimate pre-existing references (slot ledger, scenario table,
Issue #271 conflict tests). Instead the literal-reference check below pins the fixture filename in tracked
content (only this test may reference it) and the absence of build/ROM/callable markers; it does not
prove that a glob-based build rule could never select the file.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cdsubrq-subrd-slot0d-cases.json"
FIXTURE = ROOT / FIXTURE_REL
SELF_REL = "tests/host/test_cd_subrq_subrd_fixture.py"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8). Editing the fixture requires updating it here.
FIXTURE_SHA256 = "2c30e717e70ee59dee6029b8711fb86b9ad315923e91294ff8bc3a3090d45098"
POSITIVE, NEGATIVE, UNKNOWN, BLOCKED = "POSITIVE", "NEGATIVE", "UNKNOWN", "BLOCKED"
TOP_LEVEL_KEYS = {"schema", "issue", "parent_issue", "slot", "entry_candidate", "candidate_name", "abi_status",
                  "release_eligible", "execution", "provenance", "callee_abi", "medium", "io_range",
                  "query_model", "cases"}
APPROVED_PROVENANCE = ("synthetic: author-defined medium and cases only; no System Card BIOS byte, disassembly, "
                       "retail data, or ./rom/ content; no reference callee is executed")
GROUNDED_REQUEST = "current_q"
TRACK_TYPES = ("data", "audio")
POSITIVE_IDS = {"pos_t1_start", "pos_t1_end", "pos_t2_start", "pos_t2_mid", "pos_t2_end"}
NEGATIVE_IDS = {"neg_nomedium", "neg_no_such_track", "neg_track_zero", "neg_track_mismatch", "neg_before_track2",
                "neg_track1_past_end", "neg_past_leadout", "neg_negative_lba", "neg_bool_track", "neg_float_lba",
                "neg_string_lba", "neg_none_track", "neg_bool_lba"}
UNKNOWN_IDS = {"unk_request_stream", "unk_request_cached"}
BLOCKED_IDS = {"blocked_callee_args", "blocked_callee_nomedium"}
# Negatives/unknowns/blocked that the deliberately lenient judge wrongly accepts (must be exactly these).
LENIENT_CAUGHT_IDS = {"neg_track_mismatch", "neg_before_track2", "neg_track1_past_end", "neg_past_leadout",
                      "neg_bool_track", "neg_float_lba", "neg_string_lba", "neg_bool_lba",
                      "unk_request_stream", "unk_request_cached", "blocked_callee_args"}


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _well_formed(medium):
    # Synthetic-model-only contract (not a CD-standard claim): the author-defined medium is
    # contiguous from LBA 0 with no gaps or overlaps (adjacent start == previous end + 1) and
    # leadout_lba == last track end + 1. Anything outside this model (gaps, overlaps, reordered
    # tracks, empty track list, bad types, wrong lead-out, non-zero first start) is not
    # well-formed; judge() reports it as UNKNOWN, never POSITIVE. No pregap/lead-in rule is
    # assumed beyond this synthetic definition.
    if not isinstance(medium, dict) or medium.get("synthetic") is not True:
        return False
    tracks, leadout = medium.get("tracks"), medium.get("leadout_lba")
    if not isinstance(tracks, list) or len(tracks) == 0:
        return False
    if type(leadout) is not int or leadout < 0:
        return False
    prev_no, prev_end = 0, -1
    for t in tracks:
        if not isinstance(t, dict) or t.get("type") not in TRACK_TYPES:
            return False
        no, start, end = t.get("no"), t.get("start"), t.get("end")
        if any(type(v) is not int for v in (no, start, end)):
            return False
        if no < 1 or not (0 <= start <= end < leadout):
            return False
        if no <= prev_no:
            return False
        # Contiguity: first track must start at 0 (prev_end starts at -1), every next track
        # must start exactly at previous end + 1. Gaps and overlaps are both rejected here.
        if start != prev_end + 1:
            return False
        prev_no, prev_end = no, end
    if leadout != prev_end + 1:
        return False
    return True


def judge(medium, query, present=True, needs_callee=False):
    """Reference judge: (verdict, detail, result). Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI.

    Precedence (first matching rule decides): malformed control flag -> callee dependence ->
    no medium -> malformed medium -> malformed query -> unmodelled request -> malformed track/LBA ->
    unknown track -> LBA outside the track -> POSITIVE. Control flags must be plain bool and query
    must be a dict; any other type is UNKNOWN, never POSITIVE. An unmodelled request returns UNKNOWN
    only when it reaches the request-kind check; earlier rules may return another verdict first.
    """
    if type(present) is not bool or type(needs_callee) is not bool:
        return UNKNOWN, "malformed control flag: present and needs_callee must be plain bool", None
    if needs_callee:
        return BLOCKED, "BLOCKED_ABI: callee arguments/returns undecided; reference JSR not called", None
    if not present:
        return NEGATIVE, "no medium present", None
    if not _well_formed(medium):
        return UNKNOWN, "medium is outside the synthetic model", None
    if not isinstance(query, dict):
        return UNKNOWN, "malformed query: query must be a dict", None
    request = query.get("request")
    if request != GROUNDED_REQUEST:
        return UNKNOWN, "unmodelled call mode: only current_q is grounded", None
    track, lba = query.get("track"), query.get("lba")
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE, "malformed: track and LBA must be plain ints (bool, float, str, None refused)", None
    entry = next((t for t in medium["tracks"] if t["no"] == track), None)
    if entry is None:
        return NEGATIVE, "track number not on the medium", None
    if not (entry["start"] <= lba <= entry["end"]):
        return NEGATIVE, "LBA outside the requested track", None
    return POSITIVE, "position inside the requested track", {"track": track, "type": entry["type"], "lba": lba}


def lenient_judge(medium, query, present=True, needs_callee=False):
    """Deliberately wrong judge: ignores request kind, callee dependence and int strictness.

    Accepts any present query whose loose int conversion lands on a modelled track with an LBA at or below
    the lead-out (inclusive-bound off-by-one included). Negatives/unknowns/blocked that slip through must
    be exactly LENIENT_CAUGHT_IDS.
    """
    if not present:
        return NEGATIVE
    try:
        track = int(query.get("track")) if isinstance(query.get("track"), (int, float)) else None
        lba = int(query.get("lba")) if isinstance(query.get("lba"), (int, float, str)) else None
    except (ValueError, TypeError):
        return NEGATIVE
    if track in (1, 2) and isinstance(lba, int) and 0 <= lba <= medium["leadout_lba"]:
        return POSITIVE
    return NEGATIVE


class CdSubrqSubrdSlot0dFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.medium = cls.fixture["medium"]
        cls.cases = cls.fixture["cases"]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cdsubrq-subrd-slot0d-fixture/v1")
        self.assertEqual((f["issue"], f["parent_issue"], f["slot"], f["entry_candidate"], f["candidate_name"]),
                         (272, 22, "$0D", "$E027", "CD_SUBRQ / CD_SUBRD"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertIs(self.medium["synthetic"], True)
        self.assertEqual(f["callee_abi"]["reference_jsr"], "NOT_CALLED")
        self.assertEqual(f["callee_abi"]["reference_jsr_count"], 0)
        for field in ("arguments", "returns", "registers", "q_buffer_layout"):
            self.assertEqual(f["callee_abi"][field], "UNKNOWN")

    def test_fixture_hash_and_provenance_are_pinned(self):
        self.assertEqual(canonical_sha256(self.fixture), FIXTURE_SHA256)
        self.assertEqual(set(self.fixture), TOP_LEVEL_KEYS)
        self.assertEqual(self.fixture["provenance"], APPROVED_PROVENANCE)

    def test_medium_boundaries_are_contiguous(self):
        d, a = self.medium["tracks"]
        self.assertEqual((d["no"], d["type"], a["no"], a["type"]), (1, "data", 2, "audio"))
        self.assertEqual((d["start"], d["end"]), (0, 99))
        self.assertEqual((a["start"], a["end"]), (100, 199))
        self.assertEqual(a["start"], d["end"] + 1)
        self.assertEqual(self.medium["leadout_lba"], a["end"] + 1)
        self.assertIn("author-defined", self.medium["leadout_model"])
        self.assertIn("not measured", self.medium["leadout_model"])
        self.assertEqual(self.fixture["io_range"], {"lba_min": 0, "lba_max": 199, "track_numbers": [1, 2]})

    def test_every_case_matches_its_expected_verdict_and_result(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                verdict, detail, result = judge(self.medium, case, case.get("present", True),
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
        by_tuple = {c["id"]: (c["track"], c["lba"], c["expected"]) for c in self.cases}
        for boundary, want in (("pos_t1_start", (1, 0, POSITIVE)), ("pos_t1_end", (1, 99, POSITIVE)),
                               ("pos_t2_start", (2, 100, POSITIVE)), ("pos_t2_end", (2, 199, POSITIVE))):
            self.assertEqual(by_tuple[boundary], want)
        for boundary, want in (("neg_nomedium", (2, 150, NEGATIVE)), ("neg_track_zero", (0, 100, NEGATIVE)),
                               ("neg_no_such_track", (3, 100, NEGATIVE)),
                               ("neg_before_track2", (2, 99, NEGATIVE)),
                               ("neg_track1_past_end", (1, 100, NEGATIVE)),
                               ("neg_past_leadout", (2, 200, NEGATIVE))):
            self.assertEqual(by_tuple[boundary], want)
        self.assertEqual(self.fixture["query_model"]["grounded_request"], GROUNDED_REQUEST)

    def test_callee_dependence_outranks_everything_and_unknown_is_not_a_refusal(self):
        verdict, _, _ = judge(self.medium, {"request": "current_q", "track": 2, "lba": 150},
                              present=False, needs_callee=True)
        self.assertEqual(verdict, BLOCKED)
        verdict, _, _ = judge(self.medium, {"request": "stream_q", "track": 2, "lba": 150})
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge({"synthetic": True, "leadout_lba": 200, "tracks": {}},
                              {"request": "current_q", "track": 1, "lba": 0})
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge({"synthetic": True, "leadout_lba": 200, "tracks": []},
                              {"request": "current_q", "track": 1, "lba": 0})
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge({"synthetic": False}, {"request": "current_q", "track": 1, "lba": 0})
        self.assertEqual(verdict, UNKNOWN)

    def test_malformed_media_are_unknown_and_never_positive(self):
        # Synthetic-model-only negatives: gaps, bad lead-out, overlaps, reordered tracks,
        # empty list and bad types must be UNKNOWN, never POSITIVE. These are author-defined
        # model constraints, not CD-standard claims.
        gap = {"synthetic": True, "leadout_lba": 200, "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 99},
            {"no": 2, "type": "audio", "start": 101, "end": 199},
        ]}
        bad_leadout = {"synthetic": True, "leadout_lba": 205, "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 99},
            {"no": 2, "type": "audio", "start": 100, "end": 199},
        ]}
        overlap = {"synthetic": True, "leadout_lba": 200, "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 99},
            {"no": 2, "type": "audio", "start": 99, "end": 199},
        ]}
        reordered = {"synthetic": True, "leadout_lba": 200, "tracks": [
            {"no": 2, "type": "audio", "start": 100, "end": 199},
            {"no": 1, "type": "data", "start": 0, "end": 99},
        ]}
        empty = {"synthetic": True, "leadout_lba": 200, "tracks": []}
        bad_type_tracks = {"synthetic": True, "leadout_lba": 200, "tracks": "not-a-list"}
        bad_type_leadout = {"synthetic": True, "leadout_lba": "200", "tracks": [
            {"no": 1, "type": "data", "start": 0, "end": 99},
            {"no": 2, "type": "audio", "start": 100, "end": 199},
        ]}
        bad_type_track_no = {"synthetic": True, "leadout_lba": 200, "tracks": [
            {"no": "1", "type": "data", "start": 0, "end": 99},
            {"no": 2, "type": "audio", "start": 100, "end": 199},
        ]}
        nonzero_first_start = {"synthetic": True, "leadout_lba": 200, "tracks": [
            {"no": 1, "type": "data", "start": 1, "end": 99},
            {"no": 2, "type": "audio", "start": 100, "end": 199},
        ]}
        malformed = [
            ("gap track1-end-99 track2-start-101", gap,
             {"request": "current_q", "track": 2, "lba": 101}),
            ("leadout-205 past final-end-199", bad_leadout,
             {"request": "current_q", "track": 2, "lba": 150}),
            ("overlapping boundary at 99", overlap,
             {"request": "current_q", "track": 2, "lba": 99}),
            ("reordered tracks", reordered,
             {"request": "current_q", "track": 1, "lba": 0}),
            ("empty track array", empty,
             {"request": "current_q", "track": 1, "lba": 0}),
            ("non-list tracks type", bad_type_tracks,
             {"request": "current_q", "track": 1, "lba": 0}),
            ("string leadout type", bad_type_leadout,
             {"request": "current_q", "track": 2, "lba": 150}),
            ("string track-no type", bad_type_track_no,
             {"request": "current_q", "track": 1, "lba": 0}),
            ("non-zero first start", nonzero_first_start,
             {"request": "current_q", "track": 1, "lba": 1}),
        ]
        for label, medium, query in malformed:
            with self.subTest(medium=label):
                self.assertFalse(_well_formed(medium))
                verdict, detail, result = judge(medium, query)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
                self.assertTrue(detail)

    def test_malformed_control_flags_are_never_positive(self):
        query = {"request": "current_q", "track": 2, "lba": 150}
        bad_flags = ("false", "true", "0", "1", 0, 1, None, 0.0, 1.0, [], {}, b"true")
        for bad in bad_flags:
            with self.subTest(present=repr(bad)):
                verdict, _, result = judge(self.medium, query, present=bad, needs_callee=False)
                self.assertNotEqual(verdict, POSITIVE)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
            with self.subTest(needs_callee=repr(bad)):
                verdict, _, result = judge(self.medium, query, present=True, needs_callee=bad)
                self.assertNotEqual(verdict, POSITIVE)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
        # Malformed needs_callee must not fall through to BLOCKED; malformed present
        # must not fall through to NEGATIVE-as-absent.
        verdict, _, _ = judge(self.medium, query, present=False, needs_callee="true")
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, _ = judge(self.medium, query, present="false", needs_callee=True)
        self.assertEqual(verdict, UNKNOWN)
        # Valid bool precedence is preserved: callee dependence outranks no-medium.
        verdict, _, _ = judge(self.medium, query, present=False, needs_callee=True)
        self.assertEqual(verdict, BLOCKED)
        verdict, _, result = judge(self.medium, query, present=True, needs_callee=False)
        self.assertEqual(verdict, POSITIVE)
        self.assertIsNotNone(result)

    def test_malformed_query_is_unknown_and_never_positive(self):
        bad_queries = (None, [], "current_q", 1, True, 0.0, ("current_q",), b"current_q")
        for bad in bad_queries:
            with self.subTest(query=repr(bad)):
                verdict, detail, result = judge(self.medium, bad)
                self.assertEqual(verdict, UNKNOWN)
                self.assertIsNone(result)
                self.assertTrue(detail)
        # Precedence: flags -> callee -> no-medium -> malformed medium -> malformed query.
        verdict, detail, _ = judge(self.medium, None, present="bad")
        self.assertEqual(verdict, UNKNOWN)
        self.assertIn("control flag", detail)
        verdict, _, _ = judge(self.medium, None, present=True, needs_callee=True)
        self.assertEqual(verdict, BLOCKED)
        verdict, _, _ = judge(self.medium, None, present=False, needs_callee=False)
        self.assertEqual(verdict, NEGATIVE)
        bad_medium = {"synthetic": True, "leadout_lba": 200, "tracks": []}
        verdict, detail, _ = judge(bad_medium, None)
        self.assertEqual(verdict, UNKNOWN)
        self.assertIn("medium", detail)
        # Sanity: valid dict query still reaches the request-kind and position checks.
        verdict, _, _ = judge(self.medium, {"request": "stream_q", "track": 2, "lba": 150})
        self.assertEqual(verdict, UNKNOWN)
        verdict, _, result = judge(self.medium, {"request": "current_q", "track": 2, "lba": 150})
        self.assertEqual(verdict, POSITIVE)
        self.assertIsNotNone(result)

    def test_no_negative_case_is_accepted_and_lenient_judge_is_caught(self):
        accepted = [c["id"] for c in self.cases if c["expected"] != POSITIVE
                    and judge(self.medium, c, c.get("present", True),
                              c.get("needs_callee", False))[0] == POSITIVE]
        self.assertEqual(accepted, [])
        caught = [c["id"] for c in self.cases if c["expected"] != POSITIVE
                  and lenient_judge(self.medium, c, c.get("present", True),
                                    c.get("needs_callee", False)) == POSITIVE]
        self.assertEqual(set(caught), LENIENT_CAUGHT_IDS)

    def test_slot_0d_contract_row_is_not_run_and_not_executed(self):
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        row = next(e for e in contracts["apis"] if e.get("slot") == 13)
        self.assertEqual(row["candidate_name"], "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(row["candidate_entry"], "0xE027")
        self.assertEqual(row["mapping_status"], "HYPOTHESIS")
        self.assertEqual(row["verification"], {"status": "NOT_RUN", "executed_callee": False, "evidence": []})

    def test_fixture_has_no_literal_reference_outside_self_and_no_callable_marker(self):
        # Literal-reference scope only: git grep finds the filename string in tracked files; it cannot
        # detect indirect build inclusion via globs or resolved build inputs. No build-system change here.
        self.assertNotIn("DEV_CALLABLE", FIXTURE.read_text(encoding="utf-8"))
        out = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "-F", "cdsubrq-subrd-slot0d", "--", "."],
                             capture_output=True, text=True)
        self.assertIn(out.returncode, (0, 1))
        hits = {p for p in out.stdout.splitlines() if p not in (FIXTURE_REL, SELF_REL)}
        self.assertEqual(sorted(hits), [], "fixture referenced outside its test")


if __name__ == "__main__":
    unittest.main()
