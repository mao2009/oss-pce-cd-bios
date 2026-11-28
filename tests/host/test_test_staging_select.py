"""Host unit tests for the prototype test-staging selector (Issue #852).

Static only: no cc65, no Geargrafx core, no private media, no network. Git
fixtures are synthetic temporary repositories. Every test must RUN (never
SKIP): a skip is never reported as success here.
"""
import contextlib
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import test_staging_select as SEL  # noqa: E402


def git(repo, *args):
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr[-2000:]}")
    return proc.stdout


def make_repo(*initial_files):
    repo = Path(tempfile.mkdtemp(prefix="staging-select-"))
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "staging-test")
    for rel in initial_files:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("initial\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    git(repo, "tag", "vbase")
    return repo


def write_file(repo, rel, text="changed\n"):
    target = Path(repo) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


MAP_DIR = Path(tempfile.mkdtemp(prefix="staging-select-maps-"))
MAP_COUNTER = [0]


def write_map(directory, document):
    # Maps live outside the repo under test: a map file inside the repo
    # would itself be an untracked input and force L3 (by design).
    del directory
    MAP_COUNTER[0] += 1
    path = MAP_DIR / f"impact-map-{MAP_COUNTER[0]}.json"
    path.write_text(json.dumps(document))
    return str(path)


def provisional_map(rules, apis=None):
    document = {"format": SEL.PROVISIONAL_FORMAT, "rules": rules}
    if apis is not None:
        document["apis"] = apis
    return document


def l1_rule(pattern, reason="provisional test rule", api=""):
    return {"pattern": pattern, "level": "L1",
            "commands": [{"make": "dev-rom"}], "reason": reason, "api": api}


class FailingRunner:
    """Wrap the real runner but fail a chosen git subcommand (no network)."""

    def __init__(self, fail_on, failure=None):
        self.fail_on = set(fail_on)
        self.failure = failure or SEL.GitError("injected", "injected failure")

    def __call__(self, repo, *args):
        if args and args[0] in self.fail_on:
            raise self.failure
        return SEL._real_runner(repo, *args)


class CaptureTests(unittest.TestCase):
    def test_committed_change_is_captured(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.committed, [("M", "src/overrides/slot_3F.s")])
        self.assertEqual(changed.staged, [])
        self.assertEqual(changed.unstaged, [])
        self.assertEqual(changed.untracked, [])

    def test_staged_change_is_captured(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "add", "src/overrides/slot_3F.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.committed, [])
        self.assertEqual(changed.staged, [("M", "src/overrides/slot_3F.s")])

    def test_unstaged_change_is_captured(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.committed, [])
        self.assertEqual(changed.staged, [])
        self.assertEqual(changed.unstaged, [("M", "src/overrides/slot_3F.s")])

    def test_untracked_change_is_captured(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/extra_scratch.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.untracked, ["src/extra_scratch.s"])

    def test_rename_is_delete_plus_add(self):
        repo = make_repo("src/overrides/slot_3F.s")
        git(repo, "mv", "src/overrides/slot_3F.s", "src/overrides/slot_40.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        # --no-renames forces delete+add; a rename is never one silent entry.
        self.assertIn(("D", "src/overrides/slot_3F.s"), changed.staged)
        self.assertIn(("A", "src/overrides/slot_40.s"), changed.staged)
        for _origin, status, _path in changed.all_classifiable():
            self.assertNotIn(status, ("R", "C"))


class BaseFailureTests(unittest.TestCase):
    def test_unknown_base_falls_back_to_l3(self):
        repo = make_repo("src/a.s")
        selection, _ = SEL.run_selector(str(repo), "refs/heads/does-not-exist-xyz")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "base")

    def test_fetch_failure_falls_back_to_l3(self):
        repo = make_repo("src/a.s")
        runner = FailingRunner({"fetch"}, SEL.GitError("fetch", "no network in test"))
        with self.assertRaises(SEL.GitError) as caught:
            SEL.collect_changes(str(repo), "vbase", runner=runner, fetch=True)
        self.assertEqual(caught.exception.kind, "fetch")
        selection, _ = SEL.run_selector(str(repo), "vbase", runner=runner, fetch=True)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "fetch")

    def test_diff_failure_falls_back_to_l3(self):
        repo = make_repo("src/a.s")
        runner = FailingRunner({"diff"}, SEL.GitError("diff", "broken diff"))
        selection, _ = SEL.run_selector(str(repo), "vbase", runner=runner)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "diff")

    def test_shallow_history_falls_back_to_l3(self):
        repo = make_repo("src/a.s")

        def shallow_runner(repo_path, *args):
            if args[:1] == ("rev-parse",) and args[-1] == "--is-shallow-repository":
                return "true\n"
            return SEL._real_runner(repo_path, *args)

        selection, _ = SEL.run_selector(str(repo), "vbase", runner=shallow_runner)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "shallow")


class MapTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("src/overrides/slot_3F.s")
        write_file(self.repo, "src/overrides/slot_3F.s", "changed\n")
        git(self.repo, "commit", "-qam", "change")

    def select(self, document_or_none, **kwargs):
        if document_or_none is None:
            map_path = str(self.repo / "does-not-exist.json")
        elif isinstance(document_or_none, str):
            MAP_COUNTER[0] += 1
            raw = MAP_DIR / f"raw-map-{MAP_COUNTER[0]}.json"
            raw.write_text(document_or_none)
            map_path = str(raw)
        else:
            map_path = write_map(self.repo, document_or_none)
        kwargs.setdefault("map_path", map_path)
        # Provisional envelope is synthetic-test-only: the normal CLI path
        # always starts the canonical loader, so tests say so explicitly.
        kwargs.setdefault("loader", SEL.ProvisionalJsonMapLoader())
        return SEL.run_selector(str(self.repo), "vbase", **kwargs)

    def test_missing_map_falls_back_to_l3(self):
        selection, _ = self.select(None)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "map-missing-or-inconsistent")

    def test_no_map_argument_falls_back_to_l3(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_invalid_json_map_falls_back_to_l3(self):
        selection, _ = self.select("{not json")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_unknown_format_tag_falls_back_to_l3(self):
        # The canonical Task-A format is unknown to the provisional loader:
        # it must fall back, never be misread.
        selection, _ = self.select({"format": "task-a/canonical-99",
                                    "rules": [l1_rule("src/**")]})
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_unknown_level_is_inconsistent(self):
        bad = dict(l1_rule("src/**"))
        bad["level"] = "L9"
        selection, _ = self.select(provisional_map([bad]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_unknown_make_target_is_inconsistent(self):
        bad = dict(l1_rule("src/**"))
        bad["commands"] = [{"make": "test-magic-does-not-exist"}]
        selection, _ = self.select(provisional_map([bad]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_nonexistent_script_is_inconsistent(self):
        bad = dict(l1_rule("src/**"))
        bad["commands"] = [{"script": "tests/integration/no-such-script.py"}]
        selection, _ = self.select(provisional_map([bad]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)

    def test_unclassified_file_falls_back_to_l3(self):
        selection, _ = self.select(provisional_map([l1_rule("docs/**")]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "unclassified-file")

    def test_classified_change_selects_level_without_fallback(self):
        selection, _ = self.select(provisional_map([l1_rule("src/overrides/*.s")]))
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertEqual(selection.fallback_reason, "none")
        self.assertTrue(selection.partial)
        self.assertEqual(len(selection.selected), 1)

    def test_untracked_file_forces_l3_despite_covering_map(self):
        write_file(self.repo, "src/brand_new.s")
        selection, _ = self.select(provisional_map([l1_rule("src/**")]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "untracked-inputs")

    def test_map_loader_is_an_interface(self):
        self.assertIsInstance(SEL.ProvisionalJsonMapLoader(), SEL.MapLoader)

        class CustomLoader(SEL.MapLoader):
            def load(self, path):
                return [SEL.MapRule(pattern="src/**", level="L2",
                                    commands=[{"make": "check"}],
                                    reason="custom loader rule")], {}

        selection, _ = self.select(None, loader=CustomLoader(),
                                   map_path="ignored-by-custom-loader.json")
        self.assertEqual(selection.level, "L2")
        self.assertFalse(selection.fallback)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("src/overrides/slot_3F.s")
        write_file(self.repo, "src/overrides/slot_3F.s", "changed\n")
        git(self.repo, "commit", "-qam", "change")
        self.map_path = write_map(self.repo, provisional_map(
            [l1_rule("src/overrides/*.s")]))
        # Provisional envelope is synthetic-test-only: say the loader
        # explicitly (the normal CLI path always starts canonical).
        self.loader = SEL.ProvisionalJsonMapLoader()

    def test_require_cc65_preserved_on_every_command(self):
        for kwargs in ({"map_path": self.map_path, "loader": self.loader}, {}):
            selection, _ = SEL.run_selector(str(self.repo), "vbase", **kwargs)
            self.assertTrue(selection.selected)
            for command in selection.selected:
                self.assertEqual(command["env"],
                                 {SEL.REQUIRE_CC65_ENV: "1"},
                                 f"cc65 gate dropped on {command['argv']}")

    def test_l3_selection_keeps_required_gate_commands(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase")
        argvs = [" ".join(cmd["argv"]) for cmd in selection.selected]
        self.assertTrue(any(argv == "make test" for argv in argvs))
        self.assertTrue(any(argv == "make test-real-core" for argv in argvs))

    def test_empty_change_set_selects_l3_never_passes_empty(self):
        clean = make_repo("src/a.s")
        empty_map = write_map(clean, provisional_map([l1_rule("src/**")]))
        selection, _ = SEL.run_selector(str(clean), "vbase", map_path=empty_map,
                                        loader=SEL.ProvisionalJsonMapLoader())
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.selected[0]["env"],
                         {SEL.REQUIRE_CC65_ENV: "1"})

    def test_l1_output_marks_partial_and_lists_unselected(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path,
                                        loader=self.loader)
        report = SEL.render(selection)
        self.assertIn("PARTIAL", report)
        self.assertIn("never MERGE_READY evidence on its own", report)
        self.assertTrue(selection.unselected)
        self.assertIn("UNSELECTED", report)
        self.assertIn("SELECTED (1):", report)
        self.assertIn("REASONS (", report)
        self.assertIn("fallback-reason: none", report)

    def test_l3_output_distinguishes_local_l3_from_required_ci(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase")
        report = SEL.render(selection)
        self.assertIn("LOCAL-L3 vs REQUIRED-CI", report)
        for check in ("bios-dev", "verify", "guard-bios-dev",
                      "guard-verify", "guard-core-harness", "guard-bcdbin-core"):
            self.assertIn(check, report)

    def test_skip_policy_is_always_printed(self):
        for kwargs in ({"map_path": self.map_path, "loader": self.loader}, {}):
            selection, _ = SEL.run_selector(str(self.repo), "vbase", **kwargs)
            report = SEL.render(selection)
            self.assertIn("SKIP-POLICY", report)
            self.assertIn("never PASS", report)
            self.assertIn(SEL.REQUIRE_CC65_ENV, report)


class EvidenceTests(unittest.TestCase):
    def fingerprint(self, **overrides):
        base = {"head": "a" * 40, "base": "b" * 40,
                "inputs_sha256": "c" * 64,
                "map_sha256": "d" * 64,
                "map_id": "test-staging-map-v0/v1@" + "e" * 40,
                "selector": SEL.SELECTOR_ID,
                "api": "none", "level": "L1",
                "commands_sha256": "f" * 64,
                "python": "3.12.3",
                "platform": "linux", "toolchain": "{\"cc65\": \"abc\"}",
                "mode": "debug",
                "env": {"MODE": "debug",
                        SEL.REQUIRE_CC65_ENV: "",
                        "TOOLS_DIR_sha256": "unset"}}
        base.update(overrides)
        return base

    def test_exact_match_reuses(self):
        stored = self.fingerprint()
        self.assertTrue(SEL.evidence_reusable(stored, self.fingerprint()))

    def test_any_field_mismatch_refuses_reuse(self):
        stored = self.fingerprint()
        mutations = [
            {"head": "0" * 40}, {"base": "0" * 40},
            {"inputs_sha256": "0" * 64},
            {"map_sha256": "0" * 64},
            {"map_id": "test-staging-map-v0/v1@" + "0" * 40},
            {"selector": "other-selector"},
            {"api": "MA_MUL8U"}, {"level": "L3"},
            {"commands_sha256": "0" * 64}, {"python": "3.11.0"},
            {"platform": "other"}, {"toolchain": "{\"cc65\": \"zzz\"}"},
            {"mode": "release"}, {"env": {"MODE": "release",
                                            SEL.REQUIRE_CC65_ENV: "",
                                            "TOOLS_DIR_sha256": "unset"}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    SEL.evidence_reusable(stored, self.fingerprint(**mutation)))

    def test_unknown_toolchain_never_reuses(self):
        stored = self.fingerprint(toolchain="unknown")
        self.assertFalse(
            SEL.evidence_reusable(stored, self.fingerprint(toolchain="unknown")))

    def test_fingerprint_changes_with_head(self):
        repo = make_repo("src/a.s")
        before = SEL.evidence_fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        write_file(repo, "src/a.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        after = SEL.evidence_fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        self.assertNotEqual(before["head"], after["head"])
        self.assertNotEqual(before["inputs_sha256"], after["inputs_sha256"])
        self.assertFalse(SEL.evidence_reusable(before, after))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("src/overrides/slot_3F.s")
        write_file(self.repo, "src/overrides/slot_3F.s", "changed\n")
        git(self.repo, "commit", "-qam", "change")
        self.map_path = write_map(self.repo, provisional_map(
            [l1_rule("src/overrides/*.s", api="MA_MUL8U")],
            apis={"MA_MUL8U": {"commands": [{"make": "dev-rom"}],
                                 "reason": "provisional api entry",
                                 "level": "L1"}}))
        self.loader = SEL.ProvisionalJsonMapLoader()

    def test_known_api_selects_l1_subset(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="MA_MUL8U",
                                        loader=self.loader)
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertEqual([cmd["argv"] for cmd in selection.selected],
                         [["make", "dev-rom"]])

    def test_unknown_api_falls_back_to_l3(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="NOPE",
                                        loader=self.loader)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertIn("NOPE", selection.fallback_reason)

    def test_api_mode_with_out_of_scope_file_falls_back_to_l3(self):
        write_file(self.repo, "src/common/other.s", "changed\n")
        git(self.repo, "add", "src/common/other.s")
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="MA_MUL8U",
                                        loader=self.loader)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)


# ---------------------------------------------------------------------------
# Canonical Task-A map loader connection tests (Issue #854 dependency).
#
# These tests pin the FINAL canonical envelope (name test-staging-map-v0,
# schema_version 1, 40-hex base_commit fingerprint, exact Required Checks,
# first-match-wins classification, 81 slot-number identifiers with NO name
# key -- names resolve via the spec/api_slots.json ledger) against
# synthetic fixtures shaped like the Task-A map at SEL.CANONICAL_SOURCE_HEAD
# (FINAL pin 2ab14d08, read-only fetch of
# origin/agent/test-staging-map-v0-20261128). This supersedes the interim
# cf428568 record. Post-merge re-verification on the merged map is still
# required after Owner merges #856.
# ---------------------------------------------------------------------------

CANONICAL_PIN = "2ab14d08fc9fbdc704da1b1f07c96501850b542e"

# Read-only pin of the merged-main Task-A map used by
# CanonicalDefaultTests.test_cli_end_to_end_with_real_canonical_map.
# Recorded 2026-11-28 as 3dad5dad (doc-fix-only, schema stable); branch
# moved to 71ad00ab (doc-only local_media/commercial_media_access wording,
# schema unchanged), then to e8d51e75 (enumeration-probe non-destructive
# test-only change, schema stable -- verified: diff 71ad00ab..e8d51e75
# touches only tests/host/test_test_staging_map.py), then to aa03c061
# (synthetic-git-env isolation + live-tree legacy-guard drop, review
# 6095863631 A-1+A-2 -- verified read-only: diff e8d51e75..aa03c061
# touches only tests/host/test_test_staging_map.py, and the
# tools/test_staging_map.json + spec/api_slots.json blobs are byte-
# identical), then to f21e7aa3 (HOME/XDG hook isolation, review
# 6096132357 A-1 -- map/ledger blobs byte-identical, schema stable).
# FINAL (Wave2): PR #856 merged the Task-A map into main as merge commit
# 53d9a315, and the Task-A branch ref was deleted on the remote
# (verified: `git ls-remote origin agent/test-staging-map-v0-20261128`
# is empty), so the branch ref no longer resolves in fresh (CI) clones
# and branch-ref resolution skipped there (unlisted SKIP -> aggregate
# reject). Pin is now the merged-main merge commit itself, resolved as a
# commit object (present in full clones; CI uses fetch-depth 0). Map blob
# at this pin is 0106b1260d81ea573363aa2c2621838193ec9e76, byte-identical
# to the f21e7aa3 branch-tip blob (verified: content diff empty); ledger
# blob likewise identical; schema stable (v0/schema-1, 81 slots, no name
# key). No checkout, no tracked copy, no ./rom.
REAL_MAP_HEAD_PIN = "53d9a3151ced008968ab9f8b51d1e6e2303f9dc2"

# Expected git blob SHA of tools/test_staging_map.json at REAL_MAP_HEAD_PIN.
REAL_MAP_BLOB_SHA = "0106b1260d81ea573363aa2c2621838193ec9e76"


def canonical_tests(host=(), scripts=(), targets=(), static=()):
    return {"host": list(host), "integration_scripts": list(scripts),
            "dev_targets": list(targets), "static_units": list(static)}


def canonical_entry(entry_id, match_type, match, level, tests,
                    reason="fixture reason", category="dev-api-impl",
                    ci_class="required-ci", required_ci=True):
    return {"id": entry_id, "match_type": match_type, "match": match,
            "level": level, "category": category, "ci_class": ci_class,
            "required_ci": required_ci, "tests": tests, "reason": reason}


def canonical_l1_entry():
    return canonical_entry(
        "fixture-slot-3F", "exact", "src/overrides/slot_3F.s", "L1",
        canonical_tests(host=["tests.host.test_api_contracts",
                              "tests.host.test_api_table"],
                        targets=["dev-rom"]),
        reason="fixture DEV-implemented slot")


def canonical_fallback_entry():
    return canonical_entry(
        "fallback-unclassified", "glob", "**", "L3",
        canonical_tests(targets=["test", "check"]),
        reason="fixture catch-all", category="shared-code")


def canonical_slots():
    # FINAL Task-A form: rows carry NO name (identity by slot number;
    # names resolve via the spec/api_slots.json ledger, e.g. slot 0x3F ->
    # MA_MUL8U). Reasons use the slot-number form (e.g. $3F), never a
    # ledger name.
    slots = []
    for number in range(81):
        if number == 0x3F:
            slots.append({
                "slot": number, "dev_implemented": True,
                "dev_source": "src/overrides/slot_3F.s", "level": "L1",
                "tests": canonical_tests(
                    host=["tests.host.test_api_contracts"],
                    targets=["dev-rom"]),
                "reason": f"fixture DEV slot ${number:02X}"})
        else:
            slots.append({
                "slot": number,
                "dev_implemented": False, "dev_source": None,
                "level": "L3",
                "tests": canonical_tests(
                    host=["tests.host.test_api_contracts"],
                    targets=["test"]),
                "reason": f"fixture slot ${number:02X}"})
    return slots


def canonical_doc(entries=None, slots=None, name="test-staging-map-v0",
                  version=1, base_commit="9" * 40, required_checks=None,
                  static_units=("make-check:contracts",)):
    if entries is None:
        entries = [canonical_l1_entry(), canonical_fallback_entry()]
    if required_checks is None:
        required_checks = sorted(SEL.REQUIRED_CI_CHECKS)
    return {"name": name, "schema_version": version,
            "base_commit": base_commit,
            "required_checks": list(required_checks),
            "static_units_defined": list(static_units),
            "entries": entries, "fallback": copy.deepcopy(entries[-1]),
            "api_slots": slots if slots is not None else canonical_slots()}


class CanonicalLoaderTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("src/overrides/slot_3F.s")
        write_file(self.repo, "src/overrides/slot_3F.s", "changed\n")
        git(self.repo, "commit", "-qam", "change")

    def select_canonical(self, document, **kwargs):
        map_path = write_map(self.repo, document)
        kwargs.setdefault("map_path", map_path)
        kwargs.setdefault("loader", SEL.CanonicalJsonMapLoader())
        return SEL.run_selector(str(self.repo), "vbase", **kwargs)

    def assert_inconsistent(self, document):
        map_path = write_map(self.repo, document)
        with self.assertRaises(SEL.MapError):
            SEL.CanonicalJsonMapLoader().load(map_path)
        selection, _ = SEL.run_selector(
            str(self.repo), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason,
                         "map-missing-or-inconsistent")

    def test_dependency_head_pin_is_recorded(self):
        # FINAL pin 2ab14d08 (Task-A map v0, PR #856 HEAD, read-only
        # fetch of origin/agent/test-staging-map-v0-20261128): slot rows
        # carry NO name; identity by slot number via spec/api_slots.json.
        # Supersedes the interim cf428568 record. Post-merge
        # re-verification on the merged map is still required after Owner
        # merges #856.
        self.assertEqual(SEL.CANONICAL_SOURCE_HEAD, CANONICAL_PIN)
        self.assertRegex(SEL.CANONICAL_SOURCE_HEAD, r"^[0-9a-f]{40}$")
        self.assertEqual(SEL.CANONICAL_MAP_NAME, "test-staging-map-v0")
        self.assertEqual(SEL.CANONICAL_SCHEMA_VERSION, 1)

    def test_canonical_l1_selects_partial_with_required_env(self):
        selection, report = self.select_canonical(canonical_doc())
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertEqual(selection.fallback_reason, "none")
        self.assertTrue(selection.partial)
        self.assertTrue(selection.selected)
        for command in selection.selected:
            self.assertEqual(command["env"],
                             {SEL.REQUIRE_CC65_ENV: "1"},
                             f"cc65 gate dropped on {command['argv']}")
        self.assertIn("PARTIAL", report)
        self.assertIn("never MERGE_READY evidence on its own", report)
        self.assertIn("SKIP-POLICY", report)

    def test_first_match_wins_over_covering_glob(self):
        # Canonical semantics: entry order decides, not the max level.
        # (The provisional loader would take max -> L3 here.)
        cover = canonical_entry(
            "fixture-cover", "glob", "src/overrides/*.s", "L3",
            canonical_tests(targets=["test"]),
            reason="fixture covering glob", category="shared-code")
        document = canonical_doc(
            entries=[canonical_l1_entry(), cover,
                     canonical_fallback_entry()])
        selection, _ = self.select_canonical(document)
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertTrue(any("fixture-slot-3F" in reason
                            for reason in selection.reasons))
        self.assertFalse(any("fixture-cover" in reason
                             for reason in selection.reasons))

    def test_unknown_file_falls_back_to_l3(self):
        write_file(self.repo, "docs/brand_new.md")
        git(self.repo, "add", "docs/brand_new.md")
        git(self.repo, "commit", "-qam", "docs")
        selection, report = self.select_canonical(canonical_doc())
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "map-rule-requires-L3")
        self.assertIn("LOCAL-L3 vs REQUIRED-CI", report)

    def test_provisional_envelope_is_never_canonical(self):
        # Isolation: the provisional envelope must not be misread as the
        # canonical format (and vice versa -- see MapTests above).
        self.assert_inconsistent(
            provisional_map([l1_rule("src/overrides/*.s")]))

    def test_bad_name_version_fingerprint_rejected(self):
        cases = {
            "name": {"name": "test-staging-map-something-else"},
            "schema_version": {"version": 2},
            "base_commit": {"base_commit": "not-a-fingerprint"},
            "required_checks": {"required_checks": ["bios-dev"]},
            "static_units": {"static_units": []},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                self.assert_inconsistent(canonical_doc(**override))

    def test_fallback_must_be_last_l3_catch_all(self):
        entries = [canonical_fallback_entry(), canonical_l1_entry()]
        self.assert_inconsistent(canonical_doc(entries=entries))
        bad_level = canonical_doc()
        bad_level["fallback"] = copy.deepcopy(canonical_l1_entry())
        bad_level["entries"][-1] = copy.deepcopy(canonical_l1_entry())
        self.assert_inconsistent(bad_level)

    def test_non_81_slot_table_rejected(self):
        self.assert_inconsistent(
            canonical_doc(slots=canonical_slots()[:80]))
        dup = canonical_slots()
        dup[0] = copy.deepcopy(dup[1])
        self.assert_inconsistent(canonical_doc(slots=dup))

    def test_unknown_commands_rejected(self):
        bad_target = canonical_doc(entries=[canonical_entry(
            "bad-target", "exact", "src/overrides/slot_3F.s", "L1",
            canonical_tests(targets=["test-magic-does-not-exist"])),
            canonical_fallback_entry()])
        self.assert_inconsistent(bad_target)
        bad_host = canonical_doc(entries=[canonical_entry(
            "bad-host", "exact", "src/overrides/slot_3F.s", "L1",
            canonical_tests(host=["tests.host.test_no_such_module_xyz"])),
            canonical_fallback_entry()])
        self.assert_inconsistent(bad_host)
        bad_script = canonical_doc(entries=[canonical_entry(
            "bad-script", "exact", "src/overrides/slot_3F.s", "L1",
            canonical_tests(scripts=["tests/integration/no-such.py"])),
            canonical_fallback_entry()])
        self.assert_inconsistent(bad_script)
        bad_static = canonical_doc(entries=[canonical_entry(
            "bad-static", "exact", "src/overrides/slot_3F.s", "L1",
            canonical_tests(static=["make-check:no-such-unit"])),
            canonical_fallback_entry()])
        self.assert_inconsistent(bad_static)

    def test_l4_entry_escalated_to_full_l3(self):
        l4_entry = canonical_entry(
            "fixture-optin", "exact", "src/overrides/slot_3F.s", "L4",
            canonical_tests(scripts=["tests/integration/" +
                                     "dev_getver_multi_input.py"],
                            targets=["dev-rom"]),
            reason="fixture opt-in", category="real-core")
        selection, _ = self.select_canonical(
            canonical_doc(entries=[l4_entry, canonical_fallback_entry()]))
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "map-rule-requires-L3")
        argvs = [" ".join(cmd["argv"]) for cmd in selection.selected]
        # Full local-L3 sequence, not the discarded L4 opt-in commands.
        self.assertIn("make test", argvs)
        self.assertIn("make test-real-core", argvs)
        self.assertFalse(any("dev_getver" in argv for argv in argvs))
        self.assertTrue(any("canonical-L4-escalated-to-L3" in reason
                            for reason in selection.reasons))

    def test_empty_tests_l3_entry_runs_full_l3(self):
        repo = make_repo(".github/workflows/pages.yml")
        write_file(repo, ".github/workflows/pages.yml", "changed\n")
        git(repo, "commit", "-qam", "pages")
        empty = canonical_entry(
            "fixture-pages", "exact", ".github/workflows/pages.yml",
            "L3", canonical_tests(), reason="fixture CI page",
            category="ci-workflow")
        map_path = write_map(repo, canonical_doc(
            entries=[empty, canonical_fallback_entry()]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        argvs = [" ".join(cmd["argv"]) for cmd in selection.selected]
        self.assertIn("make test", argvs)
        self.assertIn("make check", argvs)

    def test_slot_number_identifiers_resolve_to_same_subset(self):
        seen = {}
        for api in ("MA_MUL8U", "63", "0x3F", "0x3f", "$3F", "$3f"):
            with self.subTest(api=api):
                selection, _ = self.select_canonical(canonical_doc(),
                                                     api=api)
                self.assertEqual(selection.level, "L1")
                self.assertFalse(selection.fallback)
                seen[api] = sorted(tuple(cmd["argv"])
                                   for cmd in selection.selected)
        for api, argvs in seen.items():
            with self.subTest(api=api):
                self.assertEqual(argvs, seen["MA_MUL8U"])
        # B-3 Wave2 union: the selected set is path-rule commands UNION
        # API slot commands (deduped). The entry carries contracts+table+
        # dev-rom; the slot carries contracts+dev-rom; the union keeps the
        # path-only table evidence (A survives) instead of overwriting it.
        self.assertIn(("make", "dev-rom"), seen["MA_MUL8U"])
        self.assertTrue(any("test_api_contracts.py" in argv
                            for argv in seen["MA_MUL8U"]))
        self.assertTrue(any("test_api_table.py" in argv
                            for argv in seen["MA_MUL8U"]))

    def test_ledger_names_resolve_without_row_names(self):
        # FINAL form: slot rows carry NO name, yet apis resolve through
        # the spec/api_slots.json ledger (slot 0x3F -> MA_MUL8U).
        loader = SEL.CanonicalJsonMapLoader()
        map_path = write_map(self.repo, canonical_doc())
        _rules, apis = loader.load(map_path)
        self.assertIn("MA_MUL8U", apis)
        self.assertEqual(loader.resolve_api("MA_MUL8U"), "MA_MUL8U")
        self.assertEqual(loader.resolve_api("$3F"), "MA_MUL8U")
        self.assertEqual(loader.resolve_api("63"), "MA_MUL8U")

    def test_ledger_slash_name_parts_resolve(self):
        # Ledger slash-names (e.g. "CD_SUBRQ / CD_SUBRD") resolve by full
        # name and by either part, without any row carrying a name.
        loader = SEL.CanonicalJsonMapLoader()
        map_path = write_map(self.repo, canonical_doc())
        loader.load(map_path)
        self.assertEqual(loader.resolve_api("CD_SUBRQ / CD_SUBRD"),
                         "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(loader.resolve_api("CD_SUBRQ"),
                         "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(loader.resolve_api("CD_SUBRD"),
                         "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(loader.resolve_api("$0D"),
                         "CD_SUBRQ / CD_SUBRD")

    def test_name_duplication_rejected(self):
        # FINAL schema: a slot row carrying 'name' duplicates the ledger
        # (single source) and is deformed -> L3, never misread.
        slots = canonical_slots()
        slots[0] = dict(slots[0], name="CD_BOOT")
        self.assert_inconsistent(canonical_doc(slots=slots))

    def test_unknown_api_falls_back_to_l3(self):
        selection, _ = self.select_canonical(canonical_doc(), api="NOPE")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertIn("NOPE", selection.fallback_reason)

    def test_api_scoping_out_of_scope_file_falls_back_to_l3(self):
        write_file(self.repo, "docs/other.md")
        git(self.repo, "add", "docs/other.md")
        git(self.repo, "commit", "-qam", "docs")
        selection, _ = self.select_canonical(canonical_doc(),
                                             api="MA_MUL8U")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)


def full_l3_argvs():
    """The one mandatory local-L3 set, as rendered argv lists."""
    return [SEL._command_argv(cmd) for cmd in SEL.FULL_L3_SEQUENCE]


def assert_full_l3(testcase, selection, fallback_reason=None):
    """Every L3 path emits exactly the full set: never partial-as-full."""
    testcase.assertEqual(selection.level, "L3")
    testcase.assertTrue(selection.fallback)
    testcase.assertFalse(selection.partial)
    testcase.assertEqual(selection.unselected, [])
    testcase.assertEqual([cmd["argv"] for cmd in selection.selected],
                         full_l3_argvs())
    for command in selection.selected:
        testcase.assertEqual(command["env"],
                             {SEL.REQUIRE_CC65_ENV: "1"},
                             f"cc65 gate dropped on {command['argv']}")
    if fallback_reason is not None:
        testcase.assertEqual(selection.fallback_reason, fallback_reason)


class L3FullSequenceTests(unittest.TestCase):
    """FIX1 RED->GREEN: L3 always emits ONE complete mandatory set.

    Before the fix, an L3 decision over a non-empty deduped subset returned
    that subset displayed as full (partial=False, unselected=[]), the
    hardcoded L3 list missed release verification, and the catch-all only
    covered test/test-real-core/check. Each case below asserts the exact
    full argv set.
    """
    def setUp(self):
        self.loader = SEL.ProvisionalJsonMapLoader()

    def select_provisional(self, repo, document, **kwargs):
        map_path = write_map(repo, document)
        kwargs.setdefault("map_path", map_path)
        kwargs.setdefault("loader", self.loader)
        return SEL.run_selector(str(repo), "vbase", **kwargs)

    def test_unknown_tracked_file_selects_full_l3(self):
        repo = make_repo("src/known.s")
        write_file(repo, "src/known.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        selection, _ = self.select_provisional(
            repo, provisional_map([l1_rule("docs/**")]))
        assert_full_l3(self, selection, "unclassified-file")

    def test_untracked_file_selects_full_l3(self):
        repo = make_repo("src/known.s")
        write_file(repo, "src/known.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        write_file(repo, "src/scratch_new.s")
        selection, _ = self.select_provisional(
            repo, provisional_map([l1_rule("src/**")]))
        assert_full_l3(self, selection, "untracked-inputs")

    def test_safety_gate_change_selects_full_l3(self):
        # A safety-gate file (tools/check.py) maps L3 with a narrow subset
        # ([check]); the emitted set must still be the full sequence.
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "tools/check.py", "changed\n")
        git(repo, "add", "tools/check.py")
        git(repo, "commit", "-qam", "safety")
        safety = dict(l1_rule("tools/check.py", reason="safety gate"))
        safety["level"] = "L3"
        safety["commands"] = [{"make": "check"}]
        selection, _ = self.select_provisional(
            repo, provisional_map(
                [safety, l1_rule("src/overrides/*.s")]))
        assert_full_l3(self, selection, "map-rule-requires-L3")

    def test_l1_l3_mixed_changes_select_full_l3(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        write_file(repo, "tools/check.py", "changed\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qam", "mixed")
        safety = dict(l1_rule("tools/**", reason="safety gate"))
        safety["level"] = "L3"
        safety["commands"] = [{"make": "check"}]
        selection, _ = self.select_provisional(
            repo, provisional_map(
                [safety, l1_rule("src/overrides/*.s")]))
        assert_full_l3(self, selection, "map-rule-requires-L3")

    def test_empty_selection_selects_full_l3(self):
        # A classification yielding zero commands is a 0-test run, never
        # PASS -> full L3 (direct select: an L1 rule with no commands).
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "a.s")])
        rules = [SEL.MapRule(pattern="a.s", level="L1", commands=[],
                             reason="empty fixture rule")]
        selection = SEL.select(changed, rules, {}, fingerprint={})
        assert_full_l3(self, selection, "empty-selection")
        self.assertTrue(any("empty-selection" in reason
                            for reason in selection.reasons))

    def test_missing_map_selects_full_l3(self):
        repo = make_repo("src/a.s")
        write_file(repo, "src/a.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        selection, _ = SEL.run_selector(
            str(repo), "vbase",
            map_path=str(repo / "does-not-exist.json"))
        assert_full_l3(self, selection, "map-missing-or-inconsistent")

    def test_inconsistent_map_selects_full_l3(self):
        # A provisional envelope via the normal (canonical) path is
        # inconsistent -> full L3, never a misread subset.
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        selection, _ = self.select_provisional(
            repo, provisional_map([l1_rule("src/overrides/*.s")]),
            loader=SEL.CanonicalJsonMapLoader())
        assert_full_l3(self, selection, "map-missing-or-inconsistent")

    def test_l4_auto_escalation_selects_full_l3(self):
        l4_entry = canonical_entry(
            "fixture-optin", "exact", "src/overrides/slot_3F.s", "L4",
            canonical_tests(scripts=["tests/integration/" +
                                     "dev_getver_multi_input.py"],
                            targets=["dev-rom"]),
            reason="fixture opt-in", category="real-core")
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, canonical_doc(
            entries=[l4_entry, canonical_fallback_entry()]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        assert_full_l3(self, selection, "map-rule-requires-L3")
        argvs = [" ".join(cmd["argv"]) for cmd in selection.selected]
        self.assertFalse(any("dev_getver" in argv for argv in argvs))

    def test_partial_rule_case_selects_full_l3(self):
        # A canonical L3 rule with a non-empty subset (narrower than full)
        # must still emit the full sequence, displayed as full.
        narrow = canonical_entry(
            "fixture-narrow-l3", "exact", "src/overrides/slot_3F.s",
            "L3", canonical_tests(targets=["test"]),
            reason="fixture narrow L3", category="shared-code")
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, canonical_doc(
            entries=[narrow, canonical_fallback_entry()]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        assert_full_l3(self, selection, "map-rule-requires-L3")
        self.assertTrue(any("l3-requires-full" in reason
                            for reason in selection.reasons))


class FingerprintContentTests(unittest.TestCase):
    """FIX2 RED->GREEN: the fingerprint binds bytes, not just paths.

    Before the fix, only origin:status:path was hashed, so the same HEAD /
    same status / same path with different bytes reused old evidence.
    """
    def fingerprint(self, repo, changed, **kwargs):
        kwargs.setdefault("map_path", self.map_path)
        return SEL.evidence_fingerprint(repo, changed, **kwargs)

    def setUp(self):
        self.map_doc = provisional_map([l1_rule("src/**")])
        self.map_path = write_map(None, self.map_doc)

    def test_same_path_different_bytes_refuses_reuse(self):
        repo = make_repo("f.s")
        write_file(repo, "f.s", "version-one\n")
        git(repo, "add", "f.s")
        first = self.fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        self.assertNotEqual(first["inputs_sha256"], "unknown")
        write_file(repo, "f.s", "version-two\n")
        git(repo, "add", "f.s")
        second = self.fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        # Same HEAD (no commit), same staged status, same path -- but the
        # old origin:status:path-only hash would call these identical.
        self.assertEqual(first["head"], second["head"])
        self.assertEqual(
            SEL.collect_changes(str(repo), "vbase").staged,
            [("M", "f.s")])
        self.assertNotEqual(first["inputs_sha256"],
                            second["inputs_sha256"])
        self.assertFalse(SEL.evidence_reusable(first, second))

    def test_identical_bytes_match_on_inputs(self):
        repo = make_repo("f.s")
        write_file(repo, "f.s", "same\n")
        git(repo, "add", "f.s")
        first = self.fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        second = self.fingerprint(
            str(repo), SEL.collect_changes(str(repo), "vbase"))
        self.assertEqual(first["inputs_sha256"], second["inputs_sha256"])
        self.assertNotEqual(first["inputs_sha256"], "unknown")

    def test_forbidden_area_refuses_reuse_unread(self):
        # Bytes under rom/ are private reference inputs: never hashed, and
        # the fingerprint refuses reuse instead of identifying them.
        repo = make_repo("src/a.s")
        write_file(repo, "rom/secret.bin", "private\n")
        git(repo, "add", "rom/secret.bin")
        changed = SEL.collect_changes(str(repo), "vbase")
        fingerprint = self.fingerprint(str(repo), changed)
        self.assertEqual(fingerprint["inputs_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))

    def test_unidentifiable_head_refuses_reuse(self):
        for head in ("unknown", "manual"):
            with self.subTest(head=head):
                changed = SEL.ChangedInputs(head=head, base="vbase")
                fingerprint = SEL.evidence_fingerprint(
                    str(make_repo("a.s")), changed,
                    map_path=self.map_path)
                self.assertEqual(fingerprint["inputs_sha256"], "unknown")
                self.assertFalse(
                    SEL.evidence_reusable(fingerprint, fingerprint))

    def test_map_content_change_refuses_reuse(self):
        repo = make_repo("f.s")
        write_file(repo, "f.s", "x\n")
        git(repo, "add", "f.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        first = SEL.evidence_fingerprint(str(repo), changed,
                                         map_path=self.map_path,
                                         level="L1",
                                         commands=[{"make": "check"}])
        other_map = write_map(None, provisional_map([l1_rule("other/**")]))
        second = SEL.evidence_fingerprint(str(repo), changed,
                                          map_path=other_map,
                                          level="L1",
                                          commands=[{"make": "check"}])
        self.assertNotEqual(first["map_sha256"], "unknown")
        self.assertNotEqual(first["map_sha256"], second["map_sha256"])
        self.assertFalse(SEL.evidence_reusable(first, second))

    def test_missing_map_refuses_reuse(self):
        repo = make_repo("f.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        fingerprint = SEL.evidence_fingerprint(str(repo), changed)
        self.assertEqual(fingerprint["map_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))

    def test_selection_and_conditions_change_refuses_reuse(self):
        repo = make_repo("f.s")
        write_file(repo, "f.s", "x\n")
        git(repo, "add", "f.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        base_kwargs = {"map_path": self.map_path, "api": "none",
                       "level": "L1", "commands": [{"make": "check"}]}
        first = SEL.evidence_fingerprint(str(repo), changed, **base_kwargs)
        for label, override in {
                "api": {"api": "MA_MUL8U"},
                "level": {"level": "L3"},
                "commands": {"commands": [{"make": "test"}]},
                "mode": {},
        }.items():
            with self.subTest(label=label):
                kwargs = dict(base_kwargs)
                kwargs.update(override)
                if label == "mode":
                    other = SEL.evidence_fingerprint(str(repo), changed,
                                                     mode="release",
                                                     **kwargs)
                else:
                    other = SEL.evidence_fingerprint(str(repo), changed,
                                                     **kwargs)
                self.assertFalse(SEL.evidence_reusable(first, other))


class CanonicalDefaultTests(unittest.TestCase):
    """FIX3: the normal CLI path starts the canonical loader.

    Provisional maps load only with an explicitly passed provisional loader
    (synthetic-test use); the default path and an explicit --map both start
    canonical, and a missing/inconsistent canonical map fails closed to L3.
    """
    def test_default_path_starts_canonical_loader(self):
        # A provisional envelope without an explicit loader is inconsistent
        # to the canonical default -> L3 (never a misread subset).
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, provisional_map(
            [l1_rule("src/overrides/*.s")]))
        defaulted, _ = SEL.run_selector(str(repo), "vbase",
                                        map_path=map_path)
        assert_full_l3(self, defaulted, "map-missing-or-inconsistent")
        explicit, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        self.assertEqual(explicit.level, "L1")
        self.assertFalse(explicit.fallback)

    def test_missing_default_canonical_map_fails_closed(self):
        repo = make_repo("src/a.s")
        write_file(repo, "src/a.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        selection, _ = SEL.run_selector(str(repo), "vbase")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason,
                         "map-missing-or-inconsistent")

    def test_cli_end_to_end_with_real_canonical_map(self):
        # Read-only use of the merged-main Task-A map (REAL_MAP_HEAD_PIN,
        # PR #856 merge commit on main). No checkout, no copy into tracked
        # paths, no ./rom. Resolved as a commit object (present in full
        # clones; CI uses fetch-depth 0): the pre-merge Task-A branch ref
        # was deleted on the remote after #856 merged, so branch-ref
        # resolution is gone. If the pin object is unavailable (e.g.
        # shallow clone) the test skips; in CI it runs.
        try:
            present = subprocess.run(
                ["git", "cat-file", "-e", REAL_MAP_HEAD_PIN],
                cwd=str(ROOT), capture_output=True, text=True,
                timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            self.skipTest(f"cannot inspect {REAL_MAP_HEAD_PIN}: {error}")
        if present.returncode != 0:
            self.skipTest(
                f"merged-main map pin absent: {present.stderr[-200:]}")
        blob = subprocess.run(
            ["git", "show", f"{REAL_MAP_HEAD_PIN}:tools/test_staging_map.json"],
            cwd=str(ROOT), capture_output=True, timeout=120)
        self.assertEqual(blob.returncode, 0)
        digest = subprocess.run(
            ["git", "hash-object", "--stdin"], cwd=str(ROOT),
            input=blob.stdout, capture_output=True, timeout=120)
        self.assertEqual(digest.returncode, 0)
        self.assertEqual(digest.stdout.decode().strip(), REAL_MAP_BLOB_SHA)
        try:
            repo = make_repo("src/overrides/slot_3F.s")
            # B-3/B-4 repo binding: the default CLI loader reads map +
            # ledger + Makefile + pins + host/script existence from
            # --repo, never a silent ROOT fallback and never a
            # cross-checkout mix. Populate the synthetic repo with the
            # (unmodified) reference files the real map needs, plus the
            # read-only Task-A blob as the repo-bound default map
            # (tools/test_staging_map.json), all committed as baseline so
            # the only diff is the slot change; nothing is copied into
            # tracked paths (the synthetic repo lives in a temp dir).
            import shutil as _shutil
            (repo / "spec").mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(str(ROOT / "spec" / "api_slots.json"),
                             str(repo / "spec" / "api_slots.json"))
            _shutil.copyfile(str(ROOT / "Makefile"),
                             str(repo / "Makefile"))
            (repo / "tools").mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(str(ROOT / "tools" / "dependencies.json"),
                             str(repo / "tools" / "dependencies.json"))
            (repo / "tools" / "test_staging_map.json"
             ).write_bytes(blob.stdout)
            for sub in ("host", "integration"):
                src_dir = ROOT / "tests" / sub
                dst_dir = repo / "tests" / sub
                dst_dir.mkdir(parents=True, exist_ok=True)
                for item in src_dir.iterdir():
                    if item.is_symlink() or not item.is_file():
                        continue
                    _shutil.copyfile(str(item), str(dst_dir / item.name))
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "fixture baseline")
            baseline = git(repo, "rev-parse", "HEAD").strip()
            write_file(repo, "src/overrides/slot_3F.s", "changed\n")
            git(repo, "commit", "-qam", "change")
            cli = [sys.executable,
                   str(ROOT / "tools" / "test_staging_select.py"),
                   "--repo", str(repo), "--base", baseline,
                   "--json"]
            proc = subprocess.run(cli, capture_output=True, text=True,
                                  timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["level"], "L1")
            self.assertFalse(payload["fallback"])
            argv = [cmd["argv"] for cmd in payload["selected"]]
            self.assertIn(["make", "dev-rom"], argv)
            fingerprint = payload["fingerprint"]
            self.assertIn("test-staging-map-v0",
                          fingerprint["map_id"])
            self.assertNotEqual(fingerprint["map_sha256"], "unknown")
            self.assertEqual(fingerprint["level"], "L1")
            self.assertNotEqual(fingerprint["commands_sha256"],
                                "unknown")
        finally:
            pass


class ApiAliasLevelTests(unittest.TestCase):
    """FIX4 RED->GREEN: no bare hex, slot levels kept, --api never lowers.

    Before the fix, str(number) vs bare-hex spellings collided via
    setdefault, and the slot level was dropped (--api forced at least L1,
    so an L3 slot could select an L1 subset).
    """
    def load_fixture(self):
        loader = SEL.CanonicalJsonMapLoader()
        map_path = write_map(None, canonical_doc())
        _rules, apis = loader.load(map_path)
        return loader, apis

    def test_api_slot_levels_are_kept(self):
        _loader, apis = self.load_fixture()
        self.assertEqual(apis["MA_MUL8U"]["level"], "L1")
        self.assertEqual(apis["CD_BOOT"]["level"], "L3")

    def test_boundary_slots_resolve(self):
        loader, _apis = self.load_fixture()
        self.assertEqual(loader.resolve_api("0"), "CD_BOOT")
        self.assertEqual(loader.resolve_api("0x00"), "CD_BOOT")
        self.assertEqual(loader.resolve_api("$00"), "CD_BOOT")
        self.assertEqual(loader.resolve_api("80"),
                         "MA_DIV8U / MA_CBASIS")
        self.assertEqual(loader.resolve_api("0x50"),
                         "MA_DIV8U / MA_CBASIS")
        self.assertEqual(loader.resolve_api("$50"),
                         "MA_DIV8U / MA_CBASIS")

    def test_decimal_means_decimal_in_collisions(self):
        loader, _apis = self.load_fixture()
        # "10" is slot 10 decimal (CD_SUBQ), never slot 0x10 == 16.
        self.assertEqual(loader.resolve_api("10"), "CD_SUBQ")
        self.assertEqual(loader.resolve_api("16"), "AD_RESET")
        self.assertEqual(loader.resolve_api("0x10"), "AD_RESET")
        self.assertEqual(loader.resolve_api("$10"), "AD_RESET")
        self.assertNotEqual(loader.resolve_api("10"),
                              loader.resolve_api("0x10"))

    def test_bare_hex_is_banned_to_l3(self):
        loader, _apis = self.load_fixture()
        for bad in ("3F", "3f", "A", "0A", "FF", "ff"):
            with self.subTest(bad=bad):
                self.assertEqual(loader.resolve_api(bad), bad)
                repo = make_repo("src/overrides/slot_3F.s")
                write_file(repo, "src/overrides/slot_3F.s", "changed\n")
                git(repo, "commit", "-qam", "change")
                map_path = write_map(repo, canonical_doc())
                selection, _ = SEL.run_selector(
                    str(repo), "vbase", map_path=map_path, api=bad,
                    loader=SEL.CanonicalJsonMapLoader())
                self.assertEqual(selection.level, "L3")
                self.assertTrue(selection.fallback)

    def test_unimplemented_slot_selects_full_l3(self):
        # CD_BOOT (slot 0) is L3 in the fixture: requesting it runs the
        # full sequence, never a filtered subset.
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, canonical_doc())
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path, api="CD_BOOT",
            loader=SEL.CanonicalJsonMapLoader())
        assert_full_l3(self, selection)

    def test_api_never_lowers_required_level(self):
        # A rule matching at L1 for the requested API, but the slot itself
        # is L3: the result is full L3, not the L1 subset (direct select).
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L1",
                             commands=[{"make": "dev-rom"}],
                             reason="fixture", api="SLOT_X")]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "fixture slot",
                             "level": "L3"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={})
        assert_full_l3(self, selection, "api-slot-requires-L3")

    def test_unknown_and_invalid_apis_select_l3(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, canonical_doc())
        for bad in ("NOPE", "", "81", "0x51", "$51", "-1", "0x",
                    "$", "CD_SUB", "063"):
            with self.subTest(bad=bad):
                selection, _ = SEL.run_selector(
                    str(repo), "vbase", map_path=map_path, api=bad,
                    loader=SEL.CanonicalJsonMapLoader())
                self.assertEqual(selection.level, "L3")
                self.assertTrue(selection.fallback)

    def test_ledger_names_and_slash_parts_resolve(self):
        loader, _apis = self.load_fixture()
        self.assertEqual(loader.resolve_api("MA_MUL8U"), "MA_MUL8U")
        self.assertEqual(loader.resolve_api("CD_SUBRQ / CD_SUBRD"),
                         "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(loader.resolve_api("CD_SUBRQ"),
                         "CD_SUBRQ / CD_SUBRD")
        self.assertEqual(loader.resolve_api("CD_SUBRD"),
                         "CD_SUBRQ / CD_SUBRD")


class B1SymlinkTraversalTests(unittest.TestCase):
    """B-1: symlink/.. traversal, outside access, non-regular fail-closed.

    Synthetic tmp repos + sentinel files only (no ./rom). The sentinel
    content is never read: tests spy on Path.read_bytes and assert the
    sentinel is never opened, not just that the output is unknown.
    """
    def test_untracked_symlink_to_outside_sentinel_not_read(self):
        repo = make_repo("f.s")
        sentinel_dir = Path(tempfile.mkdtemp(prefix="b1-sentinel-"))
        sentinel = sentinel_dir / "secret.bin"
        sentinel.write_text("SENTINEL-BYTES\n")
        link = Path(repo) / "link.s"
        link.symlink_to(sentinel)
        reads = []
        real_read = Path.read_bytes

        def spy(self, *args, **kwargs):
            if str(self) == str(sentinel) or str(self) == str(link):
                reads.append(str(self))
            return real_read(self, *args, **kwargs)
        orig = Path.read_bytes
        Path.read_bytes = spy
        try:
            hashed = SEL._input_content_hash(
                str(repo), "untracked", "??", "link.s")
        finally:
            Path.read_bytes = orig
        self.assertEqual(hashed, "unknown")
        self.assertEqual(reads, [],
                         f"sentinel/link was read: {reads}")
        changed = SEL.ChangedInputs(head="h" * 40, base="vbase",
                                    untracked=["link.s"])
        fingerprint = SEL.evidence_fingerprint(
            str(repo), changed, map_path=str(repo / "does-not-exist.json"))
        self.assertEqual(fingerprint["inputs_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))

    def test_dotdot_and_absolute_refused_without_read(self):
        repo = make_repo("f.s")
        for bad in ("../outside.s", "a/../../b.s", "/abs/path.s",
                    "", "a//b.s", "a/./b.s"):
            with self.subTest(bad=bad):
                reads = []
                real_read = Path.read_bytes
                orig = Path.read_bytes
                Path.read_bytes = lambda self, *a, **k: (
                    reads.append(str(self)), real_read(self, *a, **k))[1]
                try:
                    self.assertEqual(
                        SEL._input_content_hash(
                            str(repo), "untracked", "??", bad), "unknown")
                finally:
                    Path.read_bytes = orig
                self.assertEqual(reads, [])

    def test_forbidden_top_refused_pre_read(self):
        repo = make_repo("f.s")
        # Direct forbidden spec: no git/FS read occurs, marker unknown.
        self.assertEqual(
            SEL._input_content_hash(str(repo), "committed", "M",
                                    "rom/secret.bin"), "unknown")
        self.assertEqual(
            SEL._input_content_hash(str(repo), "untracked", "??",
                                    "rom/secret.bin"), "unknown")

    def test_non_regular_files_refused(self):
        repo = make_repo("f.s")
        target_dir = Path(repo) / "adir"
        target_dir.mkdir()
        self.assertEqual(
            SEL._input_content_hash(str(repo), "untracked", "??",
                                    "adir"), "unknown")
        fifo = Path(repo) / "pipe.fifo"
        try:
            os.mkfifo(str(fifo))
            self.assertEqual(
                SEL._input_content_hash(str(repo), "untracked", "??",
                                        "pipe.fifo"), "unknown")
        except (OSError, AttributeError):
            pass

    def test_map_symlink_refused_on_both_read_sites(self):
        repo = make_repo("f.s")
        real_map = write_map(repo, provisional_map(
            [l1_rule("src/**")]))
        link_path = str(Path(real_map).parent / "link-map.json")
        try:
            os.symlink(real_map, link_path)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        # Site 1: _map_identity must not read the link target.
        reads = []
        real_read = Path.read_bytes
        orig = Path.read_bytes

        def spy(self, *args, **kwargs):
            if str(self) == real_map:
                reads.append(str(self))
            return real_read(self, *args, **kwargs)
        Path.read_bytes = spy
        try:
            sha, mid = SEL._map_identity(link_path)
        finally:
            Path.read_bytes = orig
        self.assertEqual((sha, mid), ("unknown", "unknown"))
        self.assertEqual(reads, [])
        # Site 2: loader.load must raise MapError (fail-closed L3).
        with self.assertRaises(SEL.MapError):
            SEL.ProvisionalJsonMapLoader().load(link_path)
        with self.assertRaises(SEL.MapError):
            SEL.CanonicalJsonMapLoader().load(link_path)
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=link_path,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "map-missing-or-inconsistent")


class B2EvidenceHardeningTests(unittest.TestCase):
    """B-2: evidence_reusable prefer-refuse-on-incomplete + CLI env."""
    def full(self, **overrides):
        base = {"head": "a" * 40, "base": "b" * 40,
                "inputs_sha256": "c" * 64, "map_sha256": "d" * 64,
                "map_id": "test-staging-map-v0/v1@" + "e" * 40,
                "selector": SEL.SELECTOR_ID, "api": "none",
                "level": "L1", "commands_sha256": "f" * 64,
                "python": "3.12.3", "platform": "linux",
                "toolchain": "{\"cc65\": \"abc\"}",
                "mode": "debug",
                "env": {"MODE": "debug", SEL.REQUIRE_CC65_ENV: "",
                        "TOOLS_DIR_sha256": "unset"}}
        base.update(overrides)
        return base

    def test_empty_vs_empty_refuses(self):
        self.assertFalse(SEL.evidence_reusable({}, {}))

    def test_both_sides_missing_key_refuses(self):
        stored = self.full()
        del stored["toolchain"]
        current = self.full()
        del current["toolchain"]
        self.assertFalse(SEL.evidence_reusable(stored, current))

    def test_none_empty_bad_type_refuses(self):
        good = self.full()
        for bad_key, bad_value in [
                ("head", None), ("base", ""),
                ("inputs_sha256", None), ("map_sha256", ""),
                ("map_id", None), ("selector", ""),
                ("api", None), ("level", ""),
                ("commands_sha256", None), ("python", ""),
                ("platform", None), ("toolchain", ""),
                ("mode", None), ("env", None),
                ("env", "not-a-dict"),
                ("env", {"__malformed__": "x"}),
                ("toolchain", {"not": "a-string"}),
                ("head", 12345)]:
            with self.subTest(bad=(bad_key, bad_value)):
                stored = self.full(**{bad_key: bad_value})
                self.assertFalse(SEL.evidence_reusable(stored, good))
                self.assertFalse(SEL.evidence_reusable(good, stored))

    def test_unconfirmable_tool_identity_refuses_even_when_equal(self):
        stored = self.full(toolchain="unknown")
        current = self.full(toolchain="unknown")
        self.assertFalse(SEL.evidence_reusable(stored, current))
        stored = self.full(head="unknown")
        self.assertFalse(SEL.evidence_reusable(stored, stored))
        stored = self.full(head="manual")
        self.assertFalse(SEL.evidence_reusable(stored, stored))

    def test_real_cli_env_diff_refuses(self):
        repo = make_repo("f.s")
        write_file(repo, "f.s", "x\n")
        git(repo, "add", "f.s")
        changed = SEL.collect_changes(str(repo), "vbase")
        map_path = write_map(repo, provisional_map([l1_rule("src/**")]))
        with unittest.mock.patch.dict(os.environ, {"TOOLS_DIR": "/tmp/tools-a",
                                                   SEL.REQUIRE_CC65_ENV: "1"},
                                      clear=False):
            first = SEL.evidence_fingerprint(
                str(repo), changed, map_path=map_path, level="L1",
                commands=[{"make": "check"}])
        with unittest.mock.patch.dict(os.environ, {"TOOLS_DIR": "/tmp/tools-b",
                                                   SEL.REQUIRE_CC65_ENV: "1"},
                                      clear=False):
            second = SEL.evidence_fingerprint(
                str(repo), changed, map_path=map_path, level="L1",
                commands=[{"make": "check"}])
        self.assertNotEqual(first["env"], second["env"])
        self.assertFalse(SEL.evidence_reusable(first, second))
        # No raw TOOLS_DIR path leaks into the fingerprint.
        for fingerprint in (first, second):
            self.assertNotIn("/tmp/tools-a", json.dumps(fingerprint))
            self.assertNotIn("/tmp/tools-b", json.dumps(fingerprint))

    def test_normal_full_match_and_mismatch(self):
        stored = self.full()
        self.assertTrue(SEL.evidence_reusable(stored, self.full()))
        self.assertFalse(SEL.evidence_reusable(
            stored, self.full(level="L3")))
        self.assertFalse(SEL.evidence_reusable(
            stored, self.full(commands_sha256="0" * 64)))


class B3MalformedFailClosedTests(unittest.TestCase):
    """B-3: element-type validation at the boundary, no over-broad except."""
    def assert_map_error(self, document):
        map_path = write_map(None, document)
        with self.assertRaises(SEL.MapError):
            SEL.CanonicalJsonMapLoader().load(map_path)

    def test_required_checks_element_object_is_map_error(self):
        document = canonical_doc(
            required_checks=sorted(SEL.REQUIRED_CI_CHECKS)[:-1] + [{"x": 1}])
        self.assert_map_error(document)

    def test_dev_targets_element_object_is_map_error(self):
        entries = [canonical_entry(
            "bad-target", "exact", "src/overrides/slot_3F.s", "L1",
            {"host": [], "integration_scripts": [],
             "dev_targets": [{"make": "check"}], "static_units": []}),
            canonical_fallback_entry()]
        self.assert_map_error(canonical_doc(entries=entries))

    def test_other_list_bad_types_are_map_error(self):
        for key, bad_value in [
                ("host", [{"ref": 1}]),
                ("integration_scripts", [{"script": 1}]),
                ("static_units", [{"unit": 1}]),
                ("host", [""]), ("dev_targets", [""])]:
            with self.subTest(key=key, bad=bad_value):
                tests = canonical_tests()
                tests[key] = bad_value
                entries = [canonical_entry(
                    "bad", "exact", "src/overrides/slot_3F.s", "L1",
                    tests), canonical_fallback_entry()]
                self.assert_map_error(canonical_doc(entries=entries))

    def test_required_field_bad_types_are_map_error(self):
        bad = canonical_l1_entry()
        for key, bad_value in [("category", {"x": 1}),
                               ("required_ci", "yes"),
                               ("reason", {"x": 1}),
                               ("level", {"x": 1})]:
            with self.subTest(key=key):
                entry = dict(bad, **{key: bad_value})
                self.assert_map_error(canonical_doc(
                    entries=[entry, canonical_fallback_entry()]))

    def test_cli_e2e_malformed_map_no_traceback_full_l3(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        malformed = canonical_doc()
        malformed["required_checks"] = [{"not": "a-string"}]
        map_path = write_map(repo, malformed)
        cli = [sys.executable, str(ROOT / "tools" / "test_staging_select.py"),
               "--repo", str(repo), "--base", "vbase",
               "--map", map_path, "--json"]
        proc = subprocess.run(cli, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["level"], "L3")
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["fallback_reason"],
                         "map-missing-or-inconsistent")
        self.assertFalse(payload["partial"])
        self.assertEqual(payload["unselected"], [])
        self.assertEqual([tuple(cmd["argv"]) for cmd in payload["selected"]],
                         [tuple(argv) for argv in full_l3_argvs()])
        fingerprint = payload["fingerprint"]
        self.assertEqual(fingerprint["level"], "L3")
        stored = dict(fingerprint)
        self.assertFalse(SEL.evidence_reusable(stored, dict(fingerprint)))


class B4RuleApiIntegrityTests(unittest.TestCase):
    """B-4: exact vs glob, first-match-before-API, never lower."""
    def test_preceding_l3_not_skipped_by_api(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="*.s", level="L3",
                             commands=[{"make": "check"}],
                             reason="safety L3", api=""),
                 SEL.MapRule(pattern="f.s", level="L1",
                             commands=[{"make": "dev-rom"}],
                             reason="later L1", api="SLOT_X", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        assert_full_l3(self, selection, "api-first-match-mismatch")

    def test_exact_with_metachars_is_literal(self):
        rules = [SEL.MapRule(pattern="file[1].s", level="L1",
                             commands=[{"make": "check"}],
                             reason="exact", literal=True)]
        hit = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "file[1].s")])
        self.assertEqual(
            SEL.select(hit, rules, {}, fingerprint={}).level, "L1")
        miss = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "file1.s")])
        self.assertEqual(
            SEL.select(miss, rules, {}, fingerprint={}).level, "L3")

    def test_exact_list_per_element_literal(self):
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        entry = canonical_entry(
            "fixture-list", "exact-list",
            ["src/overrides/slot_3F.s", "docs/a[1].md"], "L1",
            canonical_tests(targets=["dev-rom"]), reason="list")
        document = canonical_doc(
            entries=[entry, canonical_fallback_entry()])
        map_path = write_map(repo, document)
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        self.assertEqual(selection.level, "L1")
        # A glob-looking sibling must not match the literal list element.
        repo2 = make_repo("docs/a1.md")
        write_file(repo2, "docs/a1.md", "changed\n")
        git(repo2, "commit", "-qam", "change")
        selection2, _ = SEL.run_selector(
            str(repo2), "vbase", map_path=map_path,
            loader=SEL.CanonicalJsonMapLoader())
        self.assertEqual(selection2.level, "L3")

    def test_multi_change_api_mismatch_is_l3(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase",
            committed=[("M", "a.s"), ("M", "b.s")])
        rules = [SEL.MapRule(pattern="a.s", level="L1",
                             commands=[{"make": "dev-rom"}],
                             reason="a", api="SLOT_X", literal=True),
                 SEL.MapRule(pattern="b.s", level="L1",
                             commands=[{"make": "check"}],
                             reason="b", api="", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        self.assertEqual(selection.level, "L3")

    def test_api_never_lowers_level_or_commands(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L1",
                             commands=[{"make": "dev-rom"}],
                             reason="fixture", api="SLOT_X", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L3"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        assert_full_l3(self, selection, "api-slot-requires-L3")


class B5DisplayExecEquivalenceTests(unittest.TestCase):
    """B-5: displayed command == executed argv via shlex.join."""
    def test_shlex_roundtrip_cases(self):
        import shlex as shlex_module
        cases = [
            ["make", "test"],
            ["python3", "tests/integration/foo bar.py", "a*b"],
            ["python3", "-m", "unittest", "discover", "-p",
             "name with spaces.py"],
            ["python3", "script.py", "a;b", "c$d", "e`f`",
             "g|h", "i&j", "k(l)", "l;m"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                selection = SEL.Selection(
                    level="L1", selected=[{"argv": argv, "env": {
                        SEL.REQUIRE_CC65_ENV: "1"}}],
                    unselected=[], reasons=["r"], fallback=False,
                    fallback_reason="none", partial=True, fingerprint={})
                report = SEL.render(selection)
                line = next(line for line in report.splitlines()
                            if line.startswith("  env "))
                displayed = line.split(" -- ", 1)[1]
                self.assertEqual(displayed, shlex_module.join(argv))
                self.assertEqual(shlex_module.split(displayed), argv)

    def test_selected_unselected_display_and_cc65_kept(self):
        selected = {"argv": ["python3", "a b.py"],
                    "env": {SEL.REQUIRE_CC65_ENV: "1"}}
        unselected = {"argv": ["make", "my target"],
                      "env": {SEL.REQUIRE_CC65_ENV: "1"}}
        selection = SEL.Selection(
            level="L1", selected=[selected], unselected=[unselected],
            reasons=["r"], fallback=False, fallback_reason="none",
            partial=True, fingerprint={})
        report = SEL.render(selection)
        self.assertIn("-- python3 'a b.py'", report)
        self.assertIn("(not run at this level) make 'my target'", report)
        for command in selection.selected + selection.unselected:
            self.assertEqual(command["env"], {SEL.REQUIRE_CC65_ENV: "1"})


# ---------------------------------------------------------------------------
# Wave2 blockers (review 6095526960): B-1 map-read boundary, B-2 toolchain
# execution identity, B-3 API-command union, B-4 --repo consistency.
#
# All fixtures are synthetic temp dirs + sentinel bytes only (no ./rom,
# no real checkouts, no network). Read-API-uncalled proofs spy on
# Path.read_bytes and assert zero calls before refusal.
# ---------------------------------------------------------------------------


class Wave2B1MapBoundaryTests(unittest.TestCase):
    """B-1: the common safe-read boundary guards fingerprint AND loaders.

    Direct forbidden specs, parent-symlink traversals, and outside-repo
    ``..`` escapes are refused PRE-READ (read API uncalled) on both the
    fingerprint side (``_map_identity``) and the loader side
    (provisional + canonical ``load``), and ``run_selector`` fails closed
    to full L3. Temp-dir ``--map`` fixtures (no rom/.. segments, no
    symlink parents) still load: the authorized test-only boundary.
    """
    def spy_reads(self):
        reads = []
        real_read = Path.read_bytes

        def spy(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)
        return reads, real_read, spy

    def assert_both_loaders_refuse_without_read(self, spec, repo):
        reads, real_read, spy = self.spy_reads()
        orig = Path.read_bytes
        Path.read_bytes = spy
        try:
            sha, mid = SEL._map_identity(spec, repo)
        finally:
            Path.read_bytes = orig
        self.assertEqual((sha, mid), ("unknown", "unknown"))
        self.assertEqual(reads, [], f"sentinel was read: {reads}")
        reads2, _, spy2 = self.spy_reads()
        Path.read_bytes = spy2
        try:
            with self.assertRaises(SEL.MapError):
                SEL.ProvisionalJsonMapLoader().load(spec)
            with self.assertRaises(SEL.MapError):
                SEL.CanonicalJsonMapLoader().load(spec)
        finally:
            Path.read_bytes = orig
        self.assertEqual(reads2, [], f"sentinel was read: {reads2}")

    def test_direct_forbidden_spec_refused_pre_read_both_paths(self):
        repo = make_repo("f.s")
        sentinel = Path(repo) / "rom" / "sentinel.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"sentinel": true}\n')
        spec = "rom/sentinel.json"
        self.assert_both_loaders_refuse_without_read(spec, str(repo))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=spec,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "map-missing-or-inconsistent")

    def test_parent_symlink_traversal_refused_pre_read_both_paths(self):
        repo = make_repo("f.s")
        outside = Path(tempfile.mkdtemp(prefix="b1-pdir-"))
        sentinel = outside / "map.json"
        sentinel.write_text('{"sentinel": true}\n')
        link = Path(repo) / "linkdir"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        spec = str(link / "map.json")
        self.assert_both_loaders_refuse_without_read(spec, str(repo))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=spec,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "map-missing-or-inconsistent")

    def test_outside_repo_dotdot_refused_pre_read_both_paths(self):
        repo = make_repo("f.s")
        parent = Path(repo).parent
        sentinel = parent / f"b1-outside-{os.getpid()}.json"
        sentinel.write_text('{"sentinel": true}\n')
        try:
            spec = str(Path(repo) / ".." / sentinel.name)
            self.assertIn("..", spec)
            self.assert_both_loaders_refuse_without_read(spec, str(repo))
            selection, _ = SEL.run_selector(
                str(repo), "vbase", map_path=spec,
                loader=SEL.ProvisionalJsonMapLoader())
            assert_full_l3(self, selection, "map-missing-or-inconsistent")
        finally:
            try:
                sentinel.unlink()
            except OSError:
                pass

    def test_authorized_temp_map_still_loads(self):
        # The explicit --map temp-dir boundary (no rom/.., no symlinks)
        # passes the same strict checks on both paths.
        repo = make_repo("f.s")
        map_path = write_map(repo, provisional_map([l1_rule("src/**")]))
        sha, mid = SEL._map_identity(map_path, str(repo))
        self.assertNotEqual(sha, "unknown")
        self.assertTrue(mid.startswith("provisional:"))
        rules, _apis = SEL.ProvisionalJsonMapLoader().load(map_path)
        self.assertTrue(rules)


class Wave2B2ToolchainTests(unittest.TestCase):
    """B-2: declared revision + TOOLS_DIR existence is NOT execution identity.

    The actually-executed cc65 binaries are verified (bytes hashed,
    ``--version`` must report the pinned ``revision[:9]``); anything
    unconfirmable (same-path content change alters the identity, different
    checkout revision, empty dir, missing tools, pin-vs-entity mismatch)
    refuses reuse. No blanket env collection, no secrets.
    """
    def make_tools(self, revision, wrong_report=None):
        tools_root = Path(tempfile.mkdtemp(prefix="b2-tools-"))
        bindir = tools_root / f"cc65-{revision}" / "bin"
        bindir.mkdir(parents=True)
        report = wrong_report if wrong_report is not None else revision[:9]
        for binary in ("ca65", "ld65"):
            tool = bindir / binary
            tool.write_text(f"#!/bin/sh\necho '{binary} V2.19 - {report}'\n")
            tool.chmod(0o755)
        return tools_root

    def make_pinned_repo(self, revision):
        repo = make_repo("f.s")
        tools_dir = Path(repo) / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / "dependencies.json").write_text(json.dumps({
            "cc65": {"revision": revision}}))
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "pins")
        return repo

    def fingerprint_with_tools(self, repo, tools_root, **kwargs):
        with unittest.mock.patch.dict(os.environ, {"TOOLS_DIR": str(tools_root)},
                                      clear=False):
            changed = SEL.collect_changes(str(repo), "vbase")
            return SEL.evidence_fingerprint(str(repo), changed, **kwargs)

    def test_same_tools_dir_content_change_refuses_reuse(self):
        revision = "71746c829e77f74c2b601a7d16821170c2610df3"
        repo = self.make_pinned_repo(revision)
        tools_root = self.make_tools(revision)
        try:
            first = self.fingerprint_with_tools(str(repo), tools_root,
                                                 map_path=None, level="L1",
                                                 commands=[{"make": "check"}])
            self.assertNotEqual(first["toolchain"], "unknown")
            # Same path, same --version output, different bytes (comment).
            for binary in ("ca65", "ld65"):
                tool = tools_root / f"cc65-{revision}" / "bin" / binary
                with tool.open("a") as handle:
                    handle.write("# content change\n")
            second = self.fingerprint_with_tools(str(repo), tools_root,
                                                  map_path=None, level="L1",
                                                  commands=[{"make": "check"}])
            self.assertNotEqual(second["toolchain"], "unknown")
            self.assertNotEqual(first["toolchain"], second["toolchain"])
            # B-1 (review 6095863728): _cli_effective_env no longer scans
            # bin/* (zero enumeration/read), so the env identity is the
            # path string alone and stays put here -- the same-path content
            # change is caught by the toolchain verdict instead.
            self.assertEqual(first["env"], second["env"])
            self.assertFalse(SEL.evidence_reusable(first, second))
        finally:
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TOOLS_DIR", None)

    def test_different_checkout_revision_mismatch_refuses(self):
        revision = "71746c829e77f74c2b601a7d16821170c2610df3"
        repo = self.make_pinned_repo(revision)
        tools_root = self.make_tools(revision, wrong_report="deadbee")
        try:
            fingerprint = self.fingerprint_with_tools(
                str(repo), tools_root, map_path=None, level="L1",
                commands=[{"make": "check"}])
            self.assertEqual(fingerprint["toolchain"], "unknown")
            self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        finally:
            os.environ.pop("TOOLS_DIR", None)

    def test_empty_tools_dir_refuses(self):
        revision = "71746c829e77f74c2b601a7d16821170c2610df3"
        repo = self.make_pinned_repo(revision)
        empty = Path(tempfile.mkdtemp(prefix="b2-empty-"))
        try:
            fingerprint = self.fingerprint_with_tools(
                str(repo), empty, map_path=None, level="L1",
                commands=[{"make": "check"}])
            self.assertEqual(fingerprint["toolchain"], "unknown")
            self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        finally:
            os.environ.pop("TOOLS_DIR", None)

    def test_missing_tool_binaries_refuse(self):
        revision = "71746c829e77f74c2b601a7d16821170c2610df3"
        repo = self.make_pinned_repo(revision)
        tools_root = Path(tempfile.mkdtemp(prefix="b2-notools-"))
        (tools_root / f"cc65-{revision}" / "bin").mkdir(parents=True)
        try:
            fingerprint = self.fingerprint_with_tools(
                str(repo), tools_root, map_path=None, level="L1",
                commands=[{"make": "check"}])
            self.assertEqual(fingerprint["toolchain"], "unknown")
            self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        finally:
            os.environ.pop("TOOLS_DIR", None)

    def test_old_selector_results_never_reuse_against_new(self):
        # Selector pinning (SELECTOR_ID v3): a stored result from the
        # previous selector never reuses, even when every other field
        # matches. This is what retires pre-Wave2 pins-only evidence.
        stored = {
            "head": "a" * 40, "base": "b" * 40,
            "inputs_sha256": "c" * 64, "map_sha256": "d" * 64,
            "map_id": "test-staging-map-v0/v1@" + "e" * 40,
            "selector": "test_staging_select/l3full-no-barehex-v2",
            "api": "none", "level": "L1",
            "commands_sha256": "f" * 64, "python": "3.12.3",
            "platform": "linux",
            "toolchain": json.dumps({"pins": {"cc65": "x" * 40},
                                       "tools": {"ca65": "y" * 64,
                                                 "ld65": "z" * 64}}),
            "mode": "debug",
            "env": {"MODE": "debug", SEL.REQUIRE_CC65_ENV: "",
                    "TOOLS_DIR_sha256": "t" * 64},
        }
        current = dict(stored, selector=SEL.SELECTOR_ID)
        self.assertFalse(SEL.evidence_reusable(stored, current))
        self.assertFalse(SEL.evidence_reusable(current, stored))

    def test_missing_malformed_mandatory_elements_refuse(self):
        base = {"head": "a" * 40, "base": "b" * 40,
                "inputs_sha256": "c" * 64, "map_sha256": "d" * 64,
                "map_id": "test-staging-map-v0/v1@" + "e" * 40,
                "selector": SEL.SELECTOR_ID, "api": "none",
                "level": "L1", "commands_sha256": "f" * 64,
                "python": "3.12.3", "platform": "linux",
                "toolchain": json.dumps({"pins": {"cc65": "x" * 40},
                                           "tools": {"ca65": "y" * 64,
                                                     "ld65": "z" * 64}}),
                "mode": "debug",
                "env": {"MODE": "debug", SEL.REQUIRE_CC65_ENV: "",
                        "TOOLS_DIR_sha256": "t" * 64}}
        self.assertTrue(SEL.evidence_reusable(base, dict(base)))
        for key in ("toolchain", "mode", "env", "map_sha256",
                    "inputs_sha256"):
            mutated = dict(base)
            del mutated[key]
            self.assertFalse(SEL.evidence_reusable(base, mutated))
            self.assertFalse(SEL.evidence_reusable(mutated, base))
            mutated = dict(base, **{key: None})
            self.assertFalse(SEL.evidence_reusable(base, mutated))
            mutated = dict(base, **{key: ("" if key != "env" else {})})
            self.assertFalse(SEL.evidence_reusable(base, mutated))


class Wave2B3UnionTests(unittest.TestCase):
    """B-3: --api unions path-rule commands instead of overwriting them."""
    def test_path_rule_ab_with_api_b_keeps_a(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L1",
                             commands=[{"make": "dev-rom"},
                                       {"unittest": "a.py",
                                        "suite": "host"}],
                             reason="path A+B", api="SLOT_X",
                             literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot B", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        self.assertEqual(selection.level, "L1")
        kinds = [(cmd.get("make"), cmd.get("unittest"))
                 for cmd in selection.selected]
        self.assertIn(("dev-rom", None), kinds)
        self.assertIn((None, "a.py"), kinds)

    def test_differing_path_and_api_sets_union(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L1",
                             commands=[{"make": "check"}],
                             reason="path", api="SLOT_X", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        self.assertEqual(selection.level, "L1")
        kinds = [cmd.get("make") for cmd in selection.selected]
        self.assertIn("check", kinds)
        self.assertIn("dev-rom", kinds)

    def test_multi_change_same_api_unions_all(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase",
            committed=[("M", "a.s"), ("M", "b.s")])
        rules = [SEL.MapRule(pattern="a.s", level="L1",
                             commands=[{"make": "check"}],
                             reason="a", api="SLOT_X", literal=True),
                 SEL.MapRule(pattern="b.s", level="L1",
                             commands=[{"unittest": "b.py",
                                        "suite": "host"}],
                             reason="b", api="SLOT_X", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        self.assertEqual(selection.level, "L1")
        kinds = [cmd.get("make", cmd.get("unittest"))
                 for cmd in selection.selected]
        self.assertIn("check", kinds)
        self.assertIn("b.py", kinds)
        self.assertIn("dev-rom", kinds)

    def test_api_never_lowers_level_with_union(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L2",
                             commands=[{"make": "check"}],
                             reason="path L2", api="SLOT_X",
                             literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot L1", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        self.assertEqual(selection.level, "L2")
        kinds = [cmd.get("make") for cmd in selection.selected]
        self.assertIn("check", kinds)
        self.assertIn("dev-rom", kinds)

    def test_l3_safety_rule_always_kept_as_full_l3(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="f.s", level="L3",
                             commands=[{"make": "check"}],
                             reason="safety", api="", literal=True)]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot", "level": "L1"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="first-match")
        assert_full_l3(self, selection, "api-first-match-mismatch")

    def test_provisional_max_mode_unions(self):
        changed = SEL.ChangedInputs(
            head="h" * 40, base="vbase", committed=[("M", "f.s")])
        rules = [SEL.MapRule(pattern="*.s", level="L1",
                             commands=[{"make": "check"}],
                             reason="path", api="SLOT_X")]
        apis = {"SLOT_X": {"commands": [{"make": "dev-rom"}],
                             "reason": "slot"}}
        selection = SEL.select(changed, rules, apis, api="SLOT_X",
                               fingerprint={}, match_mode="max")
        self.assertEqual(selection.level, "L1")
        kinds = [cmd.get("make") for cmd in selection.selected]
        self.assertIn("check", kinds)
        self.assertIn("dev-rom", kinds)


class Wave2B4RepoConsistencyTests(unittest.TestCase):
    """B-4: diff/fingerprint/map/ledger/Makefile/pins bind to --repo."""
    SLOT_A = "API_ALPHA"
    SLOT_B = "API_BETA"

    def ledger_doc(self, slot63_name):
        slots = []
        for number in range(81):
            if number == 0x3F:
                slots.append({"id": number,
                              "candidate_name": slot63_name})
            else:
                slots.append({"id": number,
                              "candidate_name": f"DUMMY_{number:02X}"})
        return {"schema_version": 1, "slots": slots}

    def make_consistent_repo(self, slot_name, with_dev_rom=True):
        repo = make_repo("src/overrides/slot_3F.s")
        spec_dir = Path(repo) / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "api_slots.json").write_text(
            json.dumps(self.ledger_doc(slot_name)))
        make_targets = ["build", "test", "test-real-core", "check",
                        "verify-build-diff", "rom-provenance",
                        "dev-integration", "dev-release-gate-blocked"]
        if with_dev_rom:
            make_targets.append("dev-rom")
        (Path(repo) / "Makefile").write_text(
            "".join(f"{target}:\n\ttrue\n" for target in make_targets))
        tools_dir = Path(repo) / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        import shutil as _shutil
        _shutil.copyfile(str(ROOT / "tools" / "dependencies.json"),
                         str(tools_dir / "dependencies.json"))
        host_dir = Path(repo) / "tests" / "host"
        host_dir.mkdir(parents=True, exist_ok=True)
        for module in ("test_api_contracts.py", "test_api_table.py"):
            (host_dir / module).write_text('"""fixture host module."""\n')
        (tools_dir / "test_staging_map.json").write_text(
            json.dumps(canonical_doc()))
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "consistent baseline")
        baseline = git(repo, "rev-parse", "HEAD").strip()
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        return repo, baseline

    def cli(self, repo, baseline, *extra):
        cmd = [sys.executable,
               str(ROOT / "tools" / "test_staging_select.py"),
               "--repo", str(repo), "--base", baseline,
               "--json", *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout)

    def test_two_repos_no_cross_checkout_mixing(self):
        repo_a, base_a = self.make_consistent_repo(self.SLOT_A)
        repo_b, base_b = self.make_consistent_repo(self.SLOT_B)
        payload_a = self.cli(repo_a, base_a, "--api", self.SLOT_A)
        payload_b = self.cli(repo_b, base_b, "--api", self.SLOT_B)
        self.assertEqual(payload_a["level"], "L1")
        self.assertFalse(payload_a["fallback"])
        self.assertEqual(payload_b["level"], "L1")
        self.assertFalse(payload_b["fallback"])
        # Each repo resolves only its own ledger name; the other's name
        # is unknown there (no cross-checkout ledger mixing).
        cross_a = self.cli(repo_a, base_a, "--api", self.SLOT_B)
        self.assertEqual(cross_a["level"], "L3")
        cross_b = self.cli(repo_b, base_b, "--api", self.SLOT_A)
        self.assertEqual(cross_b["level"], "L3")
        # Selections carry each repo's own evidence (levels match, apis
        # differ, fingerprints differ by repo HEAD/ledger-bound map).
        self.assertNotEqual(payload_a["fingerprint"]["head"],
                            payload_b["fingerprint"]["head"])

    def test_makefile_bound_to_target_repo(self):
        repo_ok, base_ok = self.make_consistent_repo(self.SLOT_A,
                                                     with_dev_rom=True)
        repo_bare, base_bare = self.make_consistent_repo(self.SLOT_A,
                                                         with_dev_rom=False)
        payload_ok = self.cli(repo_ok, base_ok, "--api", self.SLOT_A)
        self.assertEqual(payload_ok["level"], "L1")
        payload_bare = self.cli(repo_bare, base_bare,
                                "--api", self.SLOT_A)
        # The bare repo's Makefile lacks dev-rom: the same map is
        # inconsistent there (fail-closed L3), proving the live Makefile
        # comes from --repo rather than a shared checkout.
        self.assertEqual(payload_bare["level"], "L3")
        self.assertTrue(payload_bare["fallback"])

    def test_unhandleable_different_root_refuses_fail_closed(self):
        repo = make_repo("src/a.s")
        write_file(repo, "src/a.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        # No ledger/Makefile/map in the target repo: the repo-bound reads
        # refuse explicitly (L3) instead of silently using ROOT's files.
        selection, _ = SEL.run_selector(str(repo), "vbase")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason,
                         "map-missing-or-inconsistent")


# ---------------------------------------------------------------------------
# Wave3 blockers (review 6095863728): B-1 unified tool boundary, B-2 exact
# untracked prefix, B-3 cross-repo map refusal, B-4 repo binding.
#
# All fixtures are synthetic temp dirs + sentinel bytes only (no ./rom,
# no real checkouts, no network). Read/exec/enumeration-uncalled proofs
# spy on Path.read_bytes, Path.iterdir, shutil.which, and subprocess.run.
# ---------------------------------------------------------------------------


class Wave3B1ToolBoundaryTests(unittest.TestCase):
    """B-1: one safe boundary BEFORE enumerate/stat/read/execute.

    _cli_effective_env no longer walks TOOLS_DIR (path-string identity
    only -- content binding lives in _declared_toolchain). Overrides and
    executed tools are vetted (forbidden rom/.. specs, in-repo locations,
    parent symlinks, non-regular files) BEFORE which/read/--version.
    Forbidden input: zero enumeration/read/execution, toolchain+env
    unknown, reuse refused.
    """
    REVISION = "71746c829e77f74c2b601a7d16821170c2610df3"

    def make_tools(self, revision, tools_root=None, report=None):
        tools_root = Path(
            tools_root or tempfile.mkdtemp(prefix="w3b1-tools-"))
        bindir = tools_root / f"cc65-{revision}" / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        reporter = report if report is not None else revision[:9]
        for binary in ("ca65", "ld65"):
            tool = bindir / binary
            tool.write_text(
                f"#!/bin/sh\necho '{binary} V2.19 - {reporter}'\n")
            tool.chmod(0o755)
        return tools_root

    def make_pinned_repo(self, revision):
        repo = make_repo("f.s")
        tools_dir = Path(repo) / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / "dependencies.json").write_text(json.dumps({
            "cc65": {"revision": revision}}))
        git(repo, "add", "-A")
        git(repo, "commit", "-qam", "pins")
        return repo

    def fingerprint_under_env(self, repo, extra):
        with unittest.mock.patch.dict(os.environ, extra, clear=False):
            changed = SEL.collect_changes(str(repo), "vbase")
            return SEL.evidence_fingerprint(
                str(repo), changed, map_path=None, level="L1",
                commands=[{"make": "check"}])

    def test_forbidden_tools_dir_zero_tool_read_exec(self):
        repo = self.make_pinned_repo(self.REVISION)
        base = Path(tempfile.mkdtemp(prefix="w3b1-rom-"))
        tools_root = base / "rom" / "tools"
        self.make_tools(self.REVISION, tools_root=tools_root)
        (tools_root / f"cc65-{self.REVISION}" / "bin" / "sentinel.bin"
         ).write_bytes(b"SENTINEL\n")
        reads, iters, runs = [], [], []
        real_read, real_iter = Path.read_bytes, Path.iterdir
        real_run = subprocess.run

        def spy_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        def spy_iter(self, *args, **kwargs):
            iters.append(str(self))
            return real_iter(self, *args, **kwargs)

        def spy_run(argv, *args, **kwargs):
            runs.append(list(argv) if isinstance(argv, (list, tuple))
                        else argv)
            return real_run(argv, *args, **kwargs)

        Path.read_bytes, Path.iterdir = spy_read, spy_iter
        try:
            with unittest.mock.patch.object(subprocess, "run", spy_run):
                fingerprint = self.fingerprint_under_env(
                    repo, {"TOOLS_DIR": str(tools_root)})
        finally:
            Path.read_bytes, Path.iterdir = real_read, real_iter
        self.assertEqual(fingerprint["toolchain"], "unknown")
        self.assertEqual(
            fingerprint["env"]["TOOLS_DIR_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        self.assertEqual(
            [path for path in reads if str(tools_root) in path], [])
        self.assertEqual(
            [path for path in iters
             if str(tools_root) in path or str(base) in path], [])
        self.assertEqual(
            [argv for argv in runs
             if any(str(tools_root) in str(part)
                    for part in (argv if isinstance(argv, list)
                                 else [argv]))], [])

    def test_ca65_override_forbidden_zero_read_exec(self):
        repo = self.make_pinned_repo(self.REVISION)
        good_root = self.make_tools(self.REVISION)
        evil_dir = Path(tempfile.mkdtemp(prefix="w3b1-evil-")) / "rom"
        evil_dir.mkdir()
        evil_tool = evil_dir / "evil-ca65"
        evil_tool.write_text(
            f"#!/bin/sh\necho 'ca65 V2.19 - {self.REVISION[:9]}'\n")
        evil_tool.chmod(0o755)
        reads, runs, whiches = [], [], []
        real_read, real_run = Path.read_bytes, subprocess.run
        import shutil as _shutil
        real_which = _shutil.which

        def spy_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        def spy_run(argv, *args, **kwargs):
            runs.append(list(argv) if isinstance(argv, (list, tuple))
                        else argv)
            return real_run(argv, *args, **kwargs)

        def spy_which(requested, *args, **kwargs):
            whiches.append(requested)
            return real_which(requested, *args, **kwargs)

        Path.read_bytes = spy_read
        try:
            with unittest.mock.patch.object(subprocess, "run", spy_run), \
                    unittest.mock.patch("shutil.which", spy_which):
                fingerprint = self.fingerprint_under_env(repo, {
                    "TOOLS_DIR": str(good_root),
                    "CA65": str(evil_tool)})
        finally:
            Path.read_bytes = real_read
        self.assertEqual(fingerprint["toolchain"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        self.assertEqual(
            [path for path in reads if str(evil_tool) in path], [])
        self.assertEqual(
            [argv for argv in runs
             if any(str(evil_tool) in str(part)
                    for part in (argv if isinstance(argv, list)
                                 else [argv]))], [])
        self.assertNotIn(str(evil_tool), whiches)

    def test_tools_dir_inside_repo_refused_with_unknown_env(self):
        repo = self.make_pinned_repo(self.REVISION)
        inner = Path(repo) / "toolcache"
        self.make_tools(self.REVISION, tools_root=inner)
        # Commit the tool dir as baseline: otherwise its files are
        # untracked inputs and the fingerprint legitimately hashes them
        # as inputs (input hashing is not toolchain reading). Committed
        # tool bytes flow through `git show` (subprocess), so a zero
        # Path.read_bytes result proves the toolchain/env side read
        # nothing.
        git(repo, "add", "-A")
        git(repo, "commit", "-qam", "toolcache baseline")
        reads = []
        real_read = Path.read_bytes

        def spy_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = spy_read
        try:
            fingerprint = self.fingerprint_under_env(
                repo, {"TOOLS_DIR": str(inner)})
        finally:
            Path.read_bytes = real_read
        self.assertEqual(fingerprint["toolchain"], "unknown")
        self.assertEqual(
            fingerprint["env"]["TOOLS_DIR_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(fingerprint, fingerprint))
        self.assertEqual(
            [path for path in reads if str(inner) in path], [])

    def test_verify_pin_vets_before_execute(self):
        evil_dir = Path(tempfile.mkdtemp(prefix="w3b1-exec-")) / "rom"
        evil_dir.mkdir()
        evil = evil_dir / "evil-tool"
        evil.write_text("#!/bin/sh\necho 'ca65 V2.19 - deadbee'\n")
        evil.chmod(0o755)
        runs = []
        real_run = subprocess.run

        def spy_run(argv, *args, **kwargs):
            runs.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with unittest.mock.patch.object(subprocess, "run", spy_run):
            self.assertFalse(SEL._verify_tool_reports_pin(
                str(evil), "deadbee"))
        self.assertEqual(runs, [])

    def test_hash_tool_file_vets_before_read(self):
        evil_dir = Path(tempfile.mkdtemp(prefix="w3b1-read-")) / "rom"
        evil_dir.mkdir()
        evil = evil_dir / "sentinel.bin"
        evil.write_bytes(b"SENTINEL\n")
        reads = []
        real_read = Path.read_bytes

        def spy_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = spy_read
        try:
            self.assertIsNone(SEL._hash_tool_file(str(evil)))
        finally:
            Path.read_bytes = real_read
        self.assertEqual(reads, [])

    def test_effective_env_binds_path_without_scanning(self):
        repo = self.make_pinned_repo(self.REVISION)
        good_root = self.make_tools(self.REVISION)
        iters, reads = [], []
        real_iter, real_read = Path.iterdir, Path.read_bytes

        def spy_iter(self, *args, **kwargs):
            iters.append(str(self))
            return real_iter(self, *args, **kwargs)

        def spy_read(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.iterdir, Path.read_bytes = spy_iter, spy_read
        try:
            with unittest.mock.patch.dict(
                    os.environ, {"TOOLS_DIR": str(good_root)},
                    clear=False):
                env = SEL._cli_effective_env("debug", None)
        finally:
            Path.iterdir, Path.read_bytes = real_iter, real_read
        self.assertEqual(iters, [])
        self.assertEqual(reads, [])
        import hashlib as _hashlib
        self.assertEqual(env["TOOLS_DIR_sha256"],
                         _hashlib.sha256(str(good_root).encode()).hexdigest())


class Wave3B2UntrackedPrefixTests(unittest.TestCase):
    """B-2: only the fixed porcelain -z prefix is stripped, never more.

    ``chunk[2:].strip()`` destroyed trailing-space (and leading-space)
    filenames; the fix strips exactly the one separator space. Proven on
    a REAL synthetic git repo (tracked ``sample`` + untracked ``sample ``:
    untracked content change alters the fingerprint and old evidence never
    reuses). Whitespace/tab/newline/CR names round-trip; malformed and
    undecodable status output fail closed (GitError -> L3).
    """
    def test_trailing_space_untracked_changes_fingerprint(self):
        repo = make_repo("sample")
        write_file(repo, "sample ", "v1\n")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.untracked, ["sample "])
        first = SEL.evidence_fingerprint(
            str(repo), changed, map_path=None, level="L3",
            commands=[{"make": "check"}])
        write_file(repo, "sample ", "v2-different-bytes\n")
        changed2 = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed2.untracked, ["sample "])
        second = SEL.evidence_fingerprint(
            str(repo), changed2, map_path=None, level="L3",
            commands=[{"make": "check"}])
        self.assertNotEqual(first["inputs_sha256"], "unknown")
        self.assertNotEqual(second["inputs_sha256"], "unknown")
        self.assertNotEqual(first["inputs_sha256"],
                            second["inputs_sha256"])
        self.assertFalse(SEL.evidence_reusable(first, second))
        # Selection contract kept: untracked inputs still force full L3.
        covering = write_map(repo, provisional_map([l1_rule("**")]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=covering,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "untracked-inputs")

    def test_whitespace_tab_newline_names_roundtrip(self):
        repo = make_repo("base.txt")
        names = ["sp ace.txt", "tab\tname.txt", "new\nline.txt",
                 " lead.txt", "trail.txt ", "multi  space.txt"]
        for name in names:
            write_file(repo, name, "x\n")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(sorted(changed.untracked), sorted(names))

    def test_cr_filename_survives_without_normalization(self):
        repo = make_repo("base.txt")
        write_file(repo, "a\rb", "x\n")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.untracked, ["a\rb"])

    def test_malformed_untracked_entry_fails_closed(self):
        repo = make_repo("base.txt")
        for bad in ("??", "?? ", "??\tno-separator"):
            with self.subTest(bad=bad):
                def runner(repo_path, *args, _bad=bad):
                    if args[:1] == ("status",):
                        return _bad + "\0"
                    return SEL._real_runner(repo_path, *args)
                with self.assertRaises(SEL.GitError):
                    SEL.collect_changes(str(repo), "vbase", runner=runner)
                selection, _ = SEL.run_selector(
                    str(repo), "vbase", runner=runner)
                self.assertEqual(selection.level, "L3")
                self.assertTrue(selection.fallback)
                self.assertEqual(selection.fallback_reason, "diff")

    def test_status_decode_failure_fails_closed(self):
        repo = make_repo("base.txt")

        def runner(repo_path, *args):
            if args[:1] == ("status",):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1,
                                         "bad byte")
            return SEL._real_runner(repo_path, *args)

        with self.assertRaises(SEL.GitError):
            SEL.collect_changes(str(repo), "vbase", runner=runner)
        selection, _ = SEL.run_selector(str(repo), "vbase", runner=runner)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.fallback_reason, "diff")


class Wave3B3CrossRepoTests(Wave2B4RepoConsistencyTests):
    """B-3: an absolute --map from another checkout never mixes.

    The normal CLI binds target repo + map + ledger + Makefile +
    dependencies consistently: a cross-checkout --map is refused
    fail-closed (full L3, reuse refused), while the home repo's own map
    still selects L1. base_commit is a content fingerprint, never
    repo-identity proof. Repo-bound malformed/provisional maps still fail
    closed with their own reason (intent preserved).
    """
    def test_cross_repo_absolute_map_refused_via_normal_cli(self):
        repo_a, base_a = self.make_consistent_repo(self.SLOT_A)
        repo_b, _base_b = self.make_consistent_repo(self.SLOT_B)
        # Repo B gets a byte-distinct map (different ledger/map per the
        # binding contract); repo B's own git state is irrelevant here --
        # only the map file bytes at the absolute --map path matter.
        map_b_path = Path(repo_b) / "tools" / "test_staging_map.json"
        doc_b = json.loads(map_b_path.read_text())
        doc_b["entries"][0]["reason"] = (
            "fixture DEV-implemented slot (repo B map variant)")
        map_b_path.write_text(json.dumps(doc_b))
        map_b = str(map_b_path)
        home = self.cli(repo_a, base_a, "--api", self.SLOT_A)
        self.assertEqual(home["level"], "L1")
        self.assertFalse(home["fallback"])
        cross = self.cli(repo_a, base_a, "--map", map_b,
                         "--api", self.SLOT_A)
        self.assertEqual(cross["level"], "L3")
        self.assertTrue(cross["fallback"])
        self.assertEqual(cross["fallback_reason"],
                         "map-missing-or-inconsistent")
        self.assertTrue(
            any("cross-repo" in reason for reason in cross["reasons"]))
        self.assertEqual(
            [tuple(cmd["argv"]) for cmd in cross["selected"]],
            [tuple(argv) for argv in full_l3_argvs()])
        self.assertNotEqual(home["fingerprint"]["map_sha256"],
                            cross["fingerprint"]["map_sha256"])
        self.assertFalse(SEL.evidence_reusable(home["fingerprint"],
                                               cross["fingerprint"]))

    def test_repo_bound_malformed_map_still_full_l3(self):
        repo, base = self.make_consistent_repo(self.SLOT_A)
        malformed = canonical_doc()
        malformed["required_checks"] = [{"not": "a-string"}]
        committed = Path(repo) / "tools" / "malformed.json"
        committed.write_text(json.dumps(malformed))
        git(repo, "add", "tools/malformed.json")
        git(repo, "commit", "-qam", "malformed map baseline")
        baseline = git(repo, "rev-parse", "HEAD").strip()
        moved = self.cli(repo, baseline, "--map", "tools/malformed.json")
        self.assertEqual(moved["level"], "L3")
        self.assertTrue(moved["fallback"])
        self.assertEqual(moved["fallback_reason"],
                         "map-missing-or-inconsistent")
        self.assertEqual(
            [tuple(cmd["argv"]) for cmd in moved["selected"]],
            [tuple(argv) for argv in full_l3_argvs()])

    def test_repo_bound_provisional_via_normal_cli_full_l3(self):
        repo, base = self.make_consistent_repo(self.SLOT_A)
        provisional = Path(repo) / "tools" / "provisional.json"
        provisional.write_text(json.dumps(
            provisional_map([l1_rule("src/overrides/*.s")])))
        git(repo, "add", "tools/provisional.json")
        git(repo, "commit", "-qam", "provisional map baseline")
        baseline = git(repo, "rev-parse", "HEAD").strip()
        moved = self.cli(repo, baseline, "--map", "tools/provisional.json")
        self.assertEqual(moved["level"], "L3")
        self.assertTrue(moved["fallback"])
        self.assertEqual(moved["fallback_reason"],
                         "map-missing-or-inconsistent")


class Wave3B4BindingTests(unittest.TestCase):
    """B-4: repo binding is consistent and unhandleable ROOT refuses.

    A repo-bound canonical loader rejects a map from outside its bound
    repo BEFORE reading (zero read-API calls); the explicit unbound
    loader keeps the isolated synthetic-test boundary; Makefile/ledger
    reads stay bound to the target repo; no silent ROOT default map.
    """
    def test_bound_loader_rejects_outside_map_before_read(self):
        repo_a = make_repo("f.s")
        outside = Path(tempfile.mkdtemp(prefix="w3b4-outside-"))
        sentinel = outside / "other-map.json"
        sentinel.write_text(json.dumps(canonical_doc()))
        reads = []
        real_read = Path.read_bytes

        def spy(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = spy
        try:
            with self.assertRaises(SEL.MapError):
                SEL.CanonicalJsonMapLoader(repo=str(repo_a)).load(
                    str(sentinel))
        finally:
            Path.read_bytes = real_read
        self.assertEqual(reads, [])
        selection, _ = SEL.run_selector(str(repo_a), "vbase",
                                        map_path=str(sentinel))
        assert_full_l3(self, selection, "map-missing-or-inconsistent")
        self.assertTrue(
            any("cross-repo" in reason for reason in selection.reasons))

    def test_unbound_loader_keeps_isolated_synthetic_boundary(self):
        # Explicit synthetic-test lane unchanged: an unbound loader still
        # loads an isolated temp fixture (ledger/Makefile from the legacy
        # ROOT boundary); only repo-bound loads enforce containment.
        map_path = write_map(None, canonical_doc())
        rules, _apis = SEL.CanonicalJsonMapLoader().load(map_path)
        self.assertTrue(rules)

    def test_makefile_and_ledger_bound_to_target_repo_direct(self):
        repo = make_repo("f.s")
        with self.assertRaises(SEL.MapError):
            SEL._makefile_targets(str(repo))
        with self.assertRaises(SEL.MapError):
            SEL.CanonicalJsonMapLoader()._load_ledger_names(str(repo))

    def test_no_silent_root_default_map(self):
        # B-4 removed the ROOT-anchored DEFAULT_CANONICAL_MAP: the normal
        # CLI default is always <repo>/tools/test_staging_map.json, never
        # a silent fallback to this checkout's files.
        self.assertFalse(hasattr(SEL, "DEFAULT_CANONICAL_MAP"))


# ---------------------------------------------------------------------------
# Review 6096132688 (PR #857 Wave1): B-1 git-env binding, B-2 map pre-read.
#
# All fixtures are synthetic temp dirs + sentinel bytes only (no ./rom,
# no real checkouts, no network). Read-uncalled proofs spy on
# Path.read_bytes; env proofs spy on the env kwarg of subprocess.run.
# ---------------------------------------------------------------------------


class Review6096132688B1GitEnvTests(unittest.TestCase):
    """B-1: a hostile GIT_* env can never rebind any git subprocess path.

    _run_git (used by _real_runner AND the ls-tree/ls-files/show regular-
    file checks AND fingerprint hashing) strips every repo-binding variable
    and binds with explicit ``git -C <repo>`` + cwd, so HEAD/BASE/
    merge-base, all diffs, untracked listing, blob/index fetches, and the
    explicit --fetch update target stay on the target repo. Proven on two
    synthetic repos with the target call run under the OTHER repo's
    GIT_DIR/WORK_TREE (plus object-db redirects at the other repo).
    The BINDING_VARS literal below intentionally duplicates
    SEL._GIT_REPO_BINDING_VARS (it must NOT be derived from it): deriving
    the expectation from the implementation would make a dropped strip
    vacuous GREEN. If the implementation drops one variable, the
    per-variable spy assertion below flips RED.
    """
    BINDING_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE",
                    "GIT_INDEX_FILE", "GIT_INDEX_VERSION",
                    "GIT_COMMON_DIR", "GIT_NAMESPACE",
                    "GIT_CEILING_DIRECTORIES",
                    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_PREFIX",
                    "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_QUARANTINE_PATH", "GIT_GRAFT_FILE",
                    "GIT_SHALLOW_FILE", "GIT_NO_REPLACE_OBJECTS",
                    "GIT_REPLACE_REF_BASE", "GIT_CONFIG",
                    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")

    def make_pair(self):
        repo_a = make_repo("only-in-A.s")
        repo_b = make_repo("only-in-B.s")
        return repo_a, repo_b

    def hostile_env(self, other):
        other = Path(other)
        return {"GIT_DIR": str(other / ".git"),
                "GIT_WORK_TREE": str(other),
                "GIT_OBJECT_DIRECTORY": str(other / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(other / ".git" / "objects"),
                "GIT_QUARANTINE_PATH": str(other / ".git" / "objects"),
                "GIT_INDEX_FILE": str(other / ".git" / "index")}

    def test_hostile_git_dir_cannot_rebind_head(self):
        repo_a, repo_b = self.make_pair()
        head_a = git(repo_a, "rev-parse", "HEAD").strip()
        head_b = git(repo_b, "rev-parse", "HEAD").strip()
        self.assertNotEqual(head_a, head_b)
        with unittest.mock.patch.dict(os.environ,
                                      self.hostile_env(repo_b)):
            self.assertEqual(SEL._real_runner(str(repo_a), "rev-parse",
                                              "HEAD").strip(), head_a)

    def test_hostile_env_cannot_mix_diffs_or_drop_changes(self):
        repo_a, repo_b = self.make_pair()
        write_file(repo_a, "only-in-A.s", "CHANGED-A\n")
        git(repo_a, "commit", "-qam", "change A")
        with unittest.mock.patch.dict(os.environ,
                                      self.hostile_env(repo_b)):
            changed = SEL.collect_changes(str(repo_a), "vbase")
        # A's change survives; B's content never mixes in.
        self.assertEqual(changed.committed, [("M", "only-in-A.s")])
        self.assertEqual(changed.head,
                         git(repo_a, "rev-parse", "HEAD").strip())
        self.assertNotEqual(
            changed.head, git(repo_b, "rev-parse", "HEAD").strip())

    def test_every_git_call_uses_sanitized_env(self):
        repo_a, repo_b = self.make_pair()
        write_file(repo_a, "only-in-A.s", "committed\n")
        git(repo_a, "commit", "-qam", "change")
        write_file(repo_a, "staged.s", "staged\n")
        git(repo_a, "add", "staged.s")
        write_file(repo_a, "untracked.s", "untracked\n")
        seen = []
        real_run = subprocess.run

        def spy_run(argv, *args, **kwargs):
            if (isinstance(argv, list) and len(argv) > 2
                    and argv[0] == "git" and argv[1] == "-C"):
                env = kwargs.get("env", {})
                seen.append({var: env.get(var)
                             for var in self.BINDING_VARS})
            return real_run(argv, *args, **kwargs)

        with unittest.mock.patch.dict(os.environ,
                                      self.hostile_env(repo_b)):
            with unittest.mock.patch.object(subprocess, "run", spy_run):
                changed = SEL.collect_changes(str(repo_a), "vbase")
                SEL.evidence_fingerprint(
                    str(repo_a), changed, map_path=None, level="L3",
                    commands=[{"make": "check"}])
                SEL._git_blob_is_regular(str(repo_a), "HEAD",
                                         "only-in-A.s")
                SEL._git_staged_is_regular(str(repo_a), "staged.s")
        # Every git subprocess path ran (diffs, listing, blob/index
        # checks, blob fetches) and none carried a binding variable.
        self.assertGreaterEqual(len(seen), 8)
        for leaked in seen:
            self.assertEqual(
                leaked, {var: None for var in self.BINDING_VARS})
        self.assertEqual(changed.committed, [("M", "only-in-A.s")])

    def test_fetch_binds_to_target_origin_under_hostile_env(self):
        remote = Path(tempfile.mkdtemp(prefix="r6096132688-")) / "r.git"
        proc = subprocess.run(["git", "init", "-q", "--bare",
                               str(remote)], capture_output=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        repo_a, repo_b = self.make_pair()
        git(repo_a, "remote", "add", "origin", str(remote))
        git(repo_a, "push", "-q", "origin", "HEAD:refs/heads/main")
        # Repo B has no origin at all: pre-fix binding would fetch B
        # (failure / wrong target); post-fix binding fetches A's origin.
        with unittest.mock.patch.dict(os.environ,
                                      self.hostile_env(repo_b)):
            SEL.attempt_fetch(str(repo_a), "main")
        fetched = git(repo_a, "rev-parse", "FETCH_HEAD").strip()
        pushed = git(repo_a, "rev-parse", "HEAD").strip()
        self.assertTrue(fetched.startswith(pushed))


class Review6096255224ObjectDbIsolationTests(unittest.TestCase):
    """Object-database isolation: GIT_OBJECT_DIRECTORY and friends inherit.

    Pre-fix, _GIT_REPO_BINDING_VARS covered only 8 worktree/discovery
    vars, so `git -C A fetch origin HEAD` with a hostile
    GIT_OBJECT_DIRECTORY stored the fetched objects in repo B (B cat-file
    resolved the foreign commit; A dangled), and foreign alternates/
    configs redirected reads. Every git subprocess goes through _run_git
    (the single call site), so extending the strip list fixes all paths:
    worktree/gitdir/index, object/alternate/quarantine dirs, graft/
    shallow/replace overlays, and config-file/-c redirects. Transport,
    auth, and identity GIT_* must keep working (KEEP).

    EXPECTED_REMOVE below intentionally duplicates
    SEL._GIT_REPO_BINDING_VARS instead of deriving from it: dropping any
    single isolated variable from the implementation must flip its test
    RED (no vacuous GREEN). All fixtures are synthetic temp dirs +
    sentinel bytes (no ./rom, no network, no real checkouts).
    """
    EXPECTED_REMOVE = ("GIT_DIR", "GIT_WORK_TREE",
                       "GIT_IMPLICIT_WORK_TREE", "GIT_INDEX_FILE",
                       "GIT_INDEX_VERSION", "GIT_COMMON_DIR",
                       "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
                       "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_PREFIX",
                       "GIT_OBJECT_DIRECTORY",
                       "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                       "GIT_QUARANTINE_PATH", "GIT_GRAFT_FILE",
                       "GIT_SHALLOW_FILE", "GIT_NO_REPLACE_OBJECTS",
                       "GIT_REPLACE_REF_BASE", "GIT_CONFIG",
                       "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                       "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
    EXPECTED_KEEP = ("GIT_SSH_COMMAND", "GIT_ASKPASS",
                     "GIT_ALLOW_PROTOCOL", "GIT_AUTHOR_NAME",
                     "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                     "GIT_CONFIG_NOSYSTEM", "GIT_TRACE",
                     "GIT_PAGER", "GIT_LITERAL_PATHSPECS",
                     "GIT_OPTIONAL_LOCKS", "GIT_EXTERNAL_DIFF")

    def hostile_value(self, var, tmp):
        if var in ("GIT_OBJECT_DIRECTORY",
                   "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                   "GIT_QUARANTINE_PATH"):
            return str(tmp / "objects")
        if var in ("GIT_DIR",):
            return str(tmp / ".git")
        if var in ("GIT_WORK_TREE",):
            return str(tmp)
        if var in ("GIT_INDEX_FILE", "GIT_GRAFT_FILE",
                   "GIT_SHALLOW_FILE", "GIT_CONFIG",
                   "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            target = tmp / (var.lower().replace("git_", "") + ".sentinel")
            target.write_text("hostile\n")
            return str(target)
        return "hostile-sentinel"

    def clean_git(self, repo, *args):
        proc = subprocess.run(["git", *args], cwd=str(repo),
                              capture_output=True, text=True, timeout=120,
                              env=SEL._git_env_for_repo())
        if proc.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed: {proc.stderr[-1000:]}")
        return proc.stdout

    def snapshot_foreign_repo(self, repo):
        repo = Path(repo)
        objects = sorted(
            str(path.relative_to(repo))
            for path in (repo / ".git" / "objects").rglob("*")
            if path.is_file())
        refs = self.clean_git(repo, "for-each-ref")
        head = self.clean_git(repo, "rev-parse", "HEAD").strip()
        index = (repo / ".git" / "index").read_bytes()
        config = (repo / ".git" / "config").read_bytes()
        return {"objects": objects, "refs": refs, "head": head,
                "index": index, "config": config}

    def test_strip_table_covers_every_binding_var(self):
        # Data-driven over the independent EXPECTED_REMOVE literal: if
        # the implementation stops stripping exactly one variable, that
        # subTest flips RED.
        tmp = Path(tempfile.mkdtemp(prefix="r6096255224-strip-"))
        (tmp / "objects").mkdir(exist_ok=True)
        # The implementation must cover every expected variable; the
        # per-variable subTests below then prove each one is stripped.
        missing = [var for var in self.EXPECTED_REMOVE
                   if var not in SEL._GIT_REPO_BINDING_VARS]
        self.assertEqual(missing, [])
        for var in self.EXPECTED_REMOVE:
            with self.subTest(var=var):
                with unittest.mock.patch.dict(
                        os.environ, {var: self.hostile_value(var, tmp)}):
                    self.assertNotIn(var, SEL._git_env_for_repo())

    def test_config_key_value_prefixes_stripped(self):
        tmp = Path(tempfile.mkdtemp(prefix="r6096255224-kv-"))
        hostile = {"GIT_CONFIG_COUNT": "2",
                   "GIT_CONFIG_KEY_0": "url.dead.insteadOf",
                   "GIT_CONFIG_VALUE_0": "x",
                   "GIT_CONFIG_KEY_7": "url.dead.insteadOf",
                   "GIT_CONFIG_VALUE_7": "y"}
        with unittest.mock.patch.dict(os.environ, hostile):
            env = SEL._git_env_for_repo()
        for var in hostile:
            with self.subTest(var=var):
                self.assertNotIn(var, env)

    def test_transport_auth_identity_vars_pass_through(self):
        # Stripping these would gratuitously break normal settings: the
        # fetch URL still comes from the bound repo's own origin, so
        # auth/communication overrides carry no repo identity.
        keep = {var: f"keep-{var}" for var in self.EXPECTED_KEEP}
        with unittest.mock.patch.dict(os.environ, keep):
            env = SEL._git_env_for_repo()
        for var in self.EXPECTED_KEEP:
            with self.subTest(var=var):
                self.assertEqual(env.get(var), f"keep-{var}")

    def make_remote_pair(self):
        base = Path(tempfile.mkdtemp(prefix="r6096255224-"))
        remote = base / "r.git"
        proc = subprocess.run(["git", "init", "-q", "--bare",
                               str(remote)], capture_output=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        writer = base / "writer"
        git(writer.parent, "clone", "-q", str(remote), str(writer))
        git(writer, "config", "user.email", "test@example.invalid")
        git(writer, "config", "user.name", "staging-test")
        (writer / "base.txt").write_text("base\n")
        git(writer, "add", "-A")
        git(writer, "commit", "-qm", "c1")
        git(writer, "push", "-q", "origin", "HEAD:refs/heads/main")
        git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        repo_a = base / "A"
        git(base, "clone", "-q", str(remote), str(repo_a))
        repo_b = make_repo("only-in-B.s")
        return remote, writer, repo_a, repo_b

    def test_fetch_with_hostile_object_db_stays_in_target_repo(self):
        remote, writer, repo_a, repo_b = self.make_remote_pair()
        (writer / "payload2.txt").write_text("sentinel-payload-2\n")
        git(writer, "add", "-A")
        git(writer, "commit", "-qm", "c2")
        git(writer, "push", "-q", "origin", "HEAD:refs/heads/main")
        sha2 = git(writer, "rev-parse", "HEAD").strip()
        b_before = self.snapshot_foreign_repo(repo_b)
        hostile = {
            "GIT_OBJECT_DIRECTORY":
                str(repo_b / ".git" / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES":
                str(repo_b / ".git" / "objects"),
            "GIT_QUARANTINE_PATH":
                str(repo_b / ".git" / "objects"),
            "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
            "GIT_DIR": str(repo_b / ".git"),
            "GIT_WORK_TREE": str(repo_b),
        }
        with unittest.mock.patch.dict(os.environ, hostile):
            # Explicit --fetch through the tool's own runner, then the
            # full read-path suite, all under hostile env.
            SEL.attempt_fetch(str(repo_a), "main")
            changed = SEL.collect_changes(str(repo_a), "main")
            blob_ok = SEL._git_blob_is_regular(str(repo_a), "HEAD",
                                               "base.txt")
        b_after = self.snapshot_foreign_repo(repo_b)
        # B objects/refs/index/config byte-identical: nothing leaked.
        self.assertEqual(b_after, b_before)
        # The fetch landed in A (explicit --fetch bound to target repo).
        fetched = self.clean_git(repo_a, "rev-parse",
                                 "FETCH_HEAD").strip()
        self.assertEqual(fetched, sha2)
        proc_type = subprocess.run(
            ["git", "-C", str(repo_a), "cat-file", "-t", sha2],
            capture_output=True, text=True, timeout=120,
            env=SEL._git_env_for_repo())
        self.assertEqual((proc_type.returncode, proc_type.stdout.strip()),
                         (0, "commit"))
        proc_b = subprocess.run(
            ["git", "-C", str(repo_b), "cat-file", "-t", sha2],
            capture_output=True, text=True, timeout=120,
            env=SEL._git_env_for_repo())
        self.assertNotEqual(proc_b.returncode, 0)
        self.assertTrue(blob_ok)
        self.assertEqual(changed.head,
                         git(repo_a, "rev-parse", "HEAD").strip())

    def test_alternate_object_dirs_do_not_leak_foreign_reads(self):
        repo_a = make_repo("only-in-A.s")
        repo_b = make_repo("only-in-B.s")
        sha_b = git(repo_b, "rev-parse", "HEAD").strip()
        hostile = {"GIT_ALTERNATE_OBJECT_DIRECTORIES":
                   str(repo_b / ".git" / "objects")}
        with unittest.mock.patch.dict(os.environ, hostile):
            # A's reads must not resolve B's objects via the alternate.
            self.assertFalse(SEL._git_blob_is_regular(
                str(repo_a), sha_b, "only-in-B.s"))
            with self.assertRaises(SEL.GitError):
                SEL._real_runner(str(repo_a), "cat-file", "-t", sha_b)
        # Control: the clean env agrees (A genuinely lacks B's commit,
        # so the hostile-vs-clean equality proves no leak either way).
        self.assertFalse(SEL._git_blob_is_regular(
            str(repo_a), sha_b, "only-in-B.s"))
        # Positive control: B resolves its own commit through the same
        # sanitized boundary.
        with unittest.mock.patch.dict(os.environ, hostile):
            self.assertEqual(
                SEL._real_runner(str(repo_b), "cat-file", "-t",
                                 sha_b).strip(), "commit")

    def test_config_redirect_is_stripped(self):
        remote, _writer, repo_a, _repo_b = self.make_remote_pair()
        pushed = git(_writer, "rev-parse", "HEAD").strip()
        foreign = Path(tempfile.mkdtemp(prefix="r6096255224-cfg-"))
        foreign_cfg = foreign / "foreign.gitconfig"
        # A foreign url.insteadOf would rewrite A's origin to a dead URL:
        # unstripped, the fetch fails; stripped, it hits the real origin.
        foreign_cfg.write_text(
            f'[url "file:///nonexistent-dead-6096255224/"]\n'
            f"\tinsteadOf = {remote}\n")
        hostile = {"GIT_CONFIG": str(foreign_cfg),
                   "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0":
                       "url.file:///nonexistent-dead-6096255224/.insteadOf",
                   "GIT_CONFIG_VALUE_0": str(remote)}
        with unittest.mock.patch.dict(os.environ, hostile):
            SEL.attempt_fetch(str(repo_a), "main")
        fetched = git(repo_a, "rev-parse", "FETCH_HEAD").strip()
        self.assertEqual(fetched, pushed)


class Review6096132688B2MapPrereadTests(Wave2B4RepoConsistencyTests):
    """B-2: the normal CLI path refuses a foreign map BEFORE any read.

    _normal_cli_map_target unifies containment/lexical/symlink refusal
    ahead of fingerprint generation on every normal-CLI map input (the
    default map, a relative --map, an absolute --map, and the GitError-
    path raw --map). A refused map fingerprints as unknown (reuse
    refused) with full L3 and no traceback; read spies prove zero bytes
    of the foreign map are opened on any path (initial fingerprint,
    loader, finalise re-generation, GitError path). The explicit
    synthetic-loader lane (temp-only fixtures) is unchanged.
    """
    def test_cross_repo_map_zero_reads_all_normal_paths(self):
        repo_a, base_a = self.make_consistent_repo(self.SLOT_A)
        repo_b, _base_b = self.make_consistent_repo(self.SLOT_B)
        map_b = str(Path(repo_b) / "tools" / "test_staging_map.json")
        reads = []
        real_read = Path.read_bytes

        def spy(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = spy
        try:
            selection, _ = SEL.run_selector(str(repo_a), base_a,
                                            map_path=map_b)
        finally:
            Path.read_bytes = real_read
        # Full L3, cross-repo reason, zero foreign bytes on EVERY read
        # path (fingerprint + loader + finalise re-generation combined).
        assert_full_l3(self, selection, "map-missing-or-inconsistent")
        self.assertTrue(
            any("cross-repo" in reason for reason in selection.reasons))
        self.assertEqual([path for path in reads if path == map_b], [])
        self.assertEqual(selection.fingerprint["map_sha256"], "unknown")
        self.assertEqual(selection.fingerprint["map_id"], "unknown")
        self.assertFalse(SEL.evidence_reusable(selection.fingerprint,
                                               selection.fingerprint))
        # GitError path: the raw foreign --map is never opened either.
        reads_git = []
        Path.read_bytes = spy_git = (
            lambda self, *args, **kwargs:
            (reads_git.append(str(self)),
             real_read(self, *args, **kwargs))[1])
        try:
            selection_git, _ = SEL.run_selector(
                str(repo_a), "refs/heads/does-not-exist-xyz",
                map_path=map_b)
        finally:
            Path.read_bytes = real_read
        self.assertEqual(selection_git.level, "L3")
        self.assertTrue(selection_git.fallback)
        self.assertEqual(
            [path for path in reads_git if path == map_b], [])
        self.assertEqual(selection_git.fingerprint["map_sha256"],
                         "unknown")
        # Home-repo E2E still selects L1 (no over-refusal).
        home, _ = SEL.run_selector(str(repo_a), base_a,
                                   map_path=str(
                                       Path(repo_a) / "tools" /
                                       "test_staging_map.json"),
                                   api=self.SLOT_A)
        self.assertEqual(home.level, "L1")
        self.assertFalse(home.fallback)

    def test_gate_refuses_dotdot_and_symlink_specs_pre_read(self):
        repo_a, base_a = self.make_consistent_repo(self.SLOT_A)
        # ".." escape via the normal CLI: L3, zero reads.
        reads = []
        real_read = Path.read_bytes

        def spy(self, *args, **kwargs):
            reads.append(str(self))
            return real_read(self, *args, **kwargs)

        Path.read_bytes = spy
        try:
            selection, _ = SEL.run_selector(str(repo_a), base_a,
                                            map_path="../outside.json")
        finally:
            Path.read_bytes = real_read
        assert_full_l3(self, selection, "map-missing-or-inconsistent")
        # Zero foreign reads: every byte opened (e.g. the home repo's own
        # toolchain pins) stays inside the bound repo; the refused spec
        # is never resolved to a read. The gate raises before the
        # fingerprint, so the map identity is unknown (reuse refused).
        bound = str(Path(repo_a).resolve())
        self.assertEqual(
            [path for path in reads
             if not path.startswith(bound + "/") and path != bound], [])
        self.assertEqual(selection.fingerprint["map_sha256"], "unknown")
        # Symlink final component via the gate directly: refused with
        # zero read-API calls.
        target = Path(repo_a) / "tools" / "test_staging_map.json"
        link = Path(repo_a) / "tools" / "link-map.json"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        reads_link = []
        Path.read_bytes = spy_link = (
            lambda self, *args, **kwargs:
            (reads_link.append(str(self)),
             real_read(self, *args, **kwargs))[1])
        try:
            with self.assertRaises(SEL.MapError):
                SEL._normal_cli_map_target(str(repo_a),
                                           "tools/link-map.json")
        finally:
            Path.read_bytes = real_read
        self.assertEqual(reads_link, [])

    def test_explicit_synthetic_lane_unchanged(self):
        # Temp-only fixtures with an explicit loader bypass the normal-CLI
        # gate and never mix into the normal path (existing boundary kept).
        repo = make_repo("src/overrides/slot_3F.s")
        write_file(repo, "src/overrides/slot_3F.s", "changed\n")
        git(repo, "commit", "-qam", "change")
        map_path = write_map(repo, provisional_map(
            [l1_rule("src/overrides/*.s")]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)


class Review6096382075HiddenIndexFlagTests(unittest.TestCase):
    """Assume-unchanged/skip-worktree must fail closed (review 6096382075).

    ``git diff``/``status`` silently miss worktree edits behind the
    assume-unchanged (``h``) or skip-worktree (``S``/``s``) index flags,
    so a hidden L3 edit used to classify as L1 (or as no change) while
    the old evidence fingerprint stayed reusable across the hidden edit.
    ``collect_changes`` now refuses ANY non-``H`` ``ls-files -v -z`` tag
    (read-only: flags are never modified or cleared, no bytes are hashed)
    and the refusal escalates to full L3 with an unknown fingerprint, so
    reuse is refused in both directions.
    """
    L1_FILE = "src/overrides/slot_3F.s"
    L3_FILE = "src/experimental/new_feature.s"

    def make_two_file_repo(self):
        repo = make_repo(self.L1_FILE)
        write_file(repo, self.L3_FILE, "initial\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qam", "second file")
        git(repo, "tag", "-f", "vbase")
        return repo

    def select_with_rules(self, repo):
        map_path = write_map(repo, provisional_map(
            [l1_rule("src/overrides/*.s"),
             {"pattern": "src/experimental/*.s", "level": "L3",
              "commands": [{"make": "check"}],
              "reason": "experimental rule", "api": ""}]))
        return SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())

    def flag_cases(self):
        # (name, update-index args, expected ls-files -v tag). The
        # both-flags case needs two update-index calls: one call with
        # both flags only sets assume-unchanged ('h'); two calls set
        # both bits ('s'). Each case is a duplicate-literal per-var
        # proof: dropping the ls-files check re-trips every one to RED.
        return [
            ("assume-unchanged", [["--assume-unchanged"]], "h"),
            ("skip-worktree", [["--skip-worktree"]], "S"),
            ("both-flags", [["--assume-unchanged"],
                              ["--skip-worktree"]], "s"),
        ]

    def test_hidden_l3_edit_selects_full_l3(self):
        for name, flag_calls, tag in self.flag_cases():
            with self.subTest(flag=name):
                repo = self.make_two_file_repo()
                for flag in flag_calls:
                    git(repo, "update-index", *flag, self.L3_FILE)
                self.assertIn(f"{tag} {self.L3_FILE}",
                              git(repo, "ls-files", "-v"))
                write_file(repo, self.L3_FILE, "HIDDEN-L3-EDIT\n")
                write_file(repo, self.L1_FILE, "visible-l1-edit\n")
                selection, _ = self.select_with_rules(repo)
                assert_full_l3(self, selection, "hidden-index-flags")
                self.assertTrue(
                    any("assume-unchanged/skip-worktree" in reason
                        for reason in selection.reasons))
                self.assertEqual(
                    selection.fingerprint["inputs_sha256"], "unknown")
                self.assertFalse(SEL.evidence_reusable(
                    selection.fingerprint, selection.fingerprint))

    def test_flag_alone_with_no_edits_still_full_l3(self):
        # A flag with zero worktree changes is still untrustworthy: the
        # diff is empty either way, so the flag alone forces full L3.
        for name, flag_calls, _tag in self.flag_cases():
            with self.subTest(flag=name):
                repo = self.make_two_file_repo()
                for flag in flag_calls:
                    git(repo, "update-index", *flag, self.L3_FILE)
                selection, _ = self.select_with_rules(repo)
                assert_full_l3(self, selection, "hidden-index-flags")
                self.assertEqual(
                    selection.fingerprint["inputs_sha256"], "unknown")

    def test_collect_changes_raises_hidden_index_flags(self):
        for name, flag_calls, _tag in self.flag_cases():
            with self.subTest(flag=name):
                repo = self.make_two_file_repo()
                for flag in flag_calls:
                    git(repo, "update-index", *flag, self.L3_FILE)
                with self.assertRaises(SEL.GitError) as caught:
                    SEL.collect_changes(str(repo), "vbase")
                self.assertEqual(caught.exception.kind,
                                 "hidden-index-flags")

    def test_selector_never_modifies_index_flags_or_files(self):
        for name, flag_calls, tag in self.flag_cases():
            with self.subTest(flag=name):
                repo = self.make_two_file_repo()
                for flag in flag_calls:
                    git(repo, "update-index", *flag, self.L3_FILE)
                write_file(repo, self.L3_FILE, "HIDDEN-L3-EDIT\n")
                before_flags = git(repo, "ls-files", "-v")
                before_bytes = (repo / self.L3_FILE).read_bytes()
                self.select_with_rules(repo)
                # Read-only: the flag is still set (never cleared) and
                # the worktree bytes are untouched.
                self.assertEqual(git(repo, "ls-files", "-v"),
                                 before_flags)
                self.assertIn(f"{tag} {self.L3_FILE}", before_flags)
                self.assertEqual((repo / self.L3_FILE).read_bytes(),
                                 before_bytes)

    def test_old_evidence_reuse_refused_across_hidden_change(self):
        repo = self.make_two_file_repo()
        clean = SEL.collect_changes(str(repo), "vbase")
        old = SEL.evidence_fingerprint(
            str(repo), clean, map_path=None, level="L1",
            commands=[{"make": "dev-rom"}])
        git(repo, "update-index", "--assume-unchanged", self.L3_FILE)
        write_file(repo, self.L3_FILE, "HIDDEN-L3-EDIT\n")
        selection, _ = self.select_with_rules(repo)
        current = selection.fingerprint
        self.assertEqual(current["inputs_sha256"], "unknown")
        self.assertFalse(SEL.evidence_reusable(old, current))
        self.assertFalse(SEL.evidence_reusable(current, old))

    def test_whitespace_flagged_name_detected_verbatim(self):
        # No strip damage: a leading-space flagged path is refused with
        # its exact spelling (NUL-safe parse, fixed-prefix inspection).
        repo = make_repo("base.txt")
        write_file(repo, " lead.txt", "initial\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qam", "spaced")
        git(repo, "tag", "-f", "vbase")
        git(repo, "update-index", "--assume-unchanged", " lead.txt")
        with self.assertRaises(SEL.GitError) as caught:
            SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(caught.exception.kind, "hidden-index-flags")
        self.assertIn("' lead.txt'", str(caught.exception))

    def test_abnormal_ls_files_output_fails_closed(self):
        repo = self.make_two_file_repo()

        def malformed_runner(repo_path, *args):
            if args[:1] == ("ls-files",):
                return "H \0"  # empty path: unparseable
            return SEL._real_runner(repo_path, *args)

        def unknown_tag_runner(repo_path, *args):
            if args[:1] == ("ls-files",):
                return "X some/path.s\0"  # unknown tag
            return SEL._real_runner(repo_path, *args)

        for name, runner in (("malformed", malformed_runner),
                             ("unknown-tag", unknown_tag_runner)):
            with self.subTest(output=name):
                with self.assertRaises(SEL.GitError) as caught:
                    SEL.collect_changes(str(repo), "vbase", runner=runner)
                self.assertEqual(caught.exception.kind,
                                 "hidden-index-flags")
                selection, _ = SEL.run_selector(
                    str(repo), "vbase", runner=runner,
                    map_path=write_map(repo, provisional_map(
                        [l1_rule("src/**/*.s")])),
                    loader=SEL.ProvisionalJsonMapLoader())
                assert_full_l3(self, selection, "hidden-index-flags")
                self.assertEqual(
                    selection.fingerprint["inputs_sha256"], "unknown")

    def test_ls_files_git_failure_fails_closed(self):
        repo = self.make_two_file_repo()
        runner = FailingRunner({"ls-files"},
                               SEL.GitError("git-exit", "no ls-files"))
        with self.assertRaises(SEL.GitError):
            SEL.collect_changes(str(repo), "vbase", runner=runner)
        selection, _ = SEL.run_selector(
            str(repo), "vbase", runner=runner,
            map_path=write_map(repo, provisional_map(
                [l1_rule("src/**/*.s")])),
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "git-exit")
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")
        self.assertFalse(SEL.evidence_reusable(
            selection.fingerprint, selection.fingerprint))

    def test_untracked_still_forces_l3(self):
        repo = self.make_two_file_repo()
        write_file(repo, "brand_new.txt", "untracked\n")
        map_path = write_map(repo, provisional_map(
            [l1_rule("src/**/*.s"), l1_rule("*.txt")]))
        selection, _ = SEL.run_selector(
            str(repo), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "untracked-inputs")

    def test_normal_h_only_repo_unaffected(self):
        # No false positive: an H-only index still classifies normally
        # (committed/staged/unstaged captured, L1 selected).
        repo = self.make_two_file_repo()
        self.assertNotIn("\nh ", "\n" + git(repo, "ls-files", "-v"))
        write_file(repo, self.L1_FILE, "visible-l1-edit\n")
        changed = SEL.collect_changes(str(repo), "vbase")
        self.assertEqual(changed.unstaged, [("M", self.L1_FILE)])
        selection, _ = self.select_with_rules(repo)
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)


# ---------------------------------------------------------------------------
# Reviews 6096685265 + 6096688910 re-review (PR #857 Wave1): relative repo
# double-resolution.
#
# Pre-fix, _run_git passed BOTH `git -C str(repo)` AND `cwd=str(repo)`: with
# a relative repo "A" and an independent nested repo "A/A", the relative
# path applied twice (subprocess chdir into A, then git -C into A again),
# so every git operation bound the INNER repo while the map, ledger,
# Makefile, dependencies, fingerprint, and --fetch target referenced the
# OUTER repo (all exit 0, no GitError). The fix unifies binding to a SINGLE
# absolute path BEFORE any git use and verifies it against
# `rev-parse --show-toplevel` inside the same sanitized boundary, failing
# closed (full L3 + unknown fingerprint + reuse refused) on mismatch.
# All fixtures are synthetic temp dirs + sentinel bytes only (no ./rom,
# no real checkouts, no network beyond local bare remotes as fetch targets).
# ---------------------------------------------------------------------------


class Review6096688910RelativeRepoBindingTests(unittest.TestCase):
    """Relative repo "A" with an independent nested repo "A/A" inside."""
    OUTER_FILE = "src/overrides/slot_3F.s"
    INNER_FILE = "src/experimental/inner_feature.s"

    @contextlib.contextmanager
    def cwd_at(self, path):
        prev = os.getcwd()
        os.chdir(path)
        try:
            yield path
        finally:
            os.chdir(prev)

    def make_nested_pair(self):
        """Outer repo 'A' with an independent nested repo 'A/A'.

        Both tag vbase (so BASE resolves in BOTH: the tests prove the
        OUTER diff/HEAD is used, never the inner one), with diverging
        committed changes: the outer change matches an L1 rule, the inner
        change matches an L3 rule. The outer .gitignore covers the nested
        checkout dir so no untracked input masks the binding verdict.
        """
        parent = Path(tempfile.mkdtemp(prefix="r6096688910-"))
        outer = parent / "A"
        git(parent, "init", "-q", "A")
        git(outer, "config", "user.email", "test@example.invalid")
        git(outer, "config", "user.name", "staging-test")
        (outer / ".gitignore").write_text("A/\n")
        target = outer / self.OUTER_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("initial\n")
        nested = outer / "sub" / "nested.txt"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("nested\n")
        git(outer, "add", "-A")
        git(outer, "commit", "-qm", "outer baseline")
        git(outer, "tag", "vbase")
        write_file(outer, self.OUTER_FILE, "OUTER-CHANGED\n")
        git(outer, "commit", "-qam", "outer change")
        inner = outer / "A"
        git(outer, "init", "-q", "A")
        git(inner, "config", "user.email", "test@example.invalid")
        git(inner, "config", "user.name", "staging-test")
        write_file(inner, "inner.txt", "inner\n")
        write_file(inner, self.INNER_FILE, "inner initial\n")
        git(inner, "add", "-A")
        git(inner, "commit", "-qm", "inner baseline")
        git(inner, "tag", "vbase")
        write_file(inner, self.INNER_FILE, "INNER-DIVERGED\n")
        git(inner, "commit", "-qam", "inner change")
        return parent, outer, inner

    def l1_outer_map(self):
        return provisional_map([
            l1_rule("src/overrides/*.s", reason="outer override"),
            {"pattern": "src/experimental/*.s", "level": "L3",
             "commands": [{"make": "check"}],
             "reason": "inner experimental", "api": ""},
        ])

    def snapshot_repo(self, repo):
        """Byte-level snapshot of a repo (gitdir + refs + worktree)."""
        repo = Path(repo)
        gitdir = {}
        for path in sorted((repo / ".git").rglob("*")):
            if path.is_file():
                gitdir[str(path.relative_to(repo))] = path.read_bytes()
        work = {}
        for path in sorted(repo.rglob("*")):
            if ".git" in path.parts:
                continue
            if path.is_file():
                work[str(path.relative_to(repo))] = path.read_bytes()
        return {"gitdir": gitdir,
                "refs": git(repo, "for-each-ref").strip(),
                "head": git(repo, "rev-parse", "HEAD").strip(),
                "work": work}

    def test_bind_repo_unifies_to_single_absolute_path(self):
        parent, outer, _inner = self.make_nested_pair()
        with self.cwd_at(parent):
            self.assertEqual(SEL._bind_repo("A"), os.path.abspath("A"))
            self.assertEqual(SEL._bind_repo("A"), str(outer))
        self.assertEqual(SEL._bind_repo(str(outer)), str(outer))
        self.assertEqual(SEL._bind_repo(outer), str(outer))

    def test_bind_repo_rejects_unusable_specs(self):
        parent, outer, _inner = self.make_nested_pair()
        target = parent / "afile.txt"
        target.write_text("x\n")
        bad = [str(parent / "NOPE"), str(outer / "nope"), "",
                "a\0b", None, 123, str(target)]
        for spec in bad:
            with self.subTest(spec=spec):
                with self.assertRaises(SEL.GitError) as caught:
                    SEL._bind_repo(spec)
                self.assertEqual(caught.exception.kind, "repo")

    def test_bind_repo_refuses_dotdot_before_any_git_call(self):
        # Review 6096986826 MAJOR: a spec containing a `..` segment is
        # refused lexically BEFORE abspath/normalization and before ANY
        # git call, so an ambiguous spec that would resolve elsewhere can
        # never bind git ops to the wrong repo. A dotted name that is not
        # exactly `..` ("...", "a..b") still binds normally.
        parent, outer, _inner = self.make_nested_pair()
        with self.cwd_at(parent):
            specs = ["A/../A", "A/sub/../../A", "..",
                     str(outer / "sub" / ".."), "../" + parent.name]
            for spec in specs:
                with self.subTest(spec=spec):
                    with unittest.mock.patch.object(
                            subprocess, "run",
                            side_effect=AssertionError(
                                "git must not run before .. refusal")):
                        with self.assertRaises(SEL.GitError) as caught:
                            SEL._bind_repo(spec)
                    self.assertEqual(caught.exception.kind, "repo")
            # No false positives: normal relative/absolute/dot specs and
            # dotted-but-not-dotdot names still bind without a GitError.
            self.assertEqual(SEL._bind_repo("A"), str(outer))
            self.assertEqual(SEL._bind_repo(str(outer)), str(outer))
            (outer / "...").mkdir(exist_ok=True)
            (outer / "a..b").mkdir(exist_ok=True)
            with self.cwd_at(outer):
                self.assertEqual(SEL._bind_repo("."), str(outer))
            with self.cwd_at(parent):
                self.assertEqual(SEL._bind_repo("A/..."), str(outer / "..."))
                self.assertEqual(SEL._bind_repo("A/a..b"),
                                 str(outer / "a..b"))

    def test_dotdot_spec_fails_closed_full_l3_no_fetch(self):
        # The same `..` spec refused pre-read escalates through the normal
        # GitError path: full L3, unknown identity, reuse refused, and an
        # explicit --fetch never runs.
        parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        fetch_argvs = []
        real_run = subprocess.run

        def spy_run(argv, *args, **kwargs):
            if isinstance(argv, list) and "fetch" in argv:
                fetch_argvs.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with self.cwd_at(parent):
            with unittest.mock.patch.object(subprocess, "run", spy_run):
                selection, _ = SEL.run_selector(
                    "A/../A", "vbase", fetch=True, map_path=map_path,
                    loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "repo")
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")
        self.assertFalse(SEL.evidence_reusable(selection.fingerprint,
                                               selection.fingerprint))
        self.assertEqual(fetch_argvs, [])

    def test_relative_repo_binds_outer_head_not_inner(self):
        parent, outer, inner = self.make_nested_pair()
        outer_head = git(outer, "rev-parse", "HEAD").strip()
        inner_head = git(inner, "rev-parse", "HEAD").strip()
        self.assertNotEqual(outer_head, inner_head)
        with self.cwd_at(parent):
            self.assertEqual(
                SEL._real_runner("A", "rev-parse", "HEAD").strip(),
                outer_head)

    def test_relative_repo_collect_changes_binds_outer(self):
        parent, outer, inner = self.make_nested_pair()
        outer_head = git(outer, "rev-parse", "HEAD").strip()
        inner_head = git(inner, "rev-parse", "HEAD").strip()
        self.assertNotEqual(outer_head, inner_head)
        with self.cwd_at(parent):
            changed = SEL.collect_changes("A", "vbase")
        self.assertEqual(changed.head, outer_head)
        self.assertEqual(changed.base,
                         git(outer, "rev-parse", "vbase").strip())
        self.assertEqual(changed.committed, [("M", self.OUTER_FILE)])
        self.assertEqual(changed.untracked, [])

    def test_relative_repo_selector_classifies_outer_change(self):
        parent, outer, inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        with self.cwd_at(parent):
            selection, _ = SEL.run_selector(
                "A", "vbase", map_path=map_path,
                loader=SEL.ProvisionalJsonMapLoader())
        # The outer committed change matches the L1 rule; the inner
        # L3 change must never mix into the reasons or the level.
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertTrue(any(self.OUTER_FILE in reason
                            for reason in selection.reasons))
        self.assertFalse(any("inner_feature" in reason
                             for reason in selection.reasons))
        self.assertEqual(selection.fingerprint["head"],
                         git(outer, "rev-parse", "HEAD").strip())

    def test_absolute_repo_unaffected(self):
        _parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        selection, _ = SEL.run_selector(
            str(outer), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)

    def test_dot_repo_from_inside_root(self):
        _parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        with self.cwd_at(outer):
            selection, _ = SEL.run_selector(
                ".", "vbase", map_path=map_path,
                loader=SEL.ProvisionalJsonMapLoader())
            self.assertEqual(
                SEL._real_runner(".", "rev-parse", "HEAD").strip(),
                git(outer, "rev-parse", "HEAD").strip())
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)

    def test_nested_non_root_subdir_fails_closed(self):
        _parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        selection, _ = SEL.run_selector(
            str(outer / "sub"), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        # A subdir is not the worktree root: implicit parent-repo
        # discovery mixing is refused (full L3, unknown inputs, no reuse).
        assert_full_l3(self, selection, "repo")
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")
        self.assertFalse(SEL.evidence_reusable(selection.fingerprint,
                                               selection.fingerprint))

    def test_missing_repo_fails_closed(self):
        parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        with self.cwd_at(parent):
            with self.assertRaises(SEL.GitError) as caught:
                SEL.collect_changes("NOPE", "vbase")
            self.assertEqual(caught.exception.kind, "repo")
            selection, _ = SEL.run_selector(
                "NOPE", "vbase", map_path=map_path,
                loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "repo")
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")
        self.assertFalse(SEL.evidence_reusable(selection.fingerprint,
                                               selection.fingerprint))

    def test_non_git_dir_fails_closed(self):
        parent, outer, _inner = self.make_nested_pair()
        plain = parent / "plain"
        plain.mkdir()
        (plain / "note.txt").write_text("not a repo\n")
        map_path = write_map(outer, self.l1_outer_map())
        with self.assertRaises(SEL.GitError) as caught:
            SEL.collect_changes(str(plain), "vbase")
        self.assertEqual(caught.exception.kind, "repo")
        selection, _ = SEL.run_selector(
            str(plain), "vbase", map_path=map_path,
            loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "repo")
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")
        self.assertFalse(SEL.evidence_reusable(selection.fingerprint,
                                               selection.fingerprint))

    def test_linked_worktree_keeps_working(self):
        outer = make_repo(self.OUTER_FILE)
        sibling = outer.parent / "wt-linked"
        git(outer, "worktree", "add", str(sibling))
        try:
            write_file(sibling, self.OUTER_FILE, "WT-CHANGED\n")
            git(sibling, "commit", "-qam", "worktree change")
            wt_head = git(sibling, "rev-parse", "HEAD").strip()
            self.assertNotEqual(
                wt_head, git(outer, "rev-parse", "HEAD").strip())
            map_path = write_map(outer, self.l1_outer_map())
            selection, _ = SEL.run_selector(
                str(sibling), "vbase", map_path=map_path,
                loader=SEL.ProvisionalJsonMapLoader())
            self.assertEqual(selection.level, "L1")
            self.assertFalse(selection.fallback)
            self.assertEqual(
                SEL._real_runner(str(sibling), "rev-parse",
                                  "HEAD").strip(), wt_head)
            # A relative worktree path binds the worktree itself.
            with self.cwd_at(outer.parent):
                self.assertEqual(
                    SEL._real_runner(sibling.name, "rev-parse",
                                     "HEAD").strip(), wt_head)
        finally:
            git(outer, "worktree", "remove", "--force", str(sibling))

    def test_explicit_fetch_updates_only_bound_repo(self):
        parent, outer, inner = self.make_nested_pair()
        remote = parent / "r.git"
        proc = subprocess.run(["git", "init", "-q", "--bare",
                               str(remote)], capture_output=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        for repo in (outer, inner):
            git(repo, "remote", "add", "origin", str(remote))
        git(outer, "push", "-q", "origin", "HEAD:refs/heads/main")
        git(outer, "branch", "main")
        git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        writer = parent / "writer"
        git(parent, "clone", "-q", str(remote), str(writer))
        git(writer, "config", "user.email", "test@example.invalid")
        git(writer, "config", "user.name", "staging-test")
        (writer / "payload.txt").write_text("sentinel-payload\n")
        git(writer, "add", "-A")
        git(writer, "commit", "-qm", "advance remote")
        git(writer, "push", "-q", "origin", "HEAD:refs/heads/main")
        new_sha = git(writer, "rev-parse", "HEAD").strip()
        before_inner = self.snapshot_repo(inner)
        self.assertNotIn(".git/FETCH_HEAD", before_inner["gitdir"])
        fetch_argvs = []
        real_run = subprocess.run

        def spy_run(argv, *args, **kwargs):
            if isinstance(argv, list) and "fetch" in argv:
                fetch_argvs.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with self.cwd_at(parent):
            with unittest.mock.patch.object(subprocess, "run", spy_run):
                SEL.attempt_fetch("A", "main")
                changed = SEL.collect_changes("A", "main")
        # The fetch ran against the OUTER repo only: outer FETCH_HEAD
        # advanced, and the inner repo is byte-identical (no FETCH_HEAD,
        # refs, index, object, or worktree writes leaked inside).
        self.assertEqual(git(outer, "rev-parse",
                             "FETCH_HEAD").strip(), new_sha)
        self.assertEqual(changed.head,
                         git(outer, "rev-parse", "HEAD").strip())
        self.assertEqual(self.snapshot_repo(inner), before_inner)
        self.assertFalse((inner / ".git" / "FETCH_HEAD").exists())
        bound_targets = [argv[argv.index("-C") + 1]
                         for argv in fetch_argvs if "-C" in argv]
        self.assertTrue(bound_targets)
        for target in bound_targets:
            self.assertEqual(target, str(outer))

    def test_fetch_never_runs_after_failed_verification(self):
        _parent, outer, _inner = self.make_nested_pair()
        map_path = write_map(outer, self.l1_outer_map())
        fetch_argvs = []
        real_run = subprocess.run

        def spy_run(argv, *args, **kwargs):
            if isinstance(argv, list) and "fetch" in argv:
                fetch_argvs.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with unittest.mock.patch.object(subprocess, "run", spy_run):
            selection, _ = SEL.run_selector(
                str(outer / "sub"), "vbase", fetch=True,
                map_path=map_path,
                loader=SEL.ProvisionalJsonMapLoader())
        assert_full_l3(self, selection, "repo")
        self.assertEqual(fetch_argvs, [])
        self.assertFalse((outer / ".git" / "FETCH_HEAD").exists())
        self.assertEqual(selection.fingerprint["inputs_sha256"],
                         "unknown")

    def test_hostile_env_preserved_with_relative_repo(self):
        parent, outer, inner = self.make_nested_pair()
        outer_head = git(outer, "rev-parse", "HEAD").strip()
        self.assertNotEqual(
            outer_head, git(inner, "rev-parse", "HEAD").strip())
        hostile = {"GIT_DIR": str(inner / ".git"),
                   "GIT_WORK_TREE": str(inner),
                   "GIT_OBJECT_DIRECTORY":
                       str(inner / ".git" / "objects"),
                   "GIT_ALTERNATE_OBJECT_DIRECTORIES":
                       str(inner / ".git" / "objects"),
                   "GIT_QUARANTINE_PATH":
                       str(inner / ".git" / "objects"),
                   "GIT_INDEX_FILE": str(inner / ".git" / "index")}
        with self.cwd_at(parent):
            with unittest.mock.patch.dict(os.environ, hostile):
                self.assertEqual(
                    SEL._real_runner("A", "rev-parse",
                                     "HEAD").strip(), outer_head)
                changed = SEL.collect_changes("A", "vbase")
        self.assertEqual(changed.head, outer_head)
        self.assertEqual(changed.committed, [("M", self.OUTER_FILE)])


if __name__ == "__main__": unittest.main()
