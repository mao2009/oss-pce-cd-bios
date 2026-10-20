"""Pure-host positive/negative tests for the slot-$0E (CD_PCMRD, candidate $E02A) synthetic fixture (Issue #277).

Synthetic only: the medium, the observable state and every expected value are author-defined literals in
tests/fixtures/cdpcmrd-slot0e-cases.json. Nothing here runs a BIOS, emulator or reference callee, and nothing
reads ./rom/. A passing run validates only this fixture and its local judge, never CD_PCMRD behaviour.
The fixture must stay BLOCKED_ABI with release_eligible=false and must not be wired into any callable entry.
"""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cdpcmrd-slot0e-cases.json"
FIXTURE = ROOT / FIXTURE_REL
# Canonical sha256 (sort_keys, compact, UTF-8) of the fixture data. Edits must update this in the same change.
FIXTURE_SHA256 = "b76e3c0397aa6e78b2121df058476e65f0d217e741d133f1c92d105f560b0054"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
SUPPORTED_TRANSPORT = {"playing"}


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def judge(medium, state, index):
    """Local reference judge. Returns a sample value (int) for a supported read, else None (refused).
    Refuses: non-int/bool index, stopped or unsupported transport, data or missing track, index outside [0, count)."""
    if type(index) is not int:
        return None
    if state["transport"] not in SUPPORTED_TRANSPORT:
        return None
    track = next((t for t in medium["tracks"] if t["no"] == state["track"]), None)
    if track is None or track["type"] != "audio":
        return None
    samples = track["samples"]
    if not 0 <= index < len(samples):
        return None
    return samples[index]


def mutant_ignores_transport(medium, state, index):
    """Wrong judge: reads stopped/paused media. The negative transport cases must catch it."""
    st = dict(state, transport="playing")
    return judge(medium, st, index)


def mutant_off_by_one(medium, state, index):
    """Wrong judge: accepts index == sample count. The one-past-end case must catch it."""
    track = next((t for t in medium["tracks"] if t["no"] == state["track"]), None)
    if track and type(index) is int and index == len(track["samples"]):
        return 0
    return judge(medium, state, index)


def mutant_accepts_bool(medium, state, index):
    """Wrong judge: lets bool through as int (True == 1). The bool case must catch it."""
    if index is True:
        index = 1
    return judge(medium, state, index)


def mutant_ignores_track_type(medium, state, index):
    """Wrong judge: reads the data track as if it had samples. The data-track case must catch it."""
    if state["track"] == 1 and state["transport"] in SUPPORTED_TRANSPORT:
        return 0
    return judge(medium, state, index)


def run_cases(data, fn):
    """Return the case ids where fn disagrees with the fixture expectation."""
    bad = []
    for c in data["cases"]:
        got = fn(data["medium"], c["state"], c["index"])
        if c["expected"] == POSITIVE:
            ok = got == c["value"]
        else:
            ok = got is None
        if not ok:
            bad.append(c["id"])
    return bad


class CdPcmrdSlot0eFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def test_status_and_provenance_are_fixed(self):
        d = self.data
        self.assertEqual(d["abi_status"], "BLOCKED_ABI")
        self.assertIs(d["release_eligible"], False)
        self.assertEqual(d["execution"], "NOT_RUN")
        self.assertEqual(d["callee_abi"]["reference_jsr"], "NOT_CALLED")
        for k in ("arguments", "returns", "registers"):
            self.assertEqual(d["callee_abi"][k], "UNKNOWN")
        self.assertTrue(d["provenance"].startswith("synthetic:"))
        self.assertIn("no System Card BIOS byte", d["provenance"])

    def test_fixture_hash_is_pinned(self):
        canon = json.dumps(self.data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canon).hexdigest(), FIXTURE_SHA256)

    def test_expected_values_are_literals_matching_the_medium(self):
        # Expected values come from the fixture data, never from the judge. Positive values must equal the
        # hand-written sample table entry at that index.
        samples = next(t for t in self.data["medium"]["tracks"] if t["no"] == 2)["samples"]
        positives = [c for c in self.data["cases"] if c["expected"] == POSITIVE]
        self.assertGreaterEqual(len(positives), 3)
        for c in positives:
            self.assertIsInstance(c["value"], int, c["id"])
            self.assertEqual(c["value"], samples[c["index"]], c["id"])

    def test_reference_judge_matches_every_case(self):
        self.assertEqual(run_cases(self.data, judge), [])

    def test_negative_mutants_are_detected(self):
        # Each deliberately wrong judge must be caught by at least one case, or the case set is too weak.
        for fn in (mutant_ignores_transport, mutant_off_by_one, mutant_accepts_bool, mutant_ignores_track_type):
            with self.subTest(mutant=fn.__name__):
                self.assertNotEqual(run_cases(self.data, fn), [], fn.__name__)

    def test_fixture_is_not_wired_into_callable_entries(self):
        # Scope: tracked files plus untracked, non-ignored files (--untracked). Ignored paths (e.g. build/) are out of scope.
        # Exit 0 = hits listed, 1 = no match, anything else (2+, or launch failure) = the check itself failed.
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "--untracked", "cdpcmrd-slot0e", "--", ".", ":!docs"],
                                 capture_output=True, text=True)
        except OSError as e:
            self.fail(f"git grep could not be launched: {e}")
        if out.returncode == 1:
            return
        if out.returncode != 0:
            self.fail(f"git grep failed (rc={out.returncode}): {out.stderr.strip()}")
        hits = {p for p in out.stdout.split() if p not in (FIXTURE_REL, "tests/host/test_cdpcmrd_slot0e_fixture.py")}
        self.assertEqual(sorted(hits), [], "fixture name referenced outside the fixture and this test")

    # --- git grep failure modes (mocked): exit 2+, launch failure, untracked scope, clean/hit paths ---
    def test_git_grep_exit_2_is_a_failure(self):
        done = subprocess.CompletedProcess([], 2, "", "fatal: bad pattern")
        with mock.patch("subprocess.run", return_value=done):
            with self.assertRaises(AssertionError):
                self.test_fixture_is_not_wired_into_callable_entries()

    def test_git_grep_fatal_128_is_a_failure(self):
        done = subprocess.CompletedProcess([], 128, "", "fatal: not a git repository")
        with mock.patch("subprocess.run", return_value=done):
            with self.assertRaises(AssertionError):
                self.test_fixture_is_not_wired_into_callable_entries()

    def test_git_grep_launch_failure_is_a_failure(self):
        with mock.patch("subprocess.run", side_effect=OSError("git: command not found")):
            with self.assertRaises(AssertionError):
                self.test_fixture_is_not_wired_into_callable_entries()

    def test_git_grep_exit_1_means_no_reference(self):
        done = subprocess.CompletedProcess([], 1, "", "")
        with mock.patch("subprocess.run", return_value=done):
            self.test_fixture_is_not_wired_into_callable_entries()

    def test_git_grep_reported_hit_is_a_failure(self):
        done = subprocess.CompletedProcess([], 0, "tests/host/test_other.py\n", "")
        with mock.patch("subprocess.run", return_value=done):
            with self.assertRaises(AssertionError):
                self.test_fixture_is_not_wired_into_callable_entries()

    def test_untracked_files_are_scanned(self):
        done = subprocess.CompletedProcess([], 1, "", "")
        with mock.patch("subprocess.run", return_value=done) as run:
            self.test_fixture_is_not_wired_into_callable_entries()
        self.assertIn("--untracked", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
