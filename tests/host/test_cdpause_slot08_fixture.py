"""Host positive/negative/unknown checks for the slot-$08 (CD_PAUSE, candidate $E018) synthetic fixture (Issue #255).

Synthetic only: the medium, operations and cases here are author-defined. The judge below is OUR MODEL of
playback/pause transitions, not the System Card CD_PAUSE. No emulator, no reference BIOS read, no ./rom/ access,
no reference callee JSR. A passing run validates only this fixture and its judge; it is NOT a System Card ABI PASS.
Outcomes are POSITIVE (model keeps the position in PAUSED), NEGATIVE (model refuses the transition) or
UNKNOWN (state or operation outside the model; never counted as a pass). The fixture stays BLOCKED_ABI and
release_eligible=false, and must not be wired into any callable entry.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdpause-slot08-cases.json"
FIXTURE_REL = "tests/fixtures/cdpause-slot08-cases.json"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of the fixture data. Changing the fixture
# requires updating this value in the same change.
FIXTURE_SHA256 = "650a09b0d446a01ccca51d91a0d6bfc8cd65849e87e2c8b269496f71cc790b43"
POSITIVE, NEGATIVE, UNKNOWN = "POSITIVE", "NEGATIVE", "UNKNOWN"
OUTCOMES = {POSITIVE, NEGATIVE, UNKNOWN}
FORBIDDEN_WORDS = ("PASS", "VERIFIED", "OBSERVED", "DEV_CALLABLE")
APPROVED_PROVENANCE = ("synthetic: author-defined medium and cases only; no System Card BIOS byte, disassembly, "
                       "retail data, or ./rom/ content; no reference callee is executed")
TOP_LEVEL_KEYS = {"schema", "issue", "slot", "entry_candidate", "candidate_name", "abi_status", "release_eligible",
                  "execution", "provenance", "callee_abi", "medium", "ops", "outcomes", "cases"}
PREFIX_CLASS = {"pos_": POSITIVE, "neg_": NEGATIVE, "unk_": UNKNOWN}
MUTANTS = ("reset_pos", "accept_stopped_pause")


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _audio_track(medium, lba):
    for t in medium["tracks"]:
        if t["type"] == "audio" and t["start"] <= lba <= t["end"]:
            return t
    return None


def judge(ops, medium, mutant=None):
    """Run the operation list through the model. Returns (outcome, state, pos, failing_op).

    The first refusal (NEGATIVE) or unmodelled step (UNKNOWN) ends the run. Mutants are deliberately
    wrong models used only to prove that the cases catch false positives.
    """
    state, pos = "STOPPED", None
    for op in ops:
        name, _, arg = op.partition(":")
        if name == "play":
            if state != "STOPPED":
                return UNKNOWN, state, pos, op          # replay while playing is not modelled
            try:
                lba = int(arg)
            except ValueError:
                return NEGATIVE, state, pos, op         # malformed LBA
            if _audio_track(medium, lba) is None:
                return NEGATIVE, state, pos, op         # data track, lead-out, or out of range
            state, pos = "PLAYING", lba
        elif name == "tick":
            if state != "PLAYING":
                return NEGATIVE, state, pos, op         # no playback to advance
            track = _audio_track(medium, pos)
            n = int(arg)
            if pos + n > track["end"]:
                return UNKNOWN, state, pos, op          # end-of-track behaviour unmodelled
            pos += n
        elif name == "pause":
            if state == "STOPPED":
                if mutant != "accept_stopped_pause":
                    return NEGATIVE, state, pos, op     # pause needs playback
                state = "PAUSED"
            elif state == "PAUSED":
                return UNKNOWN, state, pos, op          # double pause unmeasured and unspecified
            else:
                state = "PAUSED"
                if mutant == "reset_pos":
                    pos = _audio_track(medium, pos)["start"]
        else:
            return UNKNOWN, state, pos, op              # operation outside the model
    return POSITIVE, state, pos, None


def run_all(data, mutant=None):
    return {c["id"]: judge(c["ops"], data["medium"], mutant) for c in data["cases"]}


def mismatches(data, mutant=None):
    """Case ids whose outcome differs from expected, or whose PAUSED state/position differs for POSITIVE cases."""
    bad = []
    for c in data["cases"]:
        outcome, state, pos, _ = judge(c["ops"], data["medium"], mutant)
        if outcome != c["expected"] or (c["expected"] == POSITIVE and (state, pos) != (c["state"], c["pos"])):
            bad.append(c["id"])
    return bad


class CdPauseFixtureShape(unittest.TestCase):
    def test_status_and_provenance_are_pinned(self):
        data = load()
        self.assertEqual(set(data), TOP_LEVEL_KEYS)
        self.assertEqual(data["abi_status"], "BLOCKED_ABI")
        self.assertIs(data["release_eligible"], False)
        self.assertEqual(data["execution"], "NOT_RUN")
        self.assertEqual(data["provenance"], APPROVED_PROVENANCE)
        self.assertEqual(data["callee_abi"], {"arguments": "UNKNOWN", "returns": "UNKNOWN",
                                              "registers": "UNKNOWN", "reference_jsr": "NOT_CALLED"})
        self.assertEqual(set(data["outcomes"]), OUTCOMES)

    def test_fixture_hash_is_pinned(self):
        self.assertEqual(canonical_sha256(load()), FIXTURE_SHA256)

    def test_no_execution_or_pass_words(self):
        text = FIXTURE.read_text(encoding="utf-8")
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, text)


class CdPauseModelOutcomes(unittest.TestCase):
    def test_each_case_matches_expected_outcome(self):
        data = load()
        results = run_all(data)
        for case in data["cases"]:
            with self.subTest(case=case["id"]):
                outcome, state, pos, _ = results[case["id"]]
                self.assertEqual(outcome, case["expected"])
                if case["expected"] == POSITIVE:
                    self.assertEqual((state, pos), (case["state"], case["pos"]))

    def test_positive_and_negative_are_separated_by_id_prefix(self):
        for case in load()["cases"]:
            prefix = case["id"][:4]
            self.assertIn(prefix, PREFIX_CLASS)
            self.assertEqual(PREFIX_CLASS[prefix], case["expected"], case["id"])

    def test_pause_positive_cases_cover_playing_and_boundary(self):
        ids = {c["id"] for c in load()["cases"] if c["expected"] == POSITIVE}
        self.assertTrue({"pos_pause_playing_keeps_pos", "pos_pause_at_audio_start",
                         "pos_pause_at_audio_last"} <= ids)

    def test_no_outcome_is_a_pass_word(self):
        for outcome, *_ in run_all(load()).values():
            self.assertIn(outcome, OUTCOMES)


class CdPauseMutantsAreCaught(unittest.TestCase):
    def test_every_mutant_produces_a_case_mismatch(self):
        data = load()
        for mutant in MUTANTS:
            with self.subTest(mutant=mutant):
                self.assertTrue(mismatches(data, mutant))

    def test_reset_pos_mutant_is_caught_by_playing_pause_case(self):
        self.assertIn("pos_pause_playing_keeps_pos", mismatches(load(), "reset_pos"))

    def test_stopped_pause_mutant_is_caught_by_stopped_case(self):
        self.assertIn("neg_pause_when_stopped", mismatches(load(), "accept_stopped_pause"))

    def test_unmutated_model_has_no_mismatch(self):
        self.assertEqual(mismatches(load()), [])

    def test_double_pause_is_never_positive(self):
        self.assertEqual(run_all(load())["unk_double_pause"][0], UNKNOWN)


class CdPauseNotWired(unittest.TestCase):
    def test_no_tracked_non_doc_file_names_the_fixture(self):
        if not (ROOT / ".git").exists():
            self.skipTest("not a git work tree; wiring check needs tracked files")
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
        hits = []
        for name in out.decode("utf-8").split("\0"):
            if not name or name.startswith("docs/") or name.endswith(".md") or name.startswith("rom/"):
                continue
            if name in (FIXTURE_REL, "tests/host/test_cdpause_slot08_fixture.py"):
                continue
            path = ROOT / name
            if not path.is_file() or b"\0" in path.read_bytes()[:4096]:
                continue
            if "cdpause-slot08" in path.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(name)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
