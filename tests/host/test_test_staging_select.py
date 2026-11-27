"""Host unit tests for the prototype test-staging selector (Issue #852).

Static only: no cc65, no Geargrafx core, no private media, no network. Git
fixtures are synthetic temporary repositories. Every test must RUN (never
SKIP): a skip is never reported as success here.
"""
import json
import subprocess
import sys
import tempfile
import unittest
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

    def test_require_cc65_preserved_on_every_command(self):
        for kwargs in ({"map_path": self.map_path}, {}):
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
        selection, _ = SEL.run_selector(str(clean), "vbase", map_path=empty_map)
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertEqual(selection.selected[0]["env"],
                         {SEL.REQUIRE_CC65_ENV: "1"})

    def test_l1_output_marks_partial_and_lists_unselected(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path)
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
        for kwargs in ({"map_path": self.map_path}, {}):
            selection, _ = SEL.run_selector(str(self.repo), "vbase", **kwargs)
            report = SEL.render(selection)
            self.assertIn("SKIP-POLICY", report)
            self.assertIn("never PASS", report)
            self.assertIn(SEL.REQUIRE_CC65_ENV, report)


class EvidenceTests(unittest.TestCase):
    def fingerprint(self, **overrides):
        base = {"head": "h" * 40, "base": "b" * 40,
                "inputs_sha256": "i" * 64, "python": "3.12.3",
                "platform": "linux", "toolchain": "{\"cc65\": \"abc\"}",
                "mode": "debug", "env": {}}
        base.update(overrides)
        return base

    def test_exact_match_reuses(self):
        stored = self.fingerprint()
        self.assertTrue(SEL.evidence_reusable(stored, self.fingerprint()))

    def test_any_field_mismatch_refuses_reuse(self):
        stored = self.fingerprint()
        mutations = [
            {"head": "0" * 40}, {"base": "0" * 40},
            {"inputs_sha256": "0" * 64}, {"python": "3.11.0"},
            {"platform": "other"}, {"toolchain": "{\"cc65\": \"zzz\"}"},
            {"mode": "release"}, {"env": {"X": "1"}},
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
                                 "reason": "provisional api entry"}}))

    def test_known_api_selects_l1_subset(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="MA_MUL8U")
        self.assertEqual(selection.level, "L1")
        self.assertFalse(selection.fallback)
        self.assertEqual([cmd["argv"] for cmd in selection.selected],
                         [["make", "dev-rom"]])

    def test_unknown_api_falls_back_to_l3(self):
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="NOPE")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)
        self.assertIn("NOPE", selection.fallback_reason)

    def test_api_mode_with_out_of_scope_file_falls_back_to_l3(self):
        write_file(self.repo, "src/common/other.s", "changed\n")
        git(self.repo, "add", "src/common/other.s")
        selection, _ = SEL.run_selector(str(self.repo), "vbase",
                                        map_path=self.map_path, api="MA_MUL8U")
        self.assertEqual(selection.level, "L3")
        self.assertTrue(selection.fallback)


if __name__ == "__main__":
    unittest.main()
