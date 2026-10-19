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
FIXTURE_ALIASES = (NAME, "cdsearch_slot07", "fixtures/*.json", "fixtures/**")
# Build/ROM surfaces anywhere outside docs/: a non-comment mention is real wiring (Makefile, CMake, CI yaml,
# ca65/ld65 sources and linker configs = ROM jump table, shell/PowerShell runners).
BUILD_SUFFIXES = {".mk", ".cmake", ".yml", ".yaml", ".cfg", ".s", ".inc", ".sh", ".ps1"}
BUILD_NAMES = ("Makefile", "GNUmakefile", "CMakeLists.txt")
# spec JSON: the name is allowed only under these documentary keys (at any ancestor level).
DOC_KEYS = {"evidence", "notes", "note", "description", "fixture", "fixtures", "source", "sources", "rationale"}
# Python/other runners in tools/ and tests/ that execute something and also mention the name.
EXEC_RUNNERS = re.compile(r"subprocess|Popen|os\.system|os\.exec|\bmake\b|ca65|ld65|cl65|\bjsr\b|\bjmp\b", re.I)
SKIP_DIRS = {".git", "docs", "rom", "node_modules", "__pycache__"}
SELF = Path(__file__).resolve()


def _text(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def _code_lines(path):
    """Lines of a build/runner file without whole-line comments (# or ;), so prose mentions don't count."""
    return [ln for ln in _text(path).splitlines() if not ln.lstrip().startswith(("#", ";", "//"))]


def _json_exec_hits(node, documentary=False):
    if isinstance(node, dict):
        return any(
            (any(a in k for a in FIXTURE_ALIASES) and not documentary)
            or _json_exec_hits(v, documentary or k in DOC_KEYS)
            for k, v in node.items())
    if isinstance(node, list):
        return any(_json_exec_hits(v, documentary) for v in node)
    return isinstance(node, str) and not documentary and any(a in node for a in FIXTURE_ALIASES)


def wiring_violations(root):
    """Return relative paths where the fixture is really wired (build, ROM jump table, exec registration).
    Plain references in docs/, spec evidence strings and comments/docstrings are allowed.
    ponytail: detection is by name/alias substring; a wildcard that picks the fixture up without naming it
    is only caught for the aliases above."""
    hits = set()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if not path.is_file() or SKIP_DIRS & set(rel.parts) or path.resolve() == SELF \
                or rel.as_posix() == "tests/fixtures/cdsearch-slot07-cases.json":
            continue
        build = path.suffix in BUILD_SUFFIXES or path.name in BUILD_NAMES or path.name.startswith("Makefile")
        if build and any(a in ln for ln in _code_lines(path) for a in FIXTURE_ALIASES):
            hits.add(rel.as_posix())
        elif rel.parts[0] == "spec" and path.suffix != ".md":
            text = _text(path)
            if any(a in text for a in FIXTURE_ALIASES):
                try:
                    bad = path.suffix == ".json" and _json_exec_hits(json.loads(text))
                except ValueError:
                    bad = True  # unparsable: fail closed
                if bad or path.suffix != ".json":
                    hits.add(rel.as_posix())
        elif rel.parts[0] in ("tools", "tests") and path.suffix in {".py", ".js", ".ts", ""}:
            code = "\n".join(_code_lines(path))
            if NAME in code and EXEC_RUNNERS.search(code):
                hits.add(rel.as_posix())
    return sorted(hits)


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
        self.assertTrue((ROOT / "Makefile").is_file())
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
            "Makefile": "# cdsearch-slot07 must stay unwired\nall:\n\t@echo ok\n",
            "docs/note.md": "see tests/fixtures/cdsearch-slot07-cases.json (BLOCKED_ABI) and make jmp jsr\n",
            "tools/doc.py": '"""cdsearch-slot07 is a data fixture."""\n# cdsearch-slot07 only read by tests\n',
            "src/a.s": "; cdsearch-slot07 is not linked\n",
            "spec/api_contracts.json": json.dumps({"apis": [
                {"slot": 7, "evidence": "cdsearch-slot07 fixture", "notes": {"a": ["cdsearch-slot07"]}}]}),
        })
        self.assertEqual(wiring_violations(root), [])

    def test_wiring_probe_catches_invalid_connections(self):
        base = {"Makefile": "all:\n", "spec/api_contracts.json": "{}"}
        spec = "spec/api_contracts.json"
        mutations = {
            "makefile": ("Makefile", "check: tests/fixtures/cdsearch-slot07-cases.json\n"),
            "makefile_wildcard": ("Makefile", "check: $(wildcard tests/fixtures/*.json)\n"),
            "tests_makefile": ("tests/Makefile", "t: cdsearch-slot07-cases.json\n"),
            "cmake": ("CMakeLists.txt", "add_custom_target(x DEPENDS cdsearch-slot07)\n"),
            "ci": (".github/workflows/ci.yml", "run: make cdsearch-slot07\n"),
            "root_yaml": ("ci.yml", "run: cdsearch-slot07\n"),
            "script": ("scripts/run.sh", "cat cdsearch-slot07\n"),
            "jump_table": ("src/boot.s", "  jmp cdsearch-slot07\n"),
            "linker_cfg": ("tests/fixtures/x.cfg", "FILE cdsearch-slot07\n"),
            "exec_registration": (spec, json.dumps({"apis": [{"slot": 7, "entry": "cdsearch-slot07"}]})),
            "nested_entry": (spec, json.dumps({"apis": [{"entry": {"x": "cdsearch-slot07"}}]})),
            "entries_list": (spec, json.dumps({"entries": ["cdsearch-slot07"]})),
            "name_as_key": (spec, json.dumps({"cdsearch-slot07": {"callable": True}})),
            "spec_yaml": ("spec/x.yaml", "entry: cdsearch-slot07\n"),
            "tool_exec": ("tools/run.py", "subprocess.run(['ca65', 'cdsearch-slot07'])\n"),
            "tool_split": ("tools/run.py", "cmd = ['cdsearch-slot07']\nsubprocess.run(cmd)\n"),
            "test_exec": ("tests/host/test_x.py", "subprocess.run(['make', 'cdsearch-slot07'])\n"),
        }
        for name, (rel, body) in mutations.items():
            with self.subTest(mutation=name):
                root = self._scratch({**base, rel: body})
                self.assertEqual(wiring_violations(root), [rel])

    def test_wiring_probe_detects_reference_appended_to_real_makefile_copy(self):
        root = self._scratch({"Makefile": (ROOT / "Makefile").read_text(encoding="utf-8")})
        self.assertEqual(wiring_violations(root), [])
        with (root / "Makefile").open("a", encoding="utf-8") as fh:
            fh.write("\ncheck: tests/fixtures/cdsearch-slot07-cases.json\n")
        self.assertEqual(wiring_violations(root), ["Makefile"])


if __name__ == "__main__":
    unittest.main()
