"""Positive/negative host tests for the CD_CONTNTS ($0C, $E024) synthetic track-attribute fixture (Issue #265).

Synthetic only: the mixed-mode medium, the track-attribute model and the cases are author-defined. The
reference judge here is a local model, not System Card behaviour. No emulator, no reference BIOS read,
no ./rom/ access and no reference callee JSR. The fixture stays BLOCKED_ABI with release_eligible=false.
Passing these tests validates only this fixture and its judge; it claims no CD_CONTNTS ABI compatibility.
"""
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REL = "tests/fixtures/cdcontnts-track-cases.json"
FIXTURE = ROOT / FIXTURE_REL
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8). Changing the fixture requires
# updating this value in the same change.
FIXTURE_SHA256 = "a53ef2c69caa30fa8a360f557720a02b6bca5fc0f9d885e1b1730a606ab1e04f"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
APPROVED_PROVENANCE = ("synthetic: author-defined media and cases only; no System Card BIOS byte, disassembly, "
                       "retail data, or ./rom/ content; no reference callee is executed")
APPROVED_MODEL_ORIGIN = "AUTHOR_SYNTHETIC: CD_CONTNTS の観測ではない"
TOP_LEVEL_KEYS = {"schema", "issue", "slot", "entry_candidate", "candidate_name", "abi_status", "release_eligible",
                  "execution", "provenance", "model_origin", "callee_abi", "query_model", "media", "cases"}
# Exact (stripped) lines that mention CD_CONTNTS/E024 without wiring it into the build or a callable entry.
# Line-level, so any other line in these files still fails. spec/api_contracts.json lines are HYPOTHESIS records.
WIRING_ALLOWLIST = {
    "tools/reference_bios_scenarios.py": {'"CD_SUBQ", "CD_DINFO", "CD_CONTNTS", "CD_SUBRQ / CD_SUBRD",'},
    "spec/api_slots.json": {'"candidate_name": "CD_CONTNTS"'},
    "spec/api_contracts.json": {'"candidate_name": "CD_CONTNTS",', '"candidate_entry": "0xE024",',
                                '"name": "CD_CONTNTS",', '"entry": "0xE024",'},
}
WIRING_PATTERN = re.compile(r"E024|CD_CONTNTS|cdcontnts-track-cases")
FORBIDDEN = ("VERIFIED", "DEV_CALLABLE", "PASS", "OBSERVED")


def _canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def judge(media, track):
    """Local reference judge. media is the selected medium dict or None (no disc). Returns (verdict, attributes|reason)."""
    if media is None:
        return NEGATIVE, "no medium loaded"
    if type(track) is not int:  # bool is refused: type(True) is bool, not int
        return NEGATIVE, "malformed: track must be a plain int"
    entry = next((t for t in media["tracks"] if t["no"] == track), None)
    if entry is None:
        return NEGATIVE, "track number not on the medium"
    return POSITIVE, {"type": entry["type"], "start": entry["start"], "end": entry["end"]}


def lenient_judge(media, track):
    """Deliberately wrong judge: ignores the medium entirely and accepts 1..3. Negatives must catch it."""
    if type(track) is int and 1 <= track <= 3:
        return POSITIVE, None
    return NEGATIVE, "out of range"


class CdContntsTrackFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.media = cls.fixture["media"]
        cls.cases = cls.fixture["cases"]

    def _medium(self, name):
        return None if name == "none" else self.media[name]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cd-contnts-track-fixture/v1")
        self.assertEqual((f["issue"], f["slot"], f["entry_candidate"], f["candidate_name"]),
                         (265, "$0C", "$E024", "CD_CONTNTS"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertEqual(f["callee_abi"], {"arguments": "UNKNOWN", "returns": "UNKNOWN",
                                           "registers": "UNKNOWN", "reference_jsr": "NOT_CALLED"})

    def test_fixture_hash_and_top_level_keys_are_pinned(self):
        self.assertEqual(_canonical_sha256(self.fixture), FIXTURE_SHA256)
        self.assertEqual(set(self.fixture), TOP_LEVEL_KEYS)
        self.assertEqual(self.fixture["provenance"], APPROVED_PROVENANCE)
        self.assertEqual(self.fixture["model_origin"], APPROVED_MODEL_ORIGIN)

    def test_media_model_is_well_formed(self):
        for name, medium in self.media.items():
            with self.subTest(medium=name):
                self.assertIs(medium["synthetic"], True)
                nos = [t["no"] for t in medium["tracks"]]
                self.assertEqual(nos, sorted(set(nos)))
                for t in medium["tracks"]:
                    self.assertIn(t["type"], ("data", "audio"))
                    self.assertLessEqual(t["start"], t["end"])
                    self.assertLess(t["end"], medium["leadout_lba"])
                # Mixed mode: data track 1 at the disc start, audio tracks contiguous after it.
                if medium["tracks"]:
                    self.assertEqual(medium["tracks"][0]["type"], "data")
                    for prev, cur in zip(medium["tracks"], medium["tracks"][1:]):
                        self.assertEqual(cur["start"], prev["end"] + 1)

    def test_every_case_matches_its_expected_verdict_and_attributes(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                verdict, detail = judge(self._medium(case["media"]), case["track"])
                self.assertEqual(verdict, case["expected"])
                if verdict == POSITIVE:
                    self.assertEqual(detail, case["attributes"])

    def test_case_mix_separates_positive_and_negative_groups(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        positives = [c for c in self.cases if c["expected"] == POSITIVE]
        negatives = [c for c in self.cases if c["expected"] == NEGATIVE]
        self.assertEqual({c["media"] for c in positives}, {"mixed"})
        self.assertGreaterEqual(len(positives), 3)
        self.assertEqual({t["type"] for c in positives for t in [c["attributes"]]}, {"data", "audio"})
        self.assertGreaterEqual(len(negatives), 10)
        groups = {c["media"] for c in negatives}
        self.assertTrue({"none", "empty", "mixed"} <= groups, "missing-medium and absent-track negatives required")

    def test_no_negative_case_is_accepted_by_the_judge(self):
        accepted = [c["id"] for c in self.cases
                    if c["expected"] == NEGATIVE and judge(self._medium(c["media"]), c["track"])[0] == POSITIVE]
        self.assertEqual(accepted, [])

    def test_negative_set_catches_a_medium_ignoring_judge(self):
        caught = [c["id"] for c in self.cases
                  if c["expected"] == NEGATIVE and lenient_judge(self._medium(c["media"]), c["track"])[0] == POSITIVE]
        self.assertGreaterEqual(len(caught), 3)
        self.assertTrue(any(i.startswith("neg_no_medium") for i in caught))

    def test_every_malformed_track_type_is_covered_by_a_negative_case(self):
        kinds = {type(c["track"]).__name__ for c in self.cases
                 if c["expected"] == NEGATIVE and c["media"] == "mixed"}
        self.assertTrue({"bool", "str", "float", "NoneType"} <= kinds, f"missing malformed kinds: {kinds}")

    def test_bool_track_is_refused_even_where_1_exists(self):
        mixed = self.media["mixed"]
        self.assertEqual(judge(mixed, True)[0], NEGATIVE)
        self.assertEqual(judge(mixed, 1)[0], POSITIVE)

    def test_fixture_is_not_wired_into_build_or_callable_entries(self):
        text = FIXTURE.read_text(encoding="utf-8")
        for word in FORBIDDEN:
            self.assertNotIn(word, text)
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
        tracked = [n for n in out.decode("utf-8").split("\0") if n]

        def in_scope(rel):  # build inputs, spec data and callable sources; docs/, *.md and tests are out of scope
            return (rel == "Makefile" or rel.startswith(("src/", "tools/", "spec/"))
                    or (rel.startswith("tests/fixtures/") and rel.endswith(".s")))

        hits = []
        for rel in tracked:
            if not in_scope(rel):
                continue
            p = ROOT / rel
            if not p.is_file() or b"\0" in p.read_bytes()[:4096]:
                continue
            allowed = WIRING_ALLOWLIST.get(rel, set())
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if WIRING_PATTERN.search(line) and line.strip() not in allowed:
                    hits.append(f"{rel}: {line.strip()}")
        self.assertEqual(hits, [], "CD_CONTNTS/E024 wired into build or callable sources")
        for rel, allowed in WIRING_ALLOWLIST.items():  # stale allowlist entries must not linger
            self.assertIn(rel, tracked)
            present = {ln.strip() for ln in (ROOT / rel).read_text(encoding="utf-8", errors="ignore").splitlines()}
            self.assertLessEqual(allowed, present, f"stale allowlist entry in {rel}")


if __name__ == "__main__":
    unittest.main()
