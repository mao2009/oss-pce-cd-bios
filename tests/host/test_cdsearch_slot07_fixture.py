"""Positive/negative/boundary host tests for the slot-$07 (CD_SEARCH, candidate $E015) synthetic fixture (Issue #250).

Synthetic only: the medium, cases and judge here are author-defined. No emulator, no reference BIOS
read, no ./rom/ access, no reference callee JSR. The fixture is data (tests/fixtures/cdsearch-slot07-cases.json)
judged by a local reference judge; a passing run validates only this fixture and its judge, never
reference CD_SEARCH behaviour. The fixture must stay ``BLOCKED_ABI`` and ``release_eligible=false``,
and must not be wired into any callable entry.
"""
import fnmatch
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "cdsearch-slot07-cases.json"
# sha256 of the canonical JSON (sort_keys, compact separators, UTF-8) of the fixture data. Changing the
# fixture requires updating this value in the same change, so silent edits are caught.
FIXTURE_SHA256 = "eebc25bf74e7a4b769d7960d409bac653f0c9108395079a81c01b3d1dfeabff0"
POSITIVE, NEGATIVE = "POSITIVE", "NEGATIVE"
APPROVED_PROVENANCE = ("synthetic: author-defined medium and cases only; no System Card BIOS byte, disassembly, "
                       "retail data, or ./rom/ content; no reference callee is executed")
TOP_LEVEL_KEYS = {"schema", "issue", "slot", "entry_candidate", "candidate_name", "abi_status", "release_eligible",
                  "execution", "provenance", "callee_abi", "medium", "io_range", "cases"}


NAME = "cdsearch-slot07"
FIXTURE_ALIASES = (NAME, "cdsearch_slot07", "fixtures/*.json", "fixtures/**")
FIXTURE_REL = "tests/fixtures/cdsearch-slot07-cases.json"
# spec JSON: the name is allowed only under these documentary keys (at any ancestor level).
DOC_KEYS = {"evidence", "notes", "note", "description", "fixture", "fixtures", "source", "sources", "rationale"}
# A .py file that mentions the name is wiring only if it also loads/executes something (docstrings are allowed).
EXEC_RUNNERS = re.compile(r"subprocess|Popen|os\.system|os\.exec|\bmake\b|ca65|ld65|cl65|\bjsr\b|\bjmp\b"
                          r"|open\(|read_text|json\.load|glob|rglob|listdir|scandir|import_module", re.I)
SKIP_DIRS = {".git", "docs", "rom", "node_modules", "__pycache__"}
COMMENT_PREFIXES = ("#", ";", "//", "rem ", "::")
# In C-family files "#include"/"#define" are code, not comments.
C_SUFFIXES = {".c", ".h", ".cpp", ".hpp", ".cc"}
C_COMMENT_PREFIXES = ("//", "/*", "*")
SELF = Path(__file__).resolve()
WILDCARD = re.compile(r"[\w./\-]*[*?][\w./*?\-]*")


def _text(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def _code_lines(path):
    """Lines of a file without whole-line comments (# ; // REM ::), so prose mentions don't count."""
    prefixes = C_COMMENT_PREFIXES if path.suffix in C_SUFFIXES else COMMENT_PREFIXES
    return [ln for ln in _text(path).splitlines() if not ln.lstrip().lower().startswith(prefixes)]


def _wildcard_hits(line):
    """True if a glob-like token in the line would match the fixture path (fnmatch semantics)."""
    for tok in WILDCARD.findall(line):
        tok = tok[2:] if tok.startswith("./") else tok
        if ("/" in tok and fnmatch.fnmatchcase(FIXTURE_REL, tok)) or                 (tok[:1].isalpha() and "/" not in tok and fnmatch.fnmatchcase(FIXTURE_REL.rsplit("/", 1)[1], tok)):
            return True
    return False


def _json_exec_hits(node, documentary=False):
    if isinstance(node, dict):
        return any(
            (any(a in k.lower() for a in FIXTURE_ALIASES) and not documentary)
            or _json_exec_hits(v, documentary or k in DOC_KEYS)
            for k, v in node.items())
    if isinstance(node, list):
        return any(_json_exec_hits(v, documentary) for v in node)
    return isinstance(node, str) and not documentary and any(a in node.lower() for a in FIXTURE_ALIASES)


def _tracked_files(root):
    """Tracked files (git ls-files) when root is a git work tree, so untracked/generated files are excluded;
    otherwise (scratch dirs in tests) every file below root."""
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
        return [root / n for n in out.decode("utf-8").split("\0") if n]
    return [p for p in root.rglob("*") if p.is_file()]


def wiring_violations(root):
    """Return relative paths where the fixture is really wired (build, ROM jump table, exec registration).
    Every tracked text file is scanned (any suffix: .bat .cmd .asm .c .h .ld .s .mk .py ...), case-insensitively,
    except docs/, *.md, the fixture itself and this test. spec/ JSON is judged structurally (documentary keys
    allowed). Comment lines are ignored; .py files need an exec/load keyword besides the name.
    LIMITS (ponytail): text scan only. Not caught: a name assembled at run time (N=cdsearch- then $(N)slot07,
    string concatenation), wildcard forms beyond fnmatch-style tokens (regex, brace expansion), a loader in a
    .py file that reaches the fixture without naming it or any EXEC_RUNNERS keyword, encoded/binary files.
    Not full coverage."""
    hits = set()
    for path in _tracked_files(root):
        rel = path.relative_to(root)
        if SKIP_DIRS & set(rel.parts) or path.suffix == ".md" or path.resolve() == SELF or rel.as_posix() == FIXTURE_REL:
            continue
        if b"\0" in path.read_bytes()[:4096]:
            continue
        low = _text(path).lower()
        if rel.parts[0] == "spec":
            if any(a in low for a in FIXTURE_ALIASES):
                try:
                    bad = path.suffix == ".json" and _json_exec_hits(json.loads(_text(path)))
                except ValueError:
                    bad = True  # unparsable: fail closed
                if bad or path.suffix != ".json":
                    hits.add(rel.as_posix())
            continue
        code = "\n".join(_code_lines(path))
        lowcode = code.lower()
        named = any(a in lowcode for a in FIXTURE_ALIASES) or any(_wildcard_hits(ln) for ln in code.splitlines())
        if named and (path.suffix != ".py" or EXEC_RUNNERS.search(code)):
            hits.add(rel.as_posix())
    return sorted(hits)


def canonical_sha256(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def judge_reason(medium, track, lba):
    """(verdict, reason) for a seek to (track, lba). POSITIVE only for an integer LBA inside an audio
    track's range. bool is rejected explicitly (type(x) is int; True == 1 would otherwise select data track 1)."""
    if type(track) is bool:
        return NEGATIVE, "bool is not an int track (malformed)"
    if type(track) is not int or type(lba) is not int:
        return NEGATIVE, "malformed: wrong type (float, str, bool are refused)"
    entry = next((t for t in medium["tracks"] if t["no"] == track), None)
    if entry is None or entry["type"] != "audio":
        return NEGATIVE, "no such track, or a data track designation"
    if entry["start"] <= lba <= entry["end"]:
        return POSITIVE, "inside audio track range"
    return NEGATIVE, "LBA outside the audio track range"


def judge(medium, track, lba):
    """Reference judge verdict only. Never a PASS/VERIFIED verdict; ABI stays BLOCKED_ABI."""
    return judge_reason(medium, track, lba)[0]


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

    def test_provenance_and_top_level_keys_are_explicitly_pinned(self):
        # Explicit, not hash-only: a re-hashed fixture with new provenance wording or an extra verdict key fails here.
        self.assertEqual(set(self.fixture), TOP_LEVEL_KEYS)
        self.assertEqual(self.fixture["provenance"], APPROVED_PROVENANCE)
        self.assertNotRegex(self.fixture["provenance"], r"(?i)derived|extracted|dumped|copied|based on|from (the )?(system card )?bios")

    def test_bool_track_is_rejected_with_the_fixture_reason(self):
        case = next(c for c in self.cases if c["id"] == "neg_bool_track")
        self.assertEqual(judge_reason(self.medium, case["track"], case["lba"]), (NEGATIVE, case["reason"]))
        # Discriminating medium: track 1 audio, so a judge that accepts True (== 1) would say POSITIVE.
        alt = {"tracks": [{"no": 1, "type": "audio", "start": 0, "end": 99}]}
        self.assertEqual(judge_reason(alt, True, 50), (NEGATIVE, case["reason"]))
        self.assertEqual(judge_reason(alt, 1, 50)[0], POSITIVE)

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
        """Documented limits (not detected, text scan only): runtime-assembled names (N=cdsearch- then $(N)slot07),
        regex/brace-expansion globs, a .py loader that never names the fixture. Not full coverage."""
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
            "bat": ("run.bat", "type cdsearch-slot07\n"),
            "cmd": ("scripts/run.cmd", "copy cdsearch-slot07 x\n"),
            "asm": ("src/x.asm", ' .incbin "cdsearch-slot07"\n'),
            "c_src": ("src/x.c", '#include "cdsearch-slot07.h"\n'),
            "h_src": ("src/x.h", '#define F "cdsearch-slot07"\n'),
            "ld": ("link.ld", "INPUT(cdsearch-slot07)\n"),
            "py_src": ("src/gen.py", "open('cdsearch-slot07')\n"),
            "py_scripts": ("scripts/gen.py", "Path('cdsearch-slot07').read_text()\n"),
            "py_loader_underscore": ("tools/l.py", "data = open('tests/fixtures/cdsearch_slot07')\n"),
            "bare_make_rule": ("build.mak", "x: cdsearch-slot07\n"),
            "bare_make_rule_makefile": ("Makefile", "x: cdsearch-slot07\n"),
            "uppercase": ("Makefile", "x: CDSEARCH-SLOT07\n"),
            "spec_uppercase": (spec, json.dumps({"entries": ["CDSEARCH-SLOT07"]})),
            "wildcard_func": ("Makefile", "x: $(wildcard tests/fixtures/cdsearch*)\n"),
            "wildcard_name": ("scripts/x.sh", "cat tests/fixtures/cdsearch*\n"),
            "wildcard_dir": ("scripts/y.sh", "cat tests/fixtures/*.json\n"),
            "json_outside_spec": ("tests/other.json", json.dumps({"run": "cdsearch-slot07"})),
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
