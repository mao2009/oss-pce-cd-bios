"""Host unit tests for the prototype test-staging selector (Issue #852).

Static only: no cc65, no Geargrafx core, no private media, no network. Git
fixtures are synthetic temporary repositories. Every test must RUN (never
SKIP): a skip is never reported as success here.
"""
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

# Interim Wave1 pin of the Task-A branch HEAD used read-only by
# CanonicalDefaultTests.test_cli_end_to_end_with_real_canonical_map.
# Recorded 2026-11-28 as 3dad5dad (doc-fix-only, schema stable); branch
# moved to 71ad00ab (doc-only local_media/commercial_media_access wording,
# schema unchanged), then to e8d51e75 (enumeration-probe non-destructive
# test-only change, schema stable -- verified: diff 71ad00ab..e8d51e75
# touches only tests/host/test_test_staging_map.py). Pin updated here.
# If the branch moves again this pin fails and Wave3 finalises it against
# the merge (no checkout, no tracked copy, no ./rom).
REAL_MAP_HEAD_PIN = "e8d51e75c0e3f4b324342722b5538c0f1c1faf2d"


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
        # Interim pin (Wave1): read-only use of the Task-A branch HEAD.
        # No checkout, no copy into tracked paths, no ./rom. If the branch
        # moves, this pin fails and Wave3 finalises against the merge.
        ref = "origin/agent/test-staging-map-v0-20261128"
        try:
            head = subprocess.run(
                ["git", "rev-parse", ref], cwd=str(ROOT),
                capture_output=True, text=True,
                timeout=120)
        except (OSError, subprocess.SubprocessError) as error:
            self.skipTest(f"cannot resolve {ref}: {error}")
        if head.returncode != 0:
            self.skipTest(f"Task-A branch ref absent: {head.stderr[-200:]}")
        self.assertEqual(head.stdout.strip(), REAL_MAP_HEAD_PIN)
        blob = subprocess.run(
            ["git", "show", f"{REAL_MAP_HEAD_PIN}:tools/test_staging_map.json"],
            cwd=str(ROOT), capture_output=True, timeout=120)
        self.assertEqual(blob.returncode, 0)
        with tempfile.NamedTemporaryFile(
                suffix=".json", delete=False) as handle:
            handle.write(blob.stdout)
            real_map = handle.name
        try:
            repo = make_repo("src/overrides/slot_3F.s")
            # B-4 repo binding: the default CLI loader reads ledger +
            # Makefile + pins + host/script existence from --repo, never a
            # silent ROOT fallback. Populate the synthetic repo with the
            # (unmodified) reference files the real map needs, committed
            # as baseline so the only diff is the slot change; the map
            # under test is still the read-only Task-A blob (temp file).
            import shutil as _shutil
            (repo / "spec").mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(str(ROOT / "spec" / "api_slots.json"),
                             str(repo / "spec" / "api_slots.json"))
            _shutil.copyfile(str(ROOT / "Makefile"),
                             str(repo / "Makefile"))
            (repo / "tools").mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(str(ROOT / "tools" / "dependencies.json"),
                             str(repo / "tools" / "dependencies.json"))
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
                   "--map", real_map, "--json"]
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
            try:
                os.unlink(real_map)
            except OSError:
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
            self.assertNotEqual(first["env"], second["env"])
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
if __name__ == "__main__": unittest.main()
