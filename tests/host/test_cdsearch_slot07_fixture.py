"""Positive/negative/boundary host tests for the slot-$07 (CD_SEARCH, candidate $E015) synthetic fixture (Issue #250).

Synthetic only: the medium, cases and judge here are author-defined. No emulator, no reference BIOS
read, no ./rom/ access, no reference callee JSR. The fixture is data (tests/fixtures/cdsearch-slot07-cases.json)
judged by a local reference judge; a passing run validates only this fixture and its judge, never
reference CD_SEARCH behaviour. The fixture must stay ``BLOCKED_ABI`` and ``release_eligible=false``,
and must not be wired into any callable entry.
"""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdsearch-slot07-cases.json"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of the fixture data. Changing the
# fixture requires updating this value in the same change, so silent edits are caught.
FIXTURE_SHA256 = "eebc25bf74e7a4b769d7960d409bac653f0c9108395079a81c01b3d1dfeabff0"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"


NAME = "cdsearch-slot07"
# Build/ROM surfaces: any mention is real wiring (Makefile, CI, scripts, ca65/ld65 sources and configs = jump table).
BUILD_GLOBS = ("Makefile", "*.mk", ".github/**/*", "scripts/**/*", "src/**/*")
# spec JSON keys that make a value an executable/registered target; plain evidence/notes strings are allowed.
EXEC_KEYS = {"entry", "target", "callable", "rom_entry", "jump_table", "build", "make", "command", "script"}
# tools/ lines count only when they hand the name to a build/exec step.
TOOL_EXEC = re.compile(r"subprocess|Popen|os\.system|\bmake\b|ca65|ld65|cl65|\.cfg|\bjsr\b|\bjmp\b", re.I)


def _text(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def _json_exec_hits(node, key=None):
    if isinstance(node, dict):
        return any(_json_exec_hits(v, k) for k, v in node.items())
    if isinstance(node, list):
        return any(_json_exec_hits(v, key) for v in node)
    return isinstance(node, str) and NAME in node and key in EXEC_KEYS


def wiring_violations(root):
    """Return relative paths where the fixture is really wired (build, ROM jump table, exec registration).
    Plain references in docs/*.md, spec evidence strings and tools data/comments are allowed."""
    hits = set()
    for glob in BUILD_GLOBS:
        for path in root.glob(glob):
            if path.is_file() and NAME in _text(path):
                hits.add(path)
    for path in (root / "spec").rglob("*.json"):
        if NAME in _text(path):
            try:
                if _json_exec_hits(json.loads(_text(path))):
                    hits.add(path)
            except ValueError:
                hits.add(path)  # unparsable file mentioning the name: fail closed
    for path in (root / "tools").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".sh", ""} and any(
                NAME in line and TOOL_EXEC.search(line) for line in _text(path).splitlines()):
            hits.add(path)
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in hits)


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def judge(medium, track, lba):
    """Reference judge for a seek to (track, lba) on the synthetic medium. Returns POSITIVE only for an
    integer LBA inside an audio track's range. Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI."""
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE  # malformed: wrong type (bool, float, str are refused)
    entry = next((t for t in medium["tracks"] if t["no"] == track), None)
    if entry is None or entry["type"] != "audio":
        return NEGATIVE  # no such track, or a data track designation
    return POSITIVE if entry["start"] <= lba <= entry["end"] else NEGATIVE


def lenient_judge(medium, track, lba):
    """Deliberately wrong judge (ignores track type and track range): the negative cases must catch it."""
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE
    return POSITIVE if 0 <= lba < medium["leadout_lba"] else NEGATIVE


class CdSearchSlot07FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.medium = cls.fixture["medium"]
        cls.cases = cls.fixture["cases"]

    def test_metadata_is_blocked_synthetic_and_not_releasable(self):
        f = self.fixture
        self.assertEqual(f["schema"], "oss-pce-cd-bios/cdsearch-slot07-fixture/v1")
        self.assertEqual((f["slot"], f["entry_candidate"], f["candidate_name"]), ("$07", "$E015", "CD_SEARCH"))
        self.assertEqual(f["abi_status"], "BLOCKED_ABI")
        self.assertIs(f["release_eligible"], False)
        self.assertEqual(f["execution"], "NOT_RUN")
        self.assertIs(self.medium["synthetic"], True)
        self.assertEqual(f["callee_abi"]["reference_jsr"], "NOT_CALLED")
        for key in ("arguments", "returns", "registers"):
            self.assertEqual(f["callee_abi"][key], "UNKNOWN")

    def test_fixture_hash_matches_recorded_value(self):
        self.assertEqual(canonical_sha256(self.fixture), FIXTURE_SHA256)

    def test_io_range_is_recorded_and_consistent_with_medium(self):
        io = self.fixture["io_range"]
        self.assertEqual((io["lba_min"], io["lba_max"], io["track_numbers"]), (0, 200, [1, 2]))
        self.assertEqual(self.medium["leadout_lba"], io["lba_max"])
        self.assertEqual([t["no"] for t in self.medium["tracks"]], io["track_numbers"])

    def test_every_case_matches_its_expected_verdict(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(judge(self.medium, case["track"], case["lba"]), case["expected"])

    def test_case_mix_covers_positive_negative_and_boundary(self):
        positives = [c for c in self.cases if c["expected"] == POSITIVE]
        negatives = [c for c in self.cases if c["expected"] == NEGATIVE]
        self.assertGreaterEqual(len(positives), 3)
        self.assertGreaterEqual(len(negatives), 12)
        self.assertEqual(len({c["id"] for c in self.cases}), len(self.cases))
        # Boundary LBAs of the valid audio track are positive; one step outside each edge is negative.
        # Keyed by id: (2, 100.0) would collide with (2, 100) as a dict key.
        by_id = {c["id"]: c["expected"] for c in self.cases}
        self.assertEqual(by_id["pos_audio_start"], POSITIVE)
        self.assertEqual(by_id["pos_audio_end"], POSITIVE)
        self.assertEqual(by_id["neg_before_track"], NEGATIVE)
        self.assertEqual(by_id["neg_leadout"], NEGATIVE)

    def test_no_negative_case_is_accepted_by_the_judge(self):
        accepted = [c["id"] for c in self.cases
                    if c["expected"] == NEGATIVE and judge(self.medium, c["track"], c["lba"]) == POSITIVE]
        self.assertEqual(accepted, [])

    def test_negative_set_catches_a_lenient_judge(self):
        # False-positive guard: a judge that ignores track type/range must be rejected by at least one negative.
        caught = [c["id"] for c in self.cases
                  if c["expected"] == NEGATIVE and lenient_judge(self.medium, c["track"], c["lba"]) == POSITIVE]
        self.assertGreaterEqual(len(caught), 4)

    def test_fixture_is_not_wired_as_a_callable_entry(self):
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("DEV_CALLABLE", text)
        self.assertNotIn("VERIFIED", text)
        contracts = json.loads((ROOT / "spec" / "api_contracts.json").read_text(encoding="utf-8"))
        slot7 = next(entry for entry in contracts["apis"] if entry.get("slot") == 7)
        self.assertEqual(slot7["candidate_name"], "CD_SEARCH")
        self.assertEqual(slot7["verification"]["status"], "NOT_RUN")
        self.assertIs(slot7["verification"]["executed_callee"], False)
        self.assertEqual(slot7["mapping_status"], "HYPOTHESIS")
        self.assertEqual(wiring_violations(ROOT), [], "fixture wired into build/ROM/exec target")

    def _scratch(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, body in files.items():
            f = Path(tmp.name) / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        return Path(tmp.name)

    def test_plain_documentation_references_are_allowed(self):
        root = self._scratch({
            "Makefile": "all:\n\t@echo ok\n",
            "docs/note.md": "see tests/fixtures/cdsearch-slot07-cases.json (BLOCKED_ABI)\n",
            "tools/doc.py": '"""cdsearch-slot07 is a data fixture."""\n# cdsearch-slot07 only read by tests\n',
            "spec/api_contracts.json": json.dumps({"apis": [{"slot": 7, "evidence": "cdsearch-slot07 fixture"}]}),
        })
        self.assertEqual(wiring_violations(root), [])

    def test_wiring_probe_catches_invalid_connections(self):
        base = {"Makefile": "all:\n", "spec/api_contracts.json": "{}"}
        mutations = {
            "makefile": ("Makefile", "check: tests/fixtures/cdsearch-slot07-cases.json\n"),
            "ci": (".github/workflows/ci.yml", "run: make cdsearch-slot07\n"),
            "script": ("scripts/run.sh", "cat cdsearch-slot07\n"),
            "jump_table": ("src/boot.s", "  jmp cdsearch-slot07\n"),
            "linker_cfg": ("src/bank0.cfg", "# cdsearch-slot07\n"),
            "exec_registration": ("spec/api_contracts.json",
                                  json.dumps({"apis": [{"slot": 7, "entry": "cdsearch-slot07"}]})),
            "tool_exec": ("tools/run.py", "subprocess.run(['ca65', 'cdsearch-slot07'])\n"),
        }
        for name, (rel, body) in mutations.items():
            with self.subTest(mutation=name):
                root = self._scratch({**base, rel: body})
                self.assertEqual(wiring_violations(root), [rel])


if __name__ == "__main__":
    unittest.main()
