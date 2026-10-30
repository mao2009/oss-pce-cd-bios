"""Pure-host tests for the slot-$0E (CD_PCMRD, candidate $E02A) synthetic fixture (Issue #277).

Synthetic only: the medium, the observable state and every expected value are hand-written in
tests/fixtures/cdpcmrd-slot0e-cases.json. The local judge below is checked against those values for
self-consistency only; it is not an independent oracle. Nothing here runs a BIOS, emulator or reference callee,
and nothing reads ./rom/. A passing run validates only this fixture and the local judge, never CD_PCMRD behaviour.
The fixture must stay BLOCKED_ABI with release_eligible=false and must not be wired into any callable entry.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cdpcmrd-slot0e-cases.json"
TEST_REL = "tests/host/test_cdpcmrd_slot0e_fixture.py"
FIXTURE = ROOT / FIXTURE_REL
# Canonical sha256 (sort_keys, compact, UTF-8) of the fixture data. Edits must update this in the same change.
FIXTURE_SHA256 = "f248fd9c7ed314a176dd752f7b96e1de2ae22147066f8421e583589cfda810a9"
SLOT_STATUS = "HYPOTHESIS (C4 unresolved)"
POSITIVE, NEGATIVE, UNKNOWN = "POSITIVE", "NEGATIVE", "UNKNOWN"
SUPPORTED_TRANSPORT = {"playing"}

# Wiring scans (git grep, case-insensitive, untracked non-ignored files included).
# Broad: the fixture name must not appear anywhere except the fixture and this test (docs/ is out of scope).
BROAD_TERM = r"cdpcmrd[-_]slot0e|tests/fixtures/cdpcmrd"
BROAD_SCOPE = [".", ":!docs"]
# Entry: the candidate name or entry address must not be wired into build or source files.
ENTRY_TERM = r"CD_PCMRD|\$E02A|0xE02A"
ENTRY_SCOPE = ["src", "tools", "tests/fixtures/*.s", "Makefile"]
SCAN_ALLOWED_PATHS = {FIXTURE_REL, TEST_REL}
# Exact (path, stripped line) pairs that are not wiring. Each entry needs a reason.
ENTRY_ALLOWED_LINES = {
    # Plan generator: MEDIA is a name set that classifies which candidates need synthetic media.
    # It emits HYPOTHESIS plans; it holds no entry address and makes no call.
    ("tools/reference_bios_scenarios.py", '"CD_PCMRD", "CD_FADE"}'),
}
# Not scanned: spec/api_slots.json and spec/api_contracts.json list CD_PCMRD as a registry candidate
# (documentation, not a callable entry); they are outside ENTRY_SCOPE by design.


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def judge(medium, state, index):
    """Local judge (self-consistency only). Returns (outcome, value).

    POSITIVE carries the sample value. NEGATIVE is a measured refusal (malformed index, stopped transport, data or
    missing track, index outside [0, count)). UNKNOWN is an unmeasured transport state: never a refusal, never a pass.
    """
    if type(index) is not int:
        return NEGATIVE, None
    if state["transport"] == "stopped":
        return NEGATIVE, None
    if state["transport"] not in SUPPORTED_TRANSPORT:
        return UNKNOWN, None
    track = next((t for t in medium["tracks"] if t["no"] == state["track"]), None)
    if track is None or track["type"] != "audio":
        return NEGATIVE, None
    samples = track["samples"]
    if not 0 <= index < len(samples):
        return NEGATIVE, None
    return POSITIVE, samples[index]


def mutant_ignores_transport(medium, state, index):
    """Wrong judge: reads stopped/paused media. The transport cases must catch it."""
    st = dict(state, transport="playing")
    return judge(medium, st, index)


def mutant_off_by_one(medium, state, index):
    """Wrong judge: accepts index == sample count. The one-past-end case must catch it."""
    track = next((t for t in medium["tracks"] if t["no"] == state["track"]), None)
    if track and type(index) is int and index == len(track["samples"]):
        return POSITIVE, 0
    return judge(medium, state, index)


def mutant_accepts_bool(medium, state, index):
    """Wrong judge: lets bool through as int (True == 1). The bool case must catch it."""
    if index is True:
        index = 1
    return judge(medium, state, index)


def mutant_ignores_track_type(medium, state, index):
    """Wrong judge: reads the data track as if it had samples. The data-track case must catch it."""
    if state["track"] == 1 and state["transport"] in SUPPORTED_TRANSPORT:
        return POSITIVE, 0
    return judge(medium, state, index)


def mutant_falsy_zero(medium, state, index):
    """Wrong judge: falsy check turns a valid 0 sample into a refusal. The zero-sample positive case must catch it."""
    outcome, value = judge(medium, state, index)
    return (NEGATIVE, None) if outcome == POSITIVE and not value else (outcome, value)


def mutant_unknown_as_negative(medium, state, index):
    """Wrong judge: promotes unmeasured UNKNOWN to a refusal (the #277 review finding). The UNKNOWN cases must catch it."""
    outcome, value = judge(medium, state, index)
    return (NEGATIVE, None) if outcome == UNKNOWN else (outcome, value)


def run_cases(data, fn):
    """Return the case ids where fn's outcome (and, for POSITIVE, its value) disagrees with the fixture."""
    bad = []
    for c in data["cases"]:
        outcome, value = fn(data["medium"], c["state"], c["index"])
        ok = outcome == c["expected"] and (outcome != POSITIVE or value == c["value"])
        if not ok:
            bad.append(c["id"])
    return bad


def _clean_env():
    # Drop GIT_* variables (e.g. GIT_DIR) so git resolves the repository from -C only.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git_grep(root, pattern, pathspecs):
    """Return (path, lineno, text) per hit. Exit 1 = no match; exit 0 = hits; 2+ or launch failure = test failure."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "grep", "-n", "-a", "-i", "-E", "--untracked", "-e", pattern, "--", *pathspecs],
            capture_output=True, text=True, env=_clean_env())
    except OSError as e:
        raise AssertionError(f"git grep could not be launched: {e}")
    if out.returncode == 1:
        return []
    if out.returncode != 0:
        raise AssertionError(f"git grep failed (rc={out.returncode}): {out.stderr.strip()}")
    hits = []
    for line in out.stdout.splitlines():
        path, lineno, text = line.split(":", 2)
        hits.append((path, int(lineno), text))
    return hits


def unexpected_hits(root, pattern, pathspecs, allowed_paths=frozenset(), allowed_lines=frozenset()):
    """Return sorted 'path:line' for hits that are neither an allowed path nor an allowed (path, line) pair."""
    bad = []
    for path, lineno, text in git_grep(root, pattern, pathspecs):
        if path in allowed_paths or (path, text.strip()) in allowed_lines:
            continue
        bad.append(f"{path}:{lineno}")
    return sorted(bad)


class CdPcmrdSlot0eFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def test_status_and_provenance_are_fixed(self):
        d = self.data
        self.assertEqual(d["abi_status"], "BLOCKED_ABI")
        self.assertIs(d["release_eligible"], False)
        self.assertEqual(d["execution"], "NOT_RUN")
        self.assertEqual(d["slot_status"], SLOT_STATUS)
        self.assertEqual(d["callee_abi"]["reference_jsr"], "NOT_CALLED")
        for k in ("arguments", "returns", "registers"):
            self.assertEqual(d["callee_abi"][k], "UNKNOWN")
        self.assertTrue(d["provenance"].startswith("synthetic:"))
        self.assertIn("no System Card BIOS byte", d["provenance"])

    def test_fixture_hash_is_pinned(self):
        canon = json.dumps(self.data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(canon).hexdigest(), FIXTURE_SHA256)

    def test_expected_values_match_medium_table_self_consistency(self):
        # Expected values are hand-written in the fixture. Positive values must equal the sample table entry at
        # that index. This checks the fixture against itself; it is not an independent oracle.
        samples = next(t for t in self.data["medium"]["tracks"] if t["no"] == 2)["samples"]
        positives = [c for c in self.data["cases"] if c["expected"] == POSITIVE]
        self.assertGreaterEqual(len(positives), 3)
        for c in positives:
            self.assertIsInstance(c["value"], int, c["id"])
            self.assertEqual(c["value"], samples[c["index"]], c["id"])

    def test_local_judge_agrees_with_fixture_self_consistency(self):
        # Self-consistency only: the local judge is not an independent oracle of CD_PCMRD.
        self.assertEqual(run_cases(self.data, judge), [])

    def test_unmeasured_transport_is_unknown_not_negative(self):
        # Only a measured refusal (stopped transport, malformed or out-of-range input) may be NEGATIVE.
        # An unmeasured transport state (paused, seeking, unknown, ...) is UNKNOWN and must never count as a refusal.
        for c in self.data["cases"]:
            if c["state"]["transport"] not in SUPPORTED_TRANSPORT | {"stopped"}:
                with self.subTest(case=c["id"]):
                    self.assertEqual(c["expected"], UNKNOWN)

    def test_id_prefix_matches_expected_outcome(self):
        prefix = {"pos_": POSITIVE, "neg_": NEGATIVE, "unk_": UNKNOWN}
        for c in self.data["cases"]:
            with self.subTest(case=c["id"]):
                self.assertEqual(c["expected"], prefix[c["id"].split("_", 1)[0] + "_"])

    def test_negative_mutants_are_detected(self):
        # Each deliberately wrong judge must be caught by at least one case, or the case set is too weak.
        for fn in (mutant_ignores_transport, mutant_off_by_one, mutant_accepts_bool,
                   mutant_ignores_track_type, mutant_falsy_zero, mutant_unknown_as_negative):
            with self.subTest(mutant=fn.__name__):
                self.assertNotEqual(run_cases(self.data, fn), [], fn.__name__)

    def test_fixture_is_not_wired_into_callable_entries(self):
        # Broad scan. Exit 0 = hits listed, 1 = no match, anything else (2+, or launch failure) = the check itself failed.
        bad = unexpected_hits(ROOT, BROAD_TERM, BROAD_SCOPE, allowed_paths=SCAN_ALLOWED_PATHS)
        self.assertEqual(bad, [], "fixture name referenced outside the fixture and this test")

    def test_entry_names_are_not_wired_into_build_sources(self):
        bad = unexpected_hits(ROOT, ENTRY_TERM, ENTRY_SCOPE,
                              allowed_paths=SCAN_ALLOWED_PATHS, allowed_lines=ENTRY_ALLOWED_LINES)
        self.assertEqual(bad, [], "CD_PCMRD / $E02A / 0xE02A wired into src, tools, fixtures .s or Makefile")

    # --- real git repositories in temp dirs: untracked detection and scope (never inside this worktree) ---
    def _temp_repo(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=_clean_env())
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root

    def test_untracked_wiring_is_detected_in_real_git_repo(self):
        root = self._temp_repo({
            ".gitignore": "src/ignored.c\n",
            "src/new.c": "jsr $E02A\n",          # untracked, not ignored: must be reported
            "src/ignored.c": "jsr $E02A\n",      # ignored: out of scope
            "tools/gen.py": 'x = "CD_PCMRD"\n',  # untracked tools file: must be reported
            "tests/fixtures/data.json": '{"n": "CD_PCMRD"}\n',  # outside ENTRY_SCOPE
        })
        bad = unexpected_hits(root, ENTRY_TERM, ENTRY_SCOPE)
        self.assertEqual(bad, ["src/new.c:1", "tools/gen.py:1"])

    def test_scope_covers_each_entry_term_and_broad_term(self):
        root = self._temp_repo({
            "src/a.c": "jsr $E02A\n",
            "tools/b.py": 'x = "CD_PCMRD"\n',
            "tests/fixtures/c.s": ".word 0xE02A\n",
            "Makefile": "all: CD_PCMRD\n",
            "docs/x.md": "CD_PCMRD cdpcmrd-slot0e\n",   # docs excluded from both scans
            "src/d.c": "/* cdpcmrd-slot0e */\n",         # broad hit only
        })
        self.assertEqual(unexpected_hits(root, ENTRY_TERM, ENTRY_SCOPE),
                         sorted(["Makefile:1", "src/a.c:1", "tests/fixtures/c.s:1", "tools/b.py:1"]))
        self.assertEqual(unexpected_hits(root, BROAD_TERM, BROAD_SCOPE), ["src/d.c:1"])

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
            self.test_entry_names_are_not_wired_into_build_sources()

    def test_git_grep_reported_hit_is_a_failure(self):
        done = subprocess.CompletedProcess([], 0, "tests/host/test_other.py:1:x\n", "")
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
