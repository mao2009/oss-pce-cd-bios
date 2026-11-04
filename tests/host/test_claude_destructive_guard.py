"""Decision-only tests for .claude/hooks/guard_destructive.py.

The guard receives hook JSON and returns a verdict. Nothing in this module runs
rm, git clean, or any other destructive command; the static test at the bottom
enforces that by walking the AST of this file.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOK = os.path.join(REPO, ".claude", "hooks", "guard_destructive.py")
THIS_FILE = os.path.abspath(__file__)

_spec = importlib.util.spec_from_file_location("guard_destructive", HOOK)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

DENY_COMMANDS = [
    "rm -rf /mnt/c/home/synthetic-user",
    "rm -rf /home/synthetic-user/work",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf ../",
    "rm --recursive --force dir",
    "rm -fr dir",
    "rm -r -f dir",
    "rm -rfv dir",
    "rm -R dir",
    "sudo rm -rf dir",
    "bash -c 'rm -rf dir'",
    "sh -c \"git clean -fdx\"",
    "git clean -fdx",
    "git worktree remove --force path",
    "git worktree remove /some/unregistered/path",
    "git worktree prune",
    "git reset --hard",
    "git checkout --force",
    "git checkout -- README.md",
    "git restore README.md",
    "git stash clear",
    "git branch -D some-branch",
    "find . -type f -delete",
    "find . -exec rm -rf {} \\;",
    "python3 -c 'import shutil; shutil.rmtree(\"dir\")'",
    "Remove-Item -Recurse -Force dir",
    "powershell -Command \"Remove-Item -Recurse -Force dir\"",
    "echo x | xargs rm -rf",
    "unlink README.md",
    "dd if=/dev/zero of=x",
    # evasion variants
    "rm \"-rf\" dir",
    "rm -r \\\n-f dir",
    "r\\m -rf dir",
    "/bin/rm -rf dir",
    "\\rm -rf dir",
    "\\rm -r -f dir",
    "\\\\rm -rf dir",
    "\\rm README.md \\rm",
    "true; rm -rf dir",
    "git status && rm -rf dir",
    "git status\nrm -rf dir",
    "cd sub && git clean -fd",
    "env FOO=1 rm -rf dir",
    "command rm -rf dir",
    "nohup rm -rf dir",
    "time rm -rf dir",
    "( rm -rf dir )",
    "echo $(rm -rf dir)",
    "echo `rm -rf dir`",
    "eval 'rm -rf dir'",
    "$RM -rf dir",
    "rm ../outside.txt",
    "rm .claude/hooks/guard_destructive.py",
    "rm .claude/settings.json",
    "rm $HOME/x",
    "rm \"$TARGET\"",
    "rm README.md ../x",
    "rm",
    "rm -- README.md ../x",
    "rm -r README.md",
    "git status; \\\n rm -rf dir",
    # CRITICAL 1: CR (JSON \r) is whitespace to shlex, so "\rm" became "m"
    "\rm -rf dir",
    "\rm README.md",
    "git status\rrm -rf dir",
    # MAJOR 2: command substitution whose content cannot be inspected
    "sh -c \"$(echo cm0gLXJmIHg= | base64 -d)\"",
    "bash -c \"$(cat f)\"",
    "zsh -c `cat f`",
    "eval \"$(cat f)\"",
    "sh -c \"echo $(date)\"",
    "source <(cat f)",
    "$(echo rm) -rf dir",
    "echo x | sh -c \"$(cat f)\"",
    # MAJOR 4: git aliases defined on the command line
    "git -c alias.x=clean x",
    "git -c alias.x=\"clean -fdx\" x",
    "git -c Alias.X=clean x",
    "git -C dir clean -fd",
    "git --git-dir=.git clean -fd",
    "git --git-dir .git clean -fd",
    # MAJOR 5: writes whose target is inside .claude/
    "echo x > .claude/worktree-owners.json",
    "echo x >> .claude/worktree-owners.json",
    "echo x > \".claude/worktree-owners.json\"",
    "echo x >.claude/worktree-owners.json",
    "echo x > $(echo .claude/worktree-owners.json)",
    "tee .claude/worktree-owners.json",
    "sed -i s/a/b/ .claude/settings.json",
    "cp x .claude/worktree-owners.json",
    "mv .claude/worktree-owners.json y",
    "install x .claude/y",
    "touch .claude/x",
    "rsync x .claude/",
    "dd of=.claude/x if=/dev/zero",
    "cd .claude && echo x > worktree-owners.json",
    "cd .claude && cp x worktree-owners.json",
    "python3 -c \"open('.claude/worktree-owners.json', 'w')\"",
    "python3 .claude/hooks/helper.py",
    "perl -i -pe s/a/b/ .claude/settings.json",
    # M1: NUL byte and other control characters fail closed
    "rm\x00 -rf dir",
    # M2: empty quote pairs inside a command name
    "r\"\"m -r -fo dir",
    "r\"\"m -rf dir",
    "r''m -rf dir",
    # M3: git push refspec deletion
    "git push origin :branch",
    "git push origin +:branch",
    "git push origin :refs/heads/x",
    "git push --prune origin",
    "git push origin --delete x",
    "git push -d origin x",
    # M5a: backslash-escaped .claude (\X quotes X, so .cl\aude is .claude)
    "echo x > .cl\\aude/f",
    "echo x > .clau\\de/f",
    "echo x >| .claude/worktree-owners.json",
    "cp x .cl\\aude/f",
    # M5b: write target not statically known (unexpanded variable or glob)
    "X=.claude; echo x > $X/f",
    "echo x > docs/$NAME.md",
    "X=.claude; touch $X/f",
    "echo x > ${HOME}/f",
]

POWERSHELL_DENY_COMMANDS = [
    # MAJOR 3: content of an expression or call operand is not known
    "iex \"Remove-Item -Recurse -Force dir\"",
    "iex $x",
    "Invoke-Expression \"x\"",
    "invoke-expression 'x'",
    "& \"git\" clean -fdx",
    "& 'rm' -rf dir",
    "& $cmd",
    "&\"x\"",
    "pwsh -c \"$(Get-Content f)\"",
]

ALLOW_COMMANDS = [
    "git status",
    "git diff --check",
    "git worktree list",
    "make test",
    "make check",
    "python3 -m unittest",
    "ls -la",
    "cat README.md",
    "cat .claude/settings.json",
    "ls .claude",
    "rm README.md",
    "git restore --staged README.md",
    "echo \"rm -rf dir\"",
    "git status; ls -la",
    "python3 -c 'print(1)'",
    "cd docs && ls",
    "echo $(date)",
    "echo `date`",
    "git -C dir status",
    "git -c color.ui=false status",
    "git push origin HEAD:refs/heads/x",
    "cp README.md README.bak",
    "echo $X",
    "echo x > docs/f.md",
    "echo x 2>&1",
]

POWERSHELL_ALLOW_COMMANDS = [
    "Get-ChildItem",
    "git status",
    "git -C dir status",
    "git push origin HEAD:refs/heads/x",
    "cat .claude/settings.json",
    "echo \"rm -rf dir\"",
    "cd docs; ls",
    "git status && git diff",
    "echo x > docs/f.md",
    "echo x >/dev/null 2>&1",
    "make test 2>&1",
]

INVALID_INPUTS = [
    "{not json",
    "[]",
    '{"tool_name":"Bash"}',
    '{"tool_name":"Bash","tool_input":{}}',
    '{"tool_name":"Bash","tool_input":{"command":""}}',
    '{"tool_name":"Bash","tool_input":{"command":"   "}}',
    '{"tool_name":"Bash","tool_input":{"command":5}}',
    '{"tool_input":{"command":"git status"}}',
    # M1: NUL and other control characters
    '{"tool_name":"Bash","tool_input":{"command":"git status\\u0000"}}',
    '{"tool_name":"Bash","tool_input":{"command":"git\\rstatus"}}',
    '{"tool_name":"Bash","tool_input":{"command":"git\\u0007status"}}',
]


def payload(cmd, tool="Bash"):
    return json.dumps({
        "session_id": "t",
        "hook_event_name": "PreToolUse",
        "cwd": REPO,
        "tool_name": tool,
        "tool_input": {"command": cmd},
    })


def verdict(cmd, tool="Bash", repo_root=REPO):
    return guard.decide(payload(cmd, tool), repo_root=repo_root)[0].upper()


def run_hook(text):
    p = subprocess.run([sys.executable, HOOK], input=text, capture_output=True,
                       text=True, timeout=60)
    return p.returncode, p.stdout


def run_hook_bytes(data):
    """Feed raw bytes on stdin (the hook must decode them itself)."""
    p = subprocess.run([sys.executable, HOOK], input=data, capture_output=True,
                       timeout=60)
    return p.returncode, p.stdout.decode("ascii", errors="replace")


def stdout_verdict(stdout):
    if not stdout.strip():
        return "allow"
    return json.loads(stdout)["hookSpecificOutput"]["permissionDecision"]


class DenyTests(unittest.TestCase):
    def test_destructive_commands_denied_bash(self):
        for cmd in DENY_COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, "Bash"), "DENY")

    def test_destructive_commands_denied_powershell(self):
        for cmd in DENY_COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, "PowerShell"), "DENY")

    def test_powershell_only_denied_powershell(self):
        for cmd in POWERSHELL_DENY_COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, "PowerShell"), "DENY")

    def test_invalid_inputs_denied(self):
        for raw in INVALID_INPUTS:
            with self.subTest(raw=raw):
                self.assertEqual(guard.decide(raw, repo_root=REPO)[0].upper(), "DENY")

    def test_guard_exception_denied(self):
        with mock.patch.object(guard, "decide", side_effect=RuntimeError("injected")):
            out = guard.run_hook(payload("git status"))
        self.assertEqual(stdout_verdict(out), "deny")

    def test_timeout_denied(self):
        def slow(raw, **kwargs):
            time.sleep(2)
            return ("ALLOW", "slow")
        with mock.patch.object(guard, "decide", side_effect=slow):
            out = guard.run_hook(payload("git status"), timeout=0.2)
        self.assertEqual(stdout_verdict(out), "deny")

    def test_end_to_end_subprocess_deny(self):
        rc, out = run_hook(payload("rm -rf dir"))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout_verdict(out), "deny")

    def test_end_to_end_invalid_json_deny(self):
        rc, out = run_hook("{not json")
        self.assertEqual(rc, 0)
        self.assertEqual(stdout_verdict(out), "deny")

    def test_non_utf8_stdin_denied(self):
        # cp932 bytes are not valid UTF-8: must deny, not crash open.
        data = b'{"tool_name":"Bash","tool_input":{"command":"echo \x82\xa0"}}'
        rc, out = run_hook_bytes(data)
        self.assertEqual(rc, 0)
        self.assertEqual(stdout_verdict(out), "deny")

    def test_utf8_stdin_decoded(self):
        data = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo 日本語"}},
                          ensure_ascii=False).encode("utf-8")
        rc, out = run_hook_bytes(data)
        self.assertEqual(rc, 0)
        self.assertEqual(stdout_verdict(out), "allow")


class HomeRootTests(unittest.TestCase):
    """The home directory is derived at run time. No user-specific path is written into the guard."""

    def test_generic_roots_only_in_the_source(self):
        self.assertEqual(guard.PROTECTED_BASE, ["/", "/home", "/mnt", "/mnt/c", "/mnt/c/users"])

    def test_home_roots_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/synthetic-user", "USERPROFILE": ""}):
            roots = guard.home_roots()
        self.assertIn(guard.canon("/home/synthetic-user"), roots)

    def test_windows_and_mount_forms_of_one_home_compare_equal(self):
        with mock.patch.dict(os.environ, {"HOME": "", "USERPROFILE": "C:\\Users\\synthetic-user"}):
            roots = guard.home_roots()
        self.assertIn(guard.canon("/mnt/c/Users/synthetic-user"), roots)

    def test_relative_home_is_refused(self):
        with mock.patch.dict(os.environ, {"HOME": "relative/dir", "USERPROFILE": ""}):
            with self.assertRaises(guard.Deny) as caught:
                guard.home_roots()
        self.assertEqual(caught.exception.kind, "home")

    def test_msys_and_windows_forms_of_one_path_compare_equal(self):
        self.assertEqual(guard.canon("/c/Users/synthetic-user"), guard.canon("C:\\Users\\synthetic-user"))

    def test_local_roots_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"OSS_PCE_GUARD_PROTECTED": "/home/synthetic-owner"}):
            roots = guard.local_roots(REPO)
        self.assertIn(guard.canon("/home/synthetic-owner"), roots)

    def test_malformed_local_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".claude"))
            with open(os.path.join(tmp, ".claude", "guard-local.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(guard.Deny) as caught:
                guard.local_roots(tmp)
        self.assertEqual(caught.exception.kind, "config")

    def test_local_root_protects_a_registered_worktree_at_that_root(self):
        # A registered worktree at a protected root is refused; without the local root it is allowed.
        target = "/home/synthetic-owner/work"
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git worktree remove " + target},
                              "cwd": REPO})
        registry = {guard.key(target)}
        # local_roots is patched in both arms so that a local guard-local.json cannot change the result.
        with mock.patch.object(guard, "load_registry", return_value=registry):
            with mock.patch.object(guard, "local_roots", return_value=[guard.canon(target)]):
                self.assertEqual(guard.decide(payload, REPO)[0], guard.DENY)
            with mock.patch.object(guard, "local_roots", return_value=[]):
                self.assertEqual(guard.decide(payload, REPO)[0], guard.ALLOW)

    def test_undeterminable_home_fails_closed(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": REPO}
        with mock.patch("os.path.expanduser", return_value="~"), \
                mock.patch.dict(os.environ, {"HOME": "", "USERPROFILE": ""}):
            verdict, reason = guard.decide(json.dumps(payload), REPO)
        self.assertEqual(verdict, guard.DENY)
        self.assertTrue(reason.startswith("home:"), reason)


class AllowTests(unittest.TestCase):
    def test_benign_commands_allowed(self):
        for cmd in ALLOW_COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, "Bash"), "ALLOW")

    def test_benign_powershell_commands_allowed(self):
        for cmd in POWERSHELL_ALLOW_COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, "PowerShell"), "ALLOW")

    def test_end_to_end_subprocess_allow(self):
        rc, out = run_hook(payload("git status"))
        self.assertEqual(rc, 0)
        self.assertEqual(stdout_verdict(out), "allow")


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = td.name
        os.makedirs(os.path.join(self.root, ".claude"))
        self.owned = os.path.join(self.root, "wt-owned")
        self.registry = os.path.join(self.root, ".claude", "worktree-owners.json")
        with open(self.registry, "w", encoding="utf-8") as f:
            json.dump({"worktrees": [{"path": self.owned, "owner": "alice"}]}, f)

    def test_registered_worktree_remove_allowed(self):
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.root), "ALLOW")

    def test_unregistered_worktree_remove_denied(self):
        cmd = "git worktree remove " + os.path.join(self.root, "wt-other")
        self.assertEqual(verdict(cmd, repo_root=self.root), "DENY")

    def test_registered_worktree_forced_remove_denied(self):
        cmd = "git worktree remove --force " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.root), "DENY")

    def test_broken_registry_denies_everything_removable(self):
        with open(self.registry, "w", encoding="utf-8") as f:
            f.write("{broken")
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.root), "DENY")

    def test_forged_registry_write_denied(self):
        # MAJOR 5: the registry cannot be forged with a shell write.
        cmd = "echo {} > " + self.registry
        self.assertEqual(verdict(cmd, repo_root=self.root), "DENY")


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ProtectedPathTests(unittest.TestCase):
    """Protected roots: home, repository root, ancestors of protected paths, environment and local config,
    linked worktrees, symlinks, fail-closed configuration, and normal operations that must stay allowed.

    Every decision is made on a temporary layout with a synthetic home. No test deletes or writes a real
    worktree or user file; the static test at the bottom of this module keeps it that way.
    """

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = os.path.realpath(td.name)
        self.home = os.path.join(self.tmp, "home", "synthetic-owner")
        self.repo = os.path.join(self.tmp, "repo")
        self.owned = os.path.join(self.tmp, "owned-elsewhere")
        self.ok_wt = os.path.join(self.tmp, "wt-ok")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, ".claude"))
        os.makedirs(os.path.join(self.repo, "sub"))
        write_text(os.path.join(self.repo, "file.txt"), "x\n")
        self._registry(self.repo, {self.home, self.repo, self.owned, self.ok_wt,
                                   os.path.dirname(self.home), self.tmp})
        env = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home,
                                           "OSS_PCE_GUARD_PROTECTED": ""})
        env.start()
        self.addCleanup(env.stop)

    def _registry(self, root, paths):
        write_text(os.path.join(root, ".claude", "worktree-owners.json"),
                   json.dumps({"worktrees": [{"path": p, "owner": "alice"} for p in sorted(paths)]}))

    def _local(self, content):
        write_text(os.path.join(self.repo, ".claude", "guard-local.json"), content)

    def _symlink(self, target, link, is_dir):
        try:
            os.symlink(target, link, target_is_directory=is_dir)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation not permitted here: " + type(exc).__name__)

    # Home, repository root and ancestors of protected paths.
    def test_home_directory_removal_denied(self):
        for cmd in ("git worktree remove " + self.home, "rm -rf " + self.home, "rm " + self.home):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_repository_root_removal_denied(self):
        for cmd in ("git worktree remove " + self.repo, "rm -rf " + self.repo, "rm " + self.repo):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_parent_of_protected_path_removal_denied(self):
        for cmd in ("git worktree remove " + os.path.dirname(self.home),
                    "git worktree remove " + self.tmp):
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    # Extra protection: environment variable, guard-local.json, and the main checkout of a linked worktree.
    def test_environment_variable_adds_protection(self):
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")
        with mock.patch.dict(os.environ, {"OSS_PCE_GUARD_PROTECTED": self.owned}):
            self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_guard_local_json_adds_protection(self):
        cmd = "git worktree remove " + self.owned
        self._local(json.dumps({"protected": [self.owned]}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self._local(json.dumps({"protected": []}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")

    def test_linked_worktree_uses_main_checkout_local_config(self):
        main = os.path.join(self.tmp, "main")
        linked = os.path.join(self.tmp, "linked")
        gitdir = os.path.join(main, ".git", "worktrees", "linked")
        os.makedirs(os.path.join(main, ".claude"))
        write_text(os.path.join(linked, ".git"), "gitdir: " + gitdir + "\n")
        write_text(os.path.join(gitdir, "commondir"), "../..\n")
        self._registry(linked, {self.owned})
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=linked), "ALLOW")
        write_text(os.path.join(main, ".claude", "guard-local.json"), json.dumps({"protected": [self.owned]}))
        self.assertEqual(verdict(cmd, repo_root=linked), "DENY")

    # Fail closed: a broken configuration refuses; it never removes a protection.
    def test_broken_local_config_fails_closed(self):
        for content in ("{bad", "[]", json.dumps({"protected": "x"}), json.dumps({"protected": [1]}),
                        json.dumps({"protected": ["relative/owned"]})):
            self._local(content)
            with self.subTest(content=content):
                self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")

    def test_relative_environment_entry_fails_closed(self):
        with mock.patch.dict(os.environ, {"OSS_PCE_GUARD_PROTECTED": "relative/owned"}):
            self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")

    def test_unresolvable_linked_worktree_fails_closed(self):
        broken = os.path.join(self.tmp, "linked-broken")
        write_text(os.path.join(broken, ".git"), "not a gitdir line\n")
        self.assertEqual(verdict("git status", repo_root=broken), "DENY")
        other = os.path.join(self.tmp, "other-gitdir")
        write_text(os.path.join(broken, ".git"), "gitdir: " + other + "\n")
        write_text(os.path.join(other, "commondir"), ".\n")
        self.assertEqual(verdict("git status", repo_root=broken), "DENY")

    def test_absent_local_config_adds_nothing_and_keeps_defaults(self):
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude", "guard-local.json")))
        self.assertEqual(verdict("git worktree remove " + self.home, repo_root=self.repo), "DENY")
        self.assertEqual(verdict("git worktree remove " + self.owned, repo_root=self.repo), "ALLOW")

    # Worktree removal.
    def test_worktree_removal_cases(self):
        self.assertEqual(verdict("git worktree remove " + self.ok_wt, repo_root=self.repo), "ALLOW")
        self.assertEqual(verdict("git worktree remove " + os.path.join(self.tmp, "unregistered"),
                                 repo_root=self.repo), "DENY")
        self.assertEqual(verdict("git worktree remove --force " + self.ok_wt, repo_root=self.repo), "DENY")
        self.assertEqual(verdict("git worktree prune", repo_root=self.repo), "DENY")

    # Symlinks must not reach a protected or outside target.
    def test_symlink_bypass_denied(self):
        link_home = os.path.join(self.repo, "link-home")
        self._symlink(self.home, link_home, True)
        self.assertEqual(verdict("git worktree remove " + link_home, repo_root=self.repo), "DENY")
        outside = os.path.join(self.tmp, "outside.txt")
        write_text(outside, "outside\n")
        link_file = os.path.join(self.repo, "link-file")
        self._symlink(outside, link_file, False)
        self.assertEqual(verdict("rm " + link_file, repo_root=self.repo), "DENY")
        outdir = os.path.join(self.tmp, "outdir")
        write_text(os.path.join(outdir, "x"), "x\n")
        link_dir = os.path.join(self.repo, "link-dir")
        self._symlink(outdir, link_dir, True)
        self.assertEqual(verdict("rm " + os.path.join(link_dir, "x"), repo_root=self.repo), "DENY")

    # Normal operations must stay allowed (decision only; nothing is run).
    def test_normal_operations_allowed(self):
        cmds = ["rm " + os.path.join(self.repo, "file.txt"),
                "rm -f " + os.path.join(self.repo, "file.txt"),
                "git status", "git diff --stat", "git worktree remove " + self.ok_wt,
                "make test", "python3 -m unittest discover -s tests/host -v"]
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")

    def test_forced_removal_of_absent_or_protected_targets_still_denied(self):
        # No shortcut for an absent target: an earlier segment may create it (ln -s ... && rm -f ...).
        cmds = ["rm -f " + os.path.join(self.tmp, "not-there.txt"),   # outside the repo
                "rm -f " + os.path.join(self.repo, "not-there.txt"),   # absent inside the repo
                "ln -s " + self.owned + " " + os.path.join(self.repo, "d") + " && rm -f "
                + os.path.join(self.repo, "d", "victim"),
                "rm -f " + os.path.join(self.repo, "sub"),            # existing directory
                "rm -f " + os.path.join(self.repo, ".claude", "nope.json")]
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    # Regression for false positives: read-only python must pass; write forms must stay denied.
    def test_read_only_python_is_not_refused(self):
        for code in ["import json; print(json.load(open('x.json')))",
                     "print(open('a.txt').read())",
                     "print(open('a.txt', 'rb').read())",
                     "print(open('a.txt', 'rt').read())"]:
            with self.subTest(code=code):
                self.assertEqual(verdict("python3 -c " + repr(code), repo_root=self.repo), "ALLOW")

    def test_python_write_forms_stay_denied(self):
        for code in ["open('a.txt', 'w').write('x')",
                     "open('a.txt', chr(119)).write('x')",
                     "from pathlib import Path; Path('a').open('w')",
                     "open('a.txt', 'r+')",
                     "open('a.txt', mode='w')",
                     "import os; os.replace('a', 'b')",
                     "import shutil; shutil.copy('a', 'b')",
                     # A closing paren inside a string literal must not hide the real mode.
                     "open('/x)'[:-1], 'w').close()",
                     "print('evil', file=open('x)'[:-1], 'w'))",
                     "open('.cla' + 'ude/s)'[:-1], 'w')"]:
            with self.subTest(code=code):
                self.assertEqual(verdict("python3 -c " + repr(code), repo_root=self.repo), "DENY")

    def test_python_unparseable_or_indirect_open_denied(self):
        for code in ["open('a.txt'", "f = open; f('a.txt', 'w')", "import io; io.open('a.txt', 'w')"]:
            with self.subTest(code=code):
                self.assertEqual(verdict("python3 -c " + repr(code), repo_root=self.repo), "DENY")

    def test_git_metadata_writes_denied(self):
        cmds = ["echo gitdir: " + self.tmp + " > " + os.path.join(self.repo, ".git"),
                "mv " + os.path.join(self.repo, ".git") + " " + os.path.join(self.tmp, "elsewhere"),
                "cp README.md " + os.path.join(self.repo, ".git", "config")]
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self.assertEqual(verdict("echo x > .gitignore-note", repo_root=self.repo), "ALLOW")


CHILD = r"""
import importlib.util, json, sys, time
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
raw = sys.stdin.read()
t0 = time.perf_counter()
v = g.decide(raw, repo_root=sys.argv[2])
print(json.dumps({"verdict": v[0], "reason": v[1], "secs": time.perf_counter() - t0}))
"""

# (name, command, expected verdict). Inputs over 64 KB are denied by the size cap.
PERF_CASES = [
    ("gt-heavy-200k", "a>" * 100000, "DENY"),
    ("a-200k", "a" * 200000, "DENY"),
    ("semi-200k", "a;" * 100000, "DENY"),
    ("paren-200k", "(" * 200000, "DENY"),
    ("gt-heavy-60k", "a>" * 30000, "ALLOW"),
]


class PerfTests(unittest.TestCase):
    def timed_decide(self, cmd):
        """Run decide() in a child process so a slow C-level regex cannot hang the suite."""
        try:
            p = subprocess.run([sys.executable, "-c", CHILD, HOOK, REPO],
                               input=payload(cmd), capture_output=True, text=True,
                               timeout=10)
        except subprocess.TimeoutExpired:
            return None
        return json.loads(p.stdout)

    def test_oversize_command_denied(self):
        # TIMEOUT: a hard size cap bounds the work, so no timeout is relied on.
        self.assertEqual(verdict("echo " + "a" * 70000), "DENY")
        self.assertIn("too large to inspect", guard.decide(payload("echo " + "a" * 70000), repo_root=REPO)[1])

    def test_inputs_decide_within_two_seconds(self):
        for name, cmd, expected in PERF_CASES:
            with self.subTest(case=name):
                res = self.timed_decide(cmd)
                self.assertIsNotNone(res, "decide() did not finish within 10 s")
                self.assertLess(res["secs"], 2.0)
                self.assertEqual(res["verdict"], expected)


class StaticTests(unittest.TestCase):
    """The test module must never call a process or file API with a destructive target."""

    FORBIDDEN_PREFIXES = (
        "os.system", "os.popen", "os.remove", "os.unlink", "os.rmdir",
        "os.removedirs", "os.spawn", "os.exec", "shutil.rmtree", "shutil.move",
        "pathlib.Path.unlink", "send2trash",
    )

    def test_no_destructive_calls_in_test_module(self):
        with open(THIS_FILE, encoding="utf-8") as f:
            tree = ast.parse(f.read(), THIS_FILE)
        problems = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name.startswith(self.FORBIDDEN_PREFIXES):
                problems.append((node.lineno, name))
            if name.startswith("subprocess."):
                first = ast.unparse(node.args[0]) if node.args else ""
                if not first.startswith("[sys.executable"):
                    problems.append((node.lineno, name + "(" + first + ")"))
                if any(kw.arg == "shell" for kw in node.keywords):
                    problems.append((node.lineno, name + " shell="))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
