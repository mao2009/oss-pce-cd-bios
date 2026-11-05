"""Decision-only tests for .claude/hooks/guard_destructive.py.

The guard receives hook JSON and returns a verdict. Nothing in this module runs
rm, git clean, or any other destructive command; the static test at the bottom
enforces that by walking the AST of this file.
"""
import ast
import importlib.util
import json
import os
import re
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
    "rm -rf /mnt/c/home/loach",
    "rm -rf /home/loach/work",
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


class ProtectedRootTests(unittest.TestCase):
    """Protected roots: home, repository root, ancestors of protected paths, environment and local
    configuration, required mode (fail-closed), linked worktrees, and worktree removal.

    Temporary layouts with a synthetic home only. Nothing is deleted or written outside them.
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
        write_text(os.path.join(self.repo, "file.txt"), "x\n")
        self._registry(self.repo, {self.home, self.repo, self.owned, self.ok_wt,
                                   os.path.dirname(self.home), self.tmp})
        env = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home,
                                           "OSS_PCE_GUARD_PROTECTED": "",
                                           "OSS_PCE_GUARD_REQUIRE_LOCAL": ""})
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

    def test_home_repository_and_parent_removal_denied(self):
        cmds = ["git worktree remove " + self.home, "rm -rf " + self.home, "rm " + self.home,
                "git worktree remove " + self.repo, "rm -rf " + self.repo, "rm " + self.repo,
                "git worktree remove " + os.path.dirname(self.home), "git worktree remove " + self.tmp]
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_environment_and_local_config_add_protection(self):
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")
        with mock.patch.dict(os.environ, {"OSS_PCE_GUARD_PROTECTED": self.owned}):
            self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self._local(json.dumps({"protected": [self.owned]}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self._local(json.dumps({"protected": []}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")

    def test_linked_worktree_uses_main_checkout_config_and_cannot_weaken_it(self):
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
        write_text(os.path.join(linked, ".claude", "guard-local.json"), json.dumps({"protected": []}))
        self.assertEqual(verdict(cmd, repo_root=linked), "DENY")

    def test_broken_local_config_and_unresolvable_worktree_fail_closed(self):
        for content in ("{bad", "[]", json.dumps({"protected": "x"}), json.dumps({"protected": ["relative/x"]})):
            self._local(content)
            with self.subTest(content=content):
                self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")
        broken = os.path.join(self.tmp, "linked-broken")
        write_text(os.path.join(broken, ".git"), "not a gitdir line\n")
        self.assertEqual(verdict("git status", repo_root=broken), "DENY")

    def test_required_mode_matrix(self):
        def required():
            return mock.patch.dict(os.environ, {"OSS_PCE_GUARD_REQUIRE_LOCAL": "1"})
        with required():
            self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")  # no config
        self._local("{bad")
        with required():
            self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")  # invalid JSON
        self._local(json.dumps({"protected": [self.owned]}))
        with required():
            self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")  # no required list
        self._local(json.dumps({"protected": [self.owned], "required": [self.ok_wt]}))
        with required():
            self.assertEqual(verdict("git status", repo_root=self.repo), "DENY")  # required not configured
        self._local(json.dumps({"protected": [self.owned], "required": [self.owned]}))
        with required():
            self.assertEqual(verdict("git status", repo_root=self.repo), "ALLOW")
            self.assertEqual(verdict("git worktree remove " + self.owned, repo_root=self.repo), "DENY")

    def test_worktree_removal_cases(self):
        self.assertEqual(verdict("git worktree remove " + self.ok_wt, repo_root=self.repo), "ALLOW")
        self.assertEqual(verdict("git worktree remove " + os.path.join(self.tmp, "unregistered"),
                                 repo_root=self.repo), "DENY")
        self.assertEqual(verdict("git worktree remove --force " + self.ok_wt, repo_root=self.repo), "DENY")

    def test_symlink_routes_denied(self):
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

    def test_dotdot_aliases_of_protected_roots_denied(self):
        # Registered alias spellings: the ".." must be normalised before the MSYS drive mapping.
        self._registry(self.repo, {"/abc/../mnt/c/users", "/home/../mnt/c/users", "/tmp/../mnt/c/users",
                                   "/a/..", "/a/../home", "/a/../mnt/c/users"})
        for cmd in ["git worktree remove /abc/../mnt/c/users", "git worktree remove /home/../mnt/c/users",
                    "git worktree remove /tmp/../mnt/c/users",
                    "git worktree remove /a/..", "git worktree remove /a/../home",
                    "git worktree remove /a/../mnt/c/users"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        # Host-independent normalisation check: the alias and its plain form canonicalise to the same key.
        # No protected-set membership or existing path is assumed (see test_protected_home_and_its_aliases).
        self.assertEqual(guard.canon("/abc/../c/users/prague"), guard.canon("/c/users/prague"))
        self.assertEqual(guard.canon("/a/../home"), guard.canon("/home"))
        # Excess ".." above a drive root must keep the drive letter (regression of the previous fix).
        for cmd in ["rm C:/../README.md", "rm C:\\..\\README.md"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_protected_home_and_its_aliases(self):
        # The temporary HOME is the protected root here. Its own path and ".." aliases of it must be refused,
        # wherever the temporary directory is (the test does not depend on the host's real home).
        aliases = [self.home,
                   os.path.join(self.home, "..", os.path.basename(self.home)),
                   os.path.join(self.tmp, "home", ".", "synthetic-owner")]
        self._registry(self.repo, set(aliases))
        for cmd_path in aliases:
            with self.subTest(path=cmd_path):
                self.assertEqual(verdict("git worktree remove " + cmd_path, repo_root=self.repo), "DENY")

    def test_synthetic_drive_form_home_and_aliases(self):
        # A synthetic home in drive form that does not exist on any host: the drive form, the MSYS form, the
        # /mnt form and ".." aliases must all be refused, with no dependence on the real home or on existence.
        synthetic = "C:/Users/synthetic-owner"
        with mock.patch.dict(os.environ, {"HOME": synthetic, "USERPROFILE": synthetic}):
            # key() is the casefolded canonical form that the guard compares with.
            self.assertEqual(guard.key("/abc/../c/users/synthetic-owner"), guard.key(synthetic))
            self.assertEqual(guard.key("/c/Users/synthetic-owner"), guard.key(synthetic))
            aliases = [synthetic, "/c/Users/synthetic-owner", "/mnt/c/users/synthetic-owner",
                       "/abc/../c/users/synthetic-owner", "/abc/../mnt/c/users/synthetic-owner",
                       "C:/Users/x/../synthetic-owner"]
            self._registry(self.repo, set(aliases))
            for path in aliases:
                with self.subTest(path=path):
                    self.assertEqual(verdict("git worktree remove " + path, repo_root=self.repo), "DENY")

    def test_normal_operations_allowed(self):
        for cmd in ["rm " + os.path.join(self.repo, "file.txt"), "git status",
                    "git worktree remove " + self.ok_wt, "make test"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, repo_root=self.repo), "ALLOW")


class SymlinkResolutionTests(unittest.TestCase):
    """Piece A: a symlink followed by ".." resolves to its real target, and a link reached through ".." is a link.

    The symlink cases need symlink creation. Where it is not permitted they SKIP; a SKIP is not a PASS and
    must be read as not run. The canon cases need no symlink and run everywhere.
    """

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = os.path.realpath(td.name)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, ".claude"))
        write_text(os.path.join(self.repo, "file.txt"), "x\n")
        self.outdir = os.path.join(self.tmp, "outdir")
        write_text(os.path.join(self.outdir, "placeholder"), "p\n")
        write_text(os.path.join(self.tmp, "outer.txt"), "outside\n")

    def _symlink(self, target, link, is_dir):
        try:
            os.symlink(target, link, target_is_directory=is_dir)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation not permitted here: " + type(exc).__name__)

    def _decide(self, cmd):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.repo})
        return guard.decide(payload, repo_root=self.repo)

    def test_dotdot_through_symlink_reaches_real_target(self):
        link_dir = os.path.join(self.repo, "link-dir")
        self._symlink(self.outdir, link_dir, True)
        # link-dir/.. is the real parent of outdir (the temporary directory), so outer.txt is outside the repo.
        verdict, reason = self._decide("rm " + os.path.join(link_dir, "..", "outer.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-outside-repo"), reason)

    def test_link_entry_reached_through_dotdot_is_refused(self):
        link_dir = os.path.join(self.repo, "link-dir")
        self._symlink(self.outdir, link_dir, True)
        inner = os.path.join(self.outdir, "inner-link")
        self._symlink(os.path.join(self.repo, "file.txt"), inner, False)
        # inner-link is a symlink whose target is inside the repo. The command reaches it through "..".
        verdict, reason = self._decide("rm " + os.path.join(link_dir, "..", "outdir", "inner-link"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-symlink"), reason)

    def test_ordinary_in_repo_removal_still_allowed(self):
        verdict, _ = self._decide("rm " + os.path.join(self.repo, "file.txt"))
        self.assertEqual(verdict, "ALLOW")

    def test_canon_without_normalisation_keeps_dotdot(self):
        self.assertEqual(guard.canon("/home/x/../y", normalize=False), "/home/x/../y")

    def test_canon_default_still_normalises(self):
        self.assertEqual(guard.canon("/home/x/../y"), "/home/y")


class DotdotRmTests(unittest.TestCase):
    """rm with a ".." component. A path with a real ".." component that does not resolve at hook time is refused
    (rm-unresolvable-dotdot). A path with ".." that resolves is judged by its real target. An absent plain path,
    or a ".." inside a file name, is not refused by this rule. Expectations are host-independent except the
    dangling-symlink test, which SKIPs where symlinks cannot be created."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = os.path.realpath(td.name)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, ".claude"))
        os.makedirs(os.path.join(self.repo, "sub"))
        write_text(os.path.join(self.repo, "file.txt"), "x\n")
        write_text(os.path.join(self.repo, "file..txt"), "x\n")
        write_text(os.path.join(self.repo, "sub", "inner.txt"), "x\n")
        write_text(os.path.join(self.tmp, "outer.txt"), "outside\n")

    def _decide(self, cmd):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.repo})
        return guard.decide(payload, repo_root=self.repo)

    def test_absent_path_with_dotdot_is_refused(self):
        verdict, reason = self._decide("rm " + os.path.join(self.repo, "nosuch", "..", "file.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_absent_plain_path_is_not_refused_by_the_dotdot_rule(self):
        verdict, reason = self._decide("rm " + os.path.join(self.repo, "not-there.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertFalse(reason.startswith("rm-unresolvable-dotdot"), reason)
        self.assertTrue(reason.startswith("rm:"), reason)

    def test_existing_dotdot_inside_the_repo_is_allowed(self):
        self.assertEqual(self._decide("rm " + os.path.join(self.repo, "sub", "..", "file.txt"))[0], "ALLOW")

    def test_relative_existing_dotdot_inside_the_repo_is_allowed(self):
        self.assertEqual(self._decide("rm sub/../file.txt")[0], "ALLOW")

    def test_relative_absent_dotdot_is_refused(self):
        verdict, reason = self._decide("rm sub/../nope.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_dotdot_inside_a_file_name_is_not_a_component(self):
        self.assertEqual(self._decide("rm " + os.path.join(self.repo, "file..txt"))[0], "ALLOW")
        verdict, reason = self._decide("rm " + os.path.join(self.repo, "nope..txt"))
        self.assertEqual(verdict, "DENY")
        self.assertFalse(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_existing_dotdot_that_leaves_the_repo_is_denied_by_its_real_target(self):
        verdict, reason = self._decide("rm " + os.path.join(self.repo, "..", "outer.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-outside-repo"), reason)
        verdict, reason = self._decide("rm ../outer.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-outside-repo"), reason)

    def test_quoted_existing_dotdot_path_is_judged_like_the_unquoted_one(self):
        self.assertEqual(self._decide('rm "' + os.path.join(self.repo, "sub", "..", "file.txt") + '"')[0], "ALLOW")

    def test_shell_variable_in_the_path_is_refused(self):
        verdict, reason = self._decide("rm $HOME/../file.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("unexpanded"), reason)

    def test_windows_drive_form_absent_dotdot_is_refused(self):
        verdict, reason = self._decide("rm C:/nosuch-volume/../file.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_windows_unc_form_with_dotdot_is_refused(self):
        verdict, reason = self._decide(r"rm \\nosuch-server\share\..\file.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_dotdot_escape_toward_a_protected_home_is_refused(self):
        verdict, reason = self._decide("rm " + os.path.join(self.repo, "sub", "..", "..", "home", "victim.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_worktree_remove_through_dotdot_still_denied_for_the_root(self):
        self.assertEqual(self._decide("git worktree remove " + os.path.join(self.repo, "sub", ".."))[0], "DENY")

    def test_dangling_symlink_entries_are_refused(self):
        dangle = os.path.join(self.repo, "dangle")
        try:
            os.symlink(os.path.join(self.tmp, "nowhere"), dangle)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation not permitted here: " + type(exc).__name__)
        verdict, reason = self._decide("rm " + dangle)
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-symlink"), reason)
        verdict, reason = self._decide("rm " + os.path.join(dangle, "..", "x"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)
        # The file exists, so only the unresolvable-dotdot rule refuses this (the mutant allows it).
        verdict, reason = self._decide("rm " + os.path.join(dangle, "..", "file.txt"))
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)


class PlatformPathFormTests(unittest.TestCase):
    """MSYS (/c/...) and /mnt (/mnt/c/...) forms, and an unmapped alias (/cygdrive/...).

    On Windows the mapped native path is judged: an in-repo ".." is allowed and an outside target is refused.
    On other hosts these forms are not native paths, so they are refused, never guessed to be safe. An unmapped
    alias is refused on every host."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = os.path.realpath(td.name)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, ".claude"))
        os.makedirs(os.path.join(self.repo, "sub"))
        write_text(os.path.join(self.repo, "file.txt"), "x\n")
        write_text(os.path.join(self.tmp, "outer.txt"), "outside\n")

    def _decide(self, cmd):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.repo})
        return guard.decide(payload, repo_root=self.repo)

    def test_msys_and_mnt_forms_follow_the_native_path(self):
        if os.name == "nt":
            drive, rest = os.path.splitdrive(self.repo)
            native = rest.replace("\\", "/")
            forms = ["/" + drive[0].lower() + native, "/mnt/" + drive[0].lower() + native]
            inside_ok = True
        else:
            forms = ["/t" + self.repo, "/mnt/t" + self.repo]
            inside_ok = False
        for base in forms:
            with self.subTest(form=base):
                verdict, reason = self._decide("rm " + base + "/sub/../file.txt")
                self.assertEqual(verdict, "ALLOW" if inside_ok else "DENY", reason)
                if not inside_ok:
                    self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)
                verdict, reason = self._decide("rm " + base + "/../outer.txt")
                self.assertEqual(verdict, "DENY", reason)
                self.assertTrue(reason.startswith("rm-outside-repo" if inside_ok else "rm-unresolvable-dotdot"),
                                reason)

    def test_unmapped_alias_is_refused_on_every_host(self):
        rest = os.path.splitdrive(self.repo)[1].replace("\\", "/")
        verdict, reason = self._decide("rm /cygdrive/c" + rest + "/sub/../file.txt")
        self.assertEqual(verdict, "DENY")
        self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)

    def test_bare_drive_alias_before_dotdot_is_refused_on_every_host(self):
        # Git Bash resolves /c/.. to /, so a bare alias followed by .. is refused, never judged as the drive root.
        for cmd in ["rm /c/../Users/x/file.txt", "rm /mnt/c/../Users/x/file.txt", "rm /cygdrive/c/../file.txt",
                    "rm /c//../Users/x/file.txt", "rm /c/./../Users/x/file.txt",
                    "rm /c/Users/../../Users/x/file.txt"]:
            with self.subTest(cmd=cmd):
                verdict, reason = self._decide(cmd)
                self.assertEqual(verdict, "DENY")
                self.assertTrue(reason.startswith("rm-unresolvable-dotdot"), reason)


class LauncherClassTests(unittest.TestCase):
    """Issue #639 X3a: command-position and launcher classification. Decision-level only; nothing is executed.
    Option grammars (X3b), wrapper and shell grammars (X3c) and interpreter or tool forms (X3d) are not tested here."""

    def test_launch_classes(self):
        cases = {"echo": "NONE", "sort": "OPTION", "rg": "OPTION", "diff": "FIXED_HELPER", "cd": "CD",
                 "env": "WRAPPER", "sh": "SHELL", "eval": "SHELL", "perl": "INTERP", "python3": "PY", "py": "PY",
                 "git": "GIT", "npm": "PKG", "rm": "DANGEROUS", "remove-item": "DANGEROUS", "mytool": "UNKNOWN"}
        for name, cls in cases.items():
            with self.subTest(name=name):
                self.assertEqual(guard.launch_class(name), cls)

    def test_name_status(self):
        cases = {"rm": "KNOWN_DANGEROUS", "echo": "KNOWN_SAFE", "diff": "KNOWN_SAFE", "cd": "KNOWN_SAFE",
                 "mytool": "UNKNOWN_CHECKED", "sh": "FORM_DEPENDENT", "git": "FORM_DEPENDENT", "sort": "FORM_DEPENDENT"}
        for name, status in cases.items():
            with self.subTest(name=name):
                self.assertEqual(guard.name_status(name), status)

    def test_posix_escaped_absolute_paths_resolve_to_the_shell(self):
        # Git Bash expansion (checked with printf): most of these words are /bin/sh; \\/bin/s\h and +/bin/s\h keep
        # a leading backslash or a + and are not an exact /bin/sh. All are DENY: the guard fails closed on them.
        for cmd in ["\\/bin/s\\h -c 'rm -rf /'", "\\\\/bin/s\\h -c 'rm -rf /'", "/bin/s\\h -c 'rm -rf /'",
                    "\"/bin/s\"\\h -c 'rm -rf /'", "'/bin/s'\\h -c 'rm -rf /'", "/bin/\\s\\h -c 'rm -rf /'",
                    "+/bin/s\\h -c 'rm -rf /'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_backslash_name_without_a_path_is_refused(self):
        for cmd in ["tools\\rm -rf /", ".\\rm.exe -rf /", "\\\\server\\share\\rm.exe -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_fused_function_name_is_denied(self):
        for cmd, tool in [("function f{rm -rf /}", "PowerShell"), ("function f{rm -rf /;}", "Bash"),
                          ("function f(){ rm -rf /; }", "Bash"), ("function f\\{ rm -rf /; }", "Bash")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_escaped_parenthesis_fused_name_is_denied(self):
        bs = chr(92)
        self.assertEqual(verdict("function f" + bs + "(rm -rf /" + bs + ")"), "DENY")

    def test_ordinary_function_definitions_are_allowed(self):
        for cmd in ["function f { echo hi; }; f", "f() { echo hi; }; f"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_exec_doas_sudo_hand_off_to_the_wrapped_command(self):
        for cmd in ["exec rm -rf /", "doas rm -rf /", "sudo rm -rf /", "exec sh -c 'rm -rf /'",
                    "sudo sh -c 'rm -rf /'", "doas sh -c 'rm -rf /'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_function_body_is_checked(self):
        self.assertEqual(verdict("function f { rm -rf /; }; f"), "DENY")
        self.assertEqual(verdict("function f { echo hi; }; f"), "ALLOW")

    def test_function_without_a_name_is_denied(self):
        self.assertEqual(verdict("function"), "DENY")

    def test_coproc_command_is_checked_in_both_readings(self):
        self.assertEqual(verdict("coproc rm -rf /"), "DENY")
        self.assertEqual(verdict("coproc NAME rm -rf /"), "DENY")
        self.assertEqual(verdict("coproc sleep 1"), "ALLOW")

    def test_coproc_without_a_command_is_denied(self):
        self.assertEqual(verdict("coproc"), "DENY")

    def test_absolute_path_with_shell_escape_is_resolved_to_its_command(self):
        self.assertEqual(verdict("/bin/s\\h -c 'rm -rf /'"), "DENY")

    def test_wrapper_handoff_still_checks_the_wrapped_command(self):
        # The expected wrapper list is written out here, not read from guard.WRAPPERS, so a removed entry is a failure.
        expected = ["builtin", "command", "doas", "env", "exec", "ionice", "nice", "nohup", "setsid", "stdbuf",
                    "sudo", "time", "timeout", "xargs"]
        self.assertEqual(sorted(guard.WRAPPERS), expected)
        # Every wrapper hands off: the destructive command and the shell form behind it are both DENY.
        for wrapper in expected:
            for cmd in [wrapper + " rm -rf /", wrapper + " sh -c 'rm -rf /'"]:
                with self.subTest(cmd=cmd):
                    self.assertEqual(verdict(cmd), "DENY")
        for cmd in ["nice -n 5 rm -rf /", "env -i rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_timeout_forms_are_checked(self):
        for cmd in ["timeout rm -rf /", "timeout 5 rm -rf /", "timeout 5 -s KILL rm -rf /",
                    "timeout -s KILL 5 rm -rf /", "timeout 5s sh -c 'rm -rf /'", "timeout -- 5 rm -rf /",
                    "timeout --signal KILL 5 rm -rf /", "timeout --kill-after 5 5 rm -rf /",
                    "timeout .5 rm -rf /", "timeout 1.5 rm -rf /", "timeout abc rm -rf /",
                    "timeout --kill 1 5 rm -rf /", "timeout --sig KILL 5 rm -rf /", "timeout --s KILL 5 rm -rf /",
                    "timeout -k5 rm -rf /", "timeout -s9 rm -rf /", "timeout 5 -- rm -rf /",
                    "timeout -k 1 5 rm -rf /", "timeout -- -s rm -rf /", "timeout -- -k 5 rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        self.assertEqual(verdict("timeout 5 echo hi"), "ALLOW")

    def test_timeout_ordinary_arguments_are_not_read_as_commands(self):
        for cmd in ["timeout 5 echo rm", "timeout 30 grep -rn rm docs", "timeout 5 rg -n sh src",
                    "timeout 5 -- echo hi", "timeout -k 1 5 echo hi", "timeout --kill-after=1 5 echo hi",
                    "timeout --foreground 5 echo hi", "timeout --kill 1 5 echo hi",
                    "timeout --foreground 5 echo rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_powershell_scriptblocks_and_keywords_are_checked(self):
        for cmd in ["FUNCTION f { Remove-Item -Recurse -Force / }; f", "try { Remove-Item -Recurse -Force / }",
                    "Get-Item / | ForEach-Object { Remove-Item -Recurse -Force $_ }",
                    "Invoke-Command { Remove-Item -Recurse -Force / }", "& { Remove-Item -Recurse -Force / }",
                    "Start-Job -ScriptBlock { Remove-Item -Recurse -Force / }"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_hashtable_values_and_launcher_arguments_are_checked(self):
        for cmd in ["Write-Output @{ a = Remove-Item -Recurse -Force / }", "@{ a = Remove-Item -Recurse -Force / }",
                    "Start-Process powershell -ArgumentList '-c Remove-Item -Recurse -Force /'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        self.assertEqual(verdict("@{ a = 'x'; b = 1 }", tool="PowerShell"), "ALLOW")
        self.assertEqual(verdict("Start-Process notepad file.txt", tool="PowerShell"), "ALLOW")

    def test_launcher_options_first_and_no_space_hashtable_forms_are_checked(self):
        for cmd in ["Start-Process -FilePath powershell -ArgumentList '-c Remove-Item -Recurse -Force /'",
                    "Start-Process -FilePath rm -ArgumentList '-rf /'",
                    "Start-Process -ArgumentList '-c Remove-Item -Recurse -Force /' -FilePath powershell",
                    "@{a=Remove-Item -Recurse -Force /}", "@{a =Remove-Item -Recurse -Force /}", "a = Remove-Item",
                    "ii powershell -Command Remove-Item -Recurse -Force /",
                    "icm powershell -Command Remove-Item -Recurse -Force /",
                    "start powershell -Command Remove-Item -Recurse -Force /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        self.assertEqual(verdict("@{ a = 'x'; b = 1 }", tool="PowerShell"), "ALLOW")

    def test_launcher_expression_syntax_and_aliases_are_denied(self):
        for cmd in ["Start-Process powershell -ArgumentList -c,Remove-Item,-Recurse,-Force,/",
                    "Start-Process powershell -ArgumentList ('-c Remove-Item -Recurse -Force /')",
                    "Start-Process powershell -ArgumentList @('-c','Remove-Item -Recurse -Force /')",
                    "saps powershell -Command Remove-Item -Recurse -Force /",
                    "Invoke-Item powershell -Command Remove-Item -Recurse -Force /",
                    "Start-Job powershell -Command Remove-Item -Recurse -Force /",
                    "sajb powershell -Command Remove-Item -Recurse -Force /",
                    "start-threadjob powershell -Command Remove-Item -Recurse -Force /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        self.assertEqual(verdict("Start-Process notepad file.txt", tool="PowerShell"), "ALLOW")

    def test_wmi_and_cim_process_launchers_are_checked(self):
        for cmd in ["Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList 'cmd /c rd /s /q C:\\x'",
                    "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='rm -rf /'}",
                    "saps powershell -ArgumentList -c,Remove-Item,-Recurse,-Force,/",
                    "start notepad @args"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_powershell_variables_in_scriptblocks_and_quoted_paths_are_allowed(self):
        for cmd in ["Get-Process | ForEach-Object { $_.Name }", "Get-ChildItem | ForEach-Object { $_.FullName }",
                    '"C:\\Program Files\\app.exe" foo']:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "ALLOW")

    def test_launcher_path_fragments_variables_and_blocks_are_checked(self):
        # A quoted path with a space is refused, fail closed: the launcher re-split reads its fragment as a command.
        for cmd, tool in [('Start-Process "C:\\Program Files\\app.exe"', "PowerShell"),
                          ('Start-Process "tools\\app.exe;rm -rf /"', "PowerShell"),
                          ('Start-Process ".\\x.exe;rm -rf /"', "PowerShell"),
                          ("Start-Process powershell -ArgumentList $x", "PowerShell"),
                          ("$RM -rf dir", "PowerShell"), (". $x", "PowerShell"), (". $x", "Bash"),
                          ("source $x", "Bash")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_powershell_method_calls_are_denied(self):
        for cmd in ["$x.Delete()", "$x[0].Delete()", "Get-Item / | ForEach-Object { $_.Delete() }",
                    "Get-ChildItem | % { $_.Delete() }"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        self.assertEqual(verdict("Get-Process | ForEach-Object { $_.Name }", tool="PowerShell"), "ALLOW")

    def test_quoted_parenthesised_and_named_member_calls_are_denied(self):
        for cmd in ["$x.'Delete'()", '$x.("Delete")()', "$x.Delete ()",
                    "Get-Item / | ForEach-Object { $_.'Delete'() }",
                    "Get-ChildItem | ForEach-Object -MemberName Delete"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_wmi_and_cim_aliases_are_launchers(self):
        for cmd in ["iwmi -Class Win32_Process -Name Create -ArgumentList 'cmd /c rd /s /q C:\\x'",
                    "icim -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='rm -rf /'}"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_scriptblock_hosts_with_expression_syntax_are_denied(self):
        for cmd in ["iwmi -Class Win32_Process -Name Create -ArgumentList $x",
                    "icim -ClassName Win32_Process -MethodName Create -Arguments $x",
                    "icm ('Remove-Item -Recurse -Force /')", "Invoke-Command -ScriptBlock $sb",
                    "Start-Job @('Remove-Item -Recurse -Force /')", "sajb -c,Remove-Item,-Recurse,-Force,/",
                    "start-threadjob ('Remove-Item -Recurse -Force /')", "Invoke-Command { Get-Item x }"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_member_abbreviation_and_non_identifier_hashtable_keys_are_checked(self):
        for cmd in ["Get-ChildItem | ForEach-Object -Mem Delete", "Get-ChildItem | ForEach-Object -Member Delete",
                    "@{ 'my key' = Remove-Item -Recurse -Force / }", "@{ my-key = Remove-Item -Recurse -Force / }",
                    '@{ "a b" = Remove-Item -Recurse -Force / }']:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        self.assertEqual(verdict("@{ 'my key' = 'x'; b = 1 }", tool="PowerShell"), "ALLOW")

    def test_bash_append_assignment_then_command_is_denied(self):
        for cmd in ["x+=1 rm -rf /", "a+=b rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        self.assertEqual(verdict("echo x+=1"), "ALLOW")

    def test_powershell_hashtable_keys_and_member_abbreviations_are_checked(self):
        for cmd in ["@{my-key=Remove-Item -Recurse -Force /}", "@{'my key'=Remove-Item -Recurse -Force /}",
                    "@{a-b=Remove-Item -Recurse -Force /}", "@{a.b=Remove-Item -Recurse -Force /}",
                    "Get-ChildItem | ForEach-Object -M Delete", "Get-ChildItem | ForEach-Object -Me Delete"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        for cmd in ["Write-Error -Message hi", "@{ 'my key' = 'x' }"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "ALLOW")

    def test_redirect_before_the_command_word_is_skipped_not_the_command(self):
        for cmd in [">out rm -rf /", "2>/dev/null rm -rf /", "< in rm -rf /", "x=1 >out rm -rf /",
                    "x+=1 >out rm -rf /", ">x sudo rm -rf /", ">x timeout 5 rm -rf /", "env >x rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        for cmd in ["echo hi >out", ">out echo hi", "> out echo hi", "2>/dev/null ls"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_process_launcher_alias_with_a_variable_argument_is_denied(self):
        for cmd in ["saps powershell -ArgumentList $x", "start powershell -ArgumentList $x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_redirections_anywhere_in_a_command_do_not_hide_it(self):
        for cmd in ["2>&1 rm -rf /", "1>&2 rm -rf /", ">&2 rm -rf /", ">| out rm -rf /", "&> out rm -rf /",
                    "<<< x rm -rf /", "sudo >x -u root rm -rf /", "env >x -i rm -rf /", "nice >x -n 5 rm -rf /",
                    "timeout >x 5 rm -rf /", "timeout 2>/dev/null 5 rm -rf /", "sudo 2>&1 -u root rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        for cmd in ["echo hi 2>&1", "ls 2>&1 | head -n 5", "make test 2>&1 | tail -n 3", "echo a && echo b",
                    "echo hi >&2", "echo a >out", "timeout 5 echo hi 2>&1"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")
        self.assertEqual(verdict("2>&1 Remove-Item -Recurse -Force /", tool="PowerShell"), "DENY")

    def test_glued_and_quoted_operators_are_marked_correctly(self):
        for cmd in ["rm>x -rf /", "rm&>x -rf /", "rm<x -rf /", "env -u '>' rm -rf /", "xargs -I '>' rm -rf /",
                    "coproc '>' rm -rf /", "sudo>x -u root rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        for cmd in ["echo '>' hi", "echo a>b", "echo x>out.txt", "'>' rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_operator_with_an_operator_as_its_target_is_denied(self):
        for cmd, tool in [("> 2>x rm -rf /", "Bash"), ("&> 2>x rm -rf /", "Bash"), ("echo a; > 2>x rm -rf /", "Bash"),
                          ("> 2>x Remove-Item -Recurse -Force /", "PowerShell")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_glued_digit_after_a_brace_or_comma_is_an_fd_number(self):
        for cmd, tool in [("ForEach-Object {2>x Remove-Item -Recurse -Force /}", "PowerShell"),
                          ("1,2>x Remove-Item -Recurse -Force /", "PowerShell"),
                          ("timeout -k5>x rm -rf /", "Bash")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_spaced_digits_before_a_redirect_are_arguments(self):
        for cmd in ["timeout -k 5 >x rm -rf /", "nice -n 5 >x rm -rf /", "sudo -u 5 >x rm -rf /",
                    "ionice -c 2 >x rm -rf /", "xargs -n 5 >x rm -rf /", "timeout -s 9 >x rm -rf /",
                    "sudo&>x -u root rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_powershell_name_value_after_a_comma_is_checked(self):
        for cmd in ["@{k=1, a=Remove-Item -Recurse -Force /}", "@{k = 1, a=Remove-Item -Recurse -Force /}",
                    "@{a = 1, b = Remove-Item -Recurse -Force /}", "@(1, a=Remove-Item -Recurse -Force /)",
                    "1, Remove-Item -Recurse -Force /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_if_with_a_command_in_its_segment_is_denied(self):
        self.assertEqual(verdict("if rm -rf /; then :; fi"), "DENY")

    def test_sourcing_a_path_with_a_variable_is_denied(self):
        for cmd, tool in [("source ./$x", "Bash"), (". ./$x", "Bash"), (". ./$x", "PowerShell")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_remaining_control_keywords_are_command_positions(self):
        for cmd in ["while true; do rm -rf /; done", "until false; do rm -rf /; done",
                    "while rm -rf /; do :; done", "until rm -rf /; do :; done",
                    "if true; then :; elif rm -rf /; then :; fi",
                    "if true; then echo hi; elif true; then rm -rf /; fi", "! rm -rf /", "fi rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_else_and_do_keywords_are_command_positions(self):
        self.assertEqual(verdict("if true; then echo hi; else rm -rf /; fi"), "DENY")
        self.assertEqual(verdict("for x in a; do rm -rf /; done"), "DENY")

    def test_launcher_argument_list_is_capped(self):
        self.assertEqual(verdict("Start-Process notepad " + " ".join(["x"] * 300), tool="PowerShell"), "DENY")

    def test_then_keyword_is_a_command_position(self):
        self.assertEqual(verdict("if true; then rm -rf /; fi"), "DENY")

    def test_function_keyword_without_braces_is_checked(self):
        self.assertEqual(verdict("FUNCTION f Remove-Item -Recurse -Force /", tool="PowerShell"), "DENY")
        self.assertEqual(verdict("FUNCTION f rm -rf /"), "DENY")

    def test_brace_words_in_bash_stay_unexpanded(self):
        for cmd in ["{rm,-rf,/}", "r{m,} -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_unclosed_quote_in_the_first_powershell_piece_is_denied(self):
        self.assertEqual(verdict('"echo hi', tool="PowerShell"), "DENY")

    def test_quoted_powershell_names_are_the_same_command(self):
        for cmd in ['"Remove"-Item -Recurse -Force /', "'Remove'-Item -Recurse -Force /", 'r"m" -Recurse -Force /',
                    '   "Remove"-Item -Recurse -Force /', '\t"Remove"-Item -Recurse -Force /',
                    '"rm .exe" -rf /']:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_dot_names_keep_their_meaning(self):
        for cmd in [". $(echo hi)", '. "$(echo hi)"', "..exe $(echo hi)"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_rm_with_a_space_before_exe_is_denied(self):
        self.assertEqual(verdict("rm .exe -rf /", tool="PowerShell"), "DENY")

    def test_names_with_trailing_dots_or_spaces_are_the_command(self):
        for cmd, tool in [("rm. -rf /", "Bash"), ("RM.EXE. -rf /", "Bash"), ("rm\\  -rf /", "Bash"),
                          ("C:\\Windows\\System32\\rm. -rf /", "PowerShell")]:
            with self.subTest(cmd=cmd, tool=tool):
                self.assertEqual(verdict(cmd, tool=tool), "DENY")

    def test_ordinary_commands_and_out_of_scope_forms_are_unchanged(self):
        # pip install is X3d's conditional case: X3a does not change it, so it stays ALLOW here.
        for cmd in ["echo hi", "cat README.md | head -n 5", "ls -la", "git status --short", "sort -u names.txt",
                    "rg -n TODO src", "python3 -m unittest discover -s tests/host -v", "make test", "npm test",
                    "pip install -r requirements-dev.txt"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class LauncherMutationTests(unittest.TestCase):
    """Issue #639 X3a: each classification rule must be detected by the LauncherClassTests tests. Each mutant is a
    temporary copy of the hook and this test module in a temporary folder; no repository file is changed."""

    MUTANTS = {
        "function keyword handling removed": ('        if w0.lower() == "function":', '        if False:'),
        "function keyword case-sensitive": ('        if w0.lower() == "function":', '        if w0 == "function":'),
        "PowerShell launcher arguments not analysed": ('    if ps and name in PS_LAUNCH_NAMES:', '    if False:'),
        "PowerShell name=value rule removed": ('        if ps and len(words) > 1 and words[1].startswith("="):',
                                               '        if False:'),
        "PowerShell start alias removed": ('{"start-process", "start", "saps"', '{"start-process", "saps"'),
        "PowerShell ii alias removed": ('"invoke-item", "ii", "invoke-command"', '"invoke-item", "invoke-command"'),
        "then keyword removed": ('"if", "then", "else"', '"if", "else"'),
        "PowerShell variable value rule removed": ('        if ps and len(words) == 1 and w0.startswith("$") and not any(ch in w0 for ch in "*?[]{}`~"):',
                                                   '        if False:'),
        "saps removed from the expression launcher pattern": ('(start-process|start|saps|invoke-item|ii|',
                                                              '(start-process|start|invoke-item|ii|'),
        "@ removed from the expression syntax": ('EXPR_SYNTAX_RE = re.compile(r"[,()@{}$]")',
                                                 'EXPR_SYNTAX_RE = re.compile(r"[,(){}$]")'),
        "launcher argument cap removed": ('        if len(tokens) > LAUNCHER_MAX_WORDS:', '        if False:'),
        "PowerShell dollar removed from expression syntax": ('EXPR_SYNTAX_RE = re.compile(r"[,()@{}$]")',
                                                             'EXPR_SYNTAX_RE = re.compile(r"[,()@{}]")'),
        "lone variable length condition removed": ('        if ps and len(words) == 1 and w0.startswith("$")',
                                                   '        if ps and w0.startswith("$")'),
        "else keyword removed": ('"then", "else", "elif"', '"then", "elif"'),
        "if keyword removed": ('"if", "then", "else"', '"then", "else"'),
        "quoted and parenthesised member rule removed": (r'''|['\"(])|-m(?:e|em\w*)?\b")''', r''')|-m(?:e|em\w*)?\b")'''),
        "member abbreviation rule removed": (r'''|-m(?:e|em\w*)?\b")''', r'''|-membernamex\b")'''),
        "Bash assignment with += not recognised": (r'''ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")''', r'''ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")'''),
        "PowerShell name=value rule for any key removed": ('        if ps and "=" in w0 and not w0.startswith("="):', '        if False:'),
        "icm removed from the expression launcher pattern": (r'''invoke-command|icm|start-job''', r'''invoke-command|start-job'''),
        "method call whitespace rule removed": (r'''(?:\w+\s*\(|['\"(])''', r'''(?:\w+\(|['\"(])'''),
        "iwmi removed from the expression launcher pattern": (r'''|iwmi|icim|''', r'''|icim|'''),
        "iwmi removed from the launcher names": ('"invoke-cimmethod", "iwmi", "icim"}', '"invoke-cimmethod"}'),
        "while keyword removed": ('"while", "until"', '"until"'),
        "until keyword removed": ('"while", "until"', '"while"'),
        "elif keyword removed": ('"then", "else", "elif"', '"then", "else"'),
        "fi keyword removed": ('"elif", "fi", "do"', '"elif", "do"'),
        "bang keyword removed": ('KEYWORDS = {"{", "}", "!",', 'KEYWORDS = {"{", "}",'),
        "PowerShell method call rule removed": ('    if ps and METHOD_CALL_RE.search(text):', '    if False:'),
        "sourcing any-variable rule removed": ('    if name in (".", "source") and any(("$" in a or "`" in a or SUBST in a) for a in args):',
                                               '    if False:'),
        "do keyword removed": ('"fi", "do", "done"', '"fi", "done"'),
        "PowerShell launcher expression rule removed": ('    if ps and PS_PROC_LAUNCH_RE.search(text) and EXPR_SYNTAX_RE.search(text):',
                                                        '    if False:'),
        "saps alias removed": ('"start", "saps", "invoke-item"', '"start", "invoke-item"'),
        "Invoke-Item alias removed": ('"saps", "invoke-item", "ii"', '"saps", "ii"'),
        "Start-Job alias removed": ('"icm", "start-job",', '"icm",'),
        "start-threadjob alias removed": ('"sajb", "start-threadjob", ', '"sajb", '),
        "brace and comma split applied to Bash too": ('(ps and c in "{},")', '(c in "{},")'),
        "PowerShell comma split removed": ('(ps and c in "{},")', '(ps and c in "{}")'),
        "redirections not stripped from argv": ('    words = strip_redirections(words)', '    pass'),
        "redirection operator not marked": ('            if c == ">":\n                rd.append(_redir_target(text, j))\n            cur.append(" " + REDIR_MARK + text[start:j] + " ")',
                                            '            if c == ">":\n                rd.append(_redir_target(text, j))\n            cur.append(text[start:j])'),
        "glued fd digits not taken into the operator": ('                del cur[-k:]', '                pass'),
        "operator target that is an operator not denied": ('    if i + 1 < len(words) and words[i + 1].startswith(REDIR_MARK):',
                                                           '    if False:'),
        "glued fd digit after a brace not taken": ('(i - k == 0 or text[i - k - 1] in " \\t\\n;&|(){},")',
                                                   '(i - k == 0 or text[i - k - 1] in " \\t\\n;&|(),")'),
        "glued fd digit after a comma not taken": ('(i - k == 0 or text[i - k - 1] in " \\t\\n;&|(){},")',
                                                   '(i - k == 0 or text[i - k - 1] in " \\t\\n;&|(){}")'),
        "glued fd digit only at the start of the text":('(i - k == 0 or text[i - k - 1] in " \\t\\n;&|(){},")',
                                                         '(i - k == 0 or text[i - k - 1] in "")'),
        "ampersand-greater marker branch removed": ('        if quote is None and c == "&" and i + 1 < n and text[i + 1] == ">":',
                                                    '        if False:'),
        "fd-dup or clobber character not taken into the operator": (
            '            if j < n and text[j] in ("&|" if c == ">" else "&"):',
            '            if False:'),
        "coproc handling removed": ('        if w0.lower() == "coproc":', '        if False:'),
        "PowerShell scriptblock braces not split": ('        if c in ";&|()\\n" or (ps and c in "{},"):', '        if c in ";&|()\\n":'),
        "coproc NAME reading removed": ('            if len(rest) >= 2:', '            if False:'),
        "POSIX escape unescaping removed": ('            w0 = unescaped', '            pass'),
        "fused function name check removed": ('            if len(words) < 2 or any(ch in words[1] for ch in "{}()"):',
                                              '            if len(words) < 2:'),
        "exec removed from wrappers": ('"builtin", "exec", "nohup"', '"builtin", "nohup"'),
        "doas removed from wrappers": ('"sudo", "doas", "env"', '"sudo", "env"'),
        "sudo removed from wrappers": ('WRAPPERS = {"sudo", ', 'WRAPPERS = {'),
        "wrapper handoff removed": ('        if handoff(name):', '        if False:'),
        "xargs removed from wrappers": ('"setsid", "stdbuf", "xargs"}', '"setsid", "stdbuf"}'),
        "stdbuf removed from wrappers": ('"timeout", "setsid", "stdbuf", "xargs"}', '"timeout", "setsid", "xargs"}'),
        "setsid removed from wrappers": ('"timeout", "setsid", "stdbuf"', '"timeout", "stdbuf"'),
        "ionice removed from wrappers": ('"nice", "ionice", "timeout"', '"nice", "timeout"'),
        "builtin removed from wrappers": ('"command", "builtin", "exec"', '"command", "exec"'),
        "timeout rule removed": ('        if name == "timeout":', '        if False:'),
        "trailing dot and space normalisation removed": ('        name = name.lstrip("=").rstrip(". ") or name', '        pass'),
        "timeout attached short value taken as next word": ('takes_next = j == len(a) - 1', 'takes_next = True'),
        "PowerShell head ignores a leading tab": ('            if m:\n                head', '            if m and not seg.startswith("\\t"):\n                head'),
        "timeout long value consumes no extra word":('i += 2 if len(hits) == 1 and hits[0] in TIMEOUT_LONG_VALUE else 1',
                                                      'i += 2'),
        "timeout option-like suffixes removed": ('        if c and c[0].startswith("-"):', '        if False:'),
        "timeout long option prefixes removed":('            hits = [o for o in TIMEOUT_LONG if o.startswith(a[2:])] if "=" not in a else []',
                                                 '            hits = []'),
        "PowerShell quoted command name not joined": ('                head = [m.group(1).replace(\'"\', "").replace("\'", "")]',
                                                     '                head = [m.group(1)]'),
        "table: sort moved to NONE": ('("OPTION", "sort rg find', '("NONE", "sort rg find'),
        "table: rm not dangerous": ('("DANGEROUS", "rm"),', '("NONE", "rm"),'),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.LauncherClassTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_launcher_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass LauncherClassTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("LauncherClassTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a LauncherClassTests test: " + name + "\n"
                                + "\n".join(failing))


class X3bProgramOptionTests(unittest.TestCase):
    """Issue #640 X3b: program-naming options. Decision-level only; nothing is executed. A program named by an option
    is an execution form the guard does not analyse, so it is DENY (UNKNOWN_EXECUTION)."""

    DENY = [
        # sort, rg: long options, separate value, abbreviation, quoting
        "sort --compress-program=sh payload.txt", "sort -S 1K --compress-program=bash p.txt",
        "sort --compress-program sh payload.txt", "sort --compress=sh payload.txt",
        "rg --pre=sh x y", "rg --pre sh -n x src", "rg --pre-glob '*.x' x src", "rg --pre='sh' x src",
        # tar: long, short cluster, attached value, checkpoint action
        "tar --to-command='sh -c \"rm -rf x\"' -xf a.tar", "tar -I sh -xf a.tar", "tar -Ish -xf a.tar",
        "tar -xIsh a.tar", "tar --use-compress-program=sh -xf a.tar", "tar --checkpoint-action=exec=sh -xf a.tar",
        # rsync: -e in a cluster, long options
        "rsync -e 'sh -c \"rm -rf x\"' a b", "rsync -avze ssh a b", "rsync --rsh=sh a b", "rsync --rsync-path=sh a b", "rsync --rsync=sh a b", "rsync --rem=sh a b",
        # ssh family: -o keys, attached and clustered forms, -S
        "ssh -o ProxyCommand='sh -c \"rm -rf x\"' host", "ssh -oProxyCommand=sh host", "ssh -vo ProxyCommand=sh host",
        "ssh -o LocalCommand=sh host", "ssh -o PermitLocalCommand=yes host", "ssh -o proxycommand=sh host",
        "scp -S sh a b", "sftp -S sh host",
        # find: -exec runs its command through the common entry; -delete
        "find . -exec sh -c 'rm -rf x' ;", "find . -exec rm -rf / ;", "find . -delete",
        # option position: after a positional word
        "rg TODO --pre sh src",
        # found in review: tar -F / info and new-volume scripts, the 11-char checkpoint prefix, ssh config keys, sftp -D
        "tar -F sh a.tar", "tar -Fsh a.tar", "tar --info-script=sh -xf a.tar", "tar --new-volume-script=sh -xf a.tar",
        "tar --checkpoint-=exec=sh -xf a.tar", "ssh -o KnownHostsCommand=sh host", "ssh -o PKCS11Provider=x host",
        "ssh -o 'Match exec \"sh\"' host", "sftp -D sh host",
        # found in review 2: tar remote-archive commands, GNU/BSD name variants, environment prefix assignments
        "tar --rsh-command=sh -xf a.tar", "tar --rsh-command sh -xf a.tar", "tar --rmt-command=sh -xf a.tar",
        "tar --rmt=sh -xf a.tar", "tar --rsh=sh -xf a.tar", "gsort --compress-program=sh x",
        "gtar --to-command=sh -xf a.tar", "gtar -Fsh a.tar", "bsdtar --to-command=sh -xf a.tar",
        "RSYNC_RSH=sh rsync a b", "env RSYNC_RSH=sh rsync a b", "SSH_ASKPASS=sh ssh host",
        "TAR_OPTIONS=--to-command=sh tar -xf a.tar",
        # found in review 3: export and env -S and shell strings, computed option names, backslash escapes,
        # tar checkpoint case, ssh -F and Include
        "export RSYNC_RSH=sh && rsync a b", "env -S 'RSYNC_RSH=sh rsync a b'", "sh -c 'RSYNC_RSH=sh rsync a b'",
        "P=compress-program; sort --$P=sh x", "sort --$(echo compress-program)=sh x",
        "sort --compress\\-program=sh x", "tar --to\\-command=sh -xf a.tar",
        "tar --checkpoint-action=EXEC=sh -xf a.tar", "ssh -F evil.conf host", "ssh -o Include=/tmp/x host",
        "scp -F x a b", "sftp -F x host",
        # found in review 4: escapes and expansions in option tokens and values, export with an escaped name,
        # old-style tar option clusters
        "ssh -o Proxy\\Command=sh host", "tar --checkpoint-action e\\xec=sh -xf a.tar", "ssh -o $X host",
        "ssh -o \"$X\" host", "export RSYNC\\_RSH=sh && rsync a b", "tar I sh -xf a.tar", "tar Ixf sh a.tar",
        # found in review 5: brace expansion and globs produce the option name or value after the shell expands them
        "sort --{compress-program=sh,} x", "sort --compress-program{=sh,} x", "ssh -o{Proxy,}Command=sh host",
        "ssh -o Proxy{Command=sh,} host", "tar x{I,} sh a.tar", "tar {I,}sh a.tar",
        "tar --checkpoint-action={exec,}=sh -xf a.tar", "export {RSYNC_RSH,X}=sh && rsync a b",
        "X=RSYNC_RSH; export $X=sh && rsync a b", "ssh -* host", "sort --compress*=sh x", "tar x* a.tar",
        # found in review 9: a leading glob that expands to a dashed name; XAuthLocation; config-file variables
        "sort [-]-compress-program=sh x", "sort *-compress-program=sh x", "ssh [-]o ProxyCommand=sh host",
        "tar -xf a.tar [-]-to-command=sh", "ssh -o XAuthLocation=sh host", "ssh -X -o XAuthLocation=sh host",
        "RIPGREP_CONFIG_PATH=cfg.txt rg x src", "RSYNC_CONNECT_PROG=sh rsync rsync://h/m d",
        # found in review 7: += appends, indirect settings (printf -v, read, export without =), globs in -o values
        "export RSYNC_RSH+=sh && rsync a b", "SSH_ASKPASS+=sh ssh host", "printf -v RSYNC_RSH sh && export RSYNC_RSH && rsync a b",
        "read RSYNC_RSH <<< sh; export RSYNC_RSH; rsync a b", "ssh -o *roxyCommand=sh host",
        "export $X=y", "export {A,B}=y",
        # found in review 10: Windows environment names are case-insensitive
        "rsync_rsh=sh rsync a b", "Rsync_Rsh=sh rsync a b", "env rsync_rsh=sh rsync a b", "ssh_askpass=sh ssh host",
        "tar_options=--to-command=sh tar -xf a.tar",
        # found in review 11: a variable name assembled from an expansion; sftp -b batch file
        "A=RSYN; B=C_RSH=sh; export $A$B && rsync a b", "A=RSYN; B=C_RSH; printf -v \"$A$B\" sh && rsync a b",
        "A=RSYN; B=C_RSH=sh; declare $A$B", "sftp -b batch.txt host",
        # found in review 12: an attached -v option on printf; rsync remote options (-M, --remote-option)
        "A=RSYN; B=C_RSH=sh; \\export $A$B && rsync a b", "A=RSYN; B=C_RSH; set -a; \\printf -v$A$B sh && rsync a b",
        "A=RSYN; B=C_RSH; set -a; printf -v$A$B sh && rsync a b", "rsync -M--rsync-path=sh a b",
        "rsync --remote-option=--rsync-path=sh a b",
        # found in review 8: rg --hostname-bin runs the named executable (rg --help); ssh SSH_SK_HELPER names a helper
        "rg --hostname-bin=sh x src", "rg --hostname-bin sh x src", "SSH_SK_HELPER=sh ssh host",
        "ssh -o Proxy*=sh host", "ssh -o ProxyCom*=sh host",
        # found in review 6: a leading = (zsh path expansion) names the same program
        "=sort --compress-program=sh x", "=tar -I sh -xf a.tar", "=rsync -e sh a b", "=ssh -o ProxyCommand=sh host",
    ]
    ALLOW = [
        "sort -u names.txt", "sort -o out.txt names.txt", "sort --check names.txt",
        "rg -n TODO src", "rg --pretty TODO src", "rg --no-config x",
        "find . -name '*.py'", "find . -exec grep -l x {} ;",
        "tar -xf a.tar", "tar --checkpoint=100 -xf a.tar", "tar --checkpoint-action=echo=x -xf a.tar",
        "rsync -a src/ dst/", "rsync --delete -a a b",
        "ssh host", "ssh -o BatchMode=yes host", "ssh -p 22 host",
        "diff -l a b", "file -z a.gz",
        "tar xf a.tar", "echo $HOME", "echo x\\y",
        "sort -u names*.txt", "rg -n TODO src/*.py", "echo {a,b}", "rg --hyperlink-format=default x src",
        "printf %s $HOME",
    ]

    def test_program_naming_options_are_denied(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_ordinary_uses_of_the_same_tools_are_allowed(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class X3bMutationTests(unittest.TestCase):
    """Issue #640 X3b: each program-option rule must be detected by a named X3bProgramOptionTests failure. Each mutant is
    a temporary copy of the hook and this test module; no repository file is changed."""

    MUTANTS = {
        "sftp -D rule removed": ('"sftp": "SDFb"', '"sftp": "SFb"'),
        "GNU and BSD name aliases removed": ('    name = PROGRAM_ALIAS.get(name, name)', '    pass'),
        "program-naming environment variables not checked": (r'''    if any(v in w.replace("\\", "").replace('"', "").replace("'", "").upper() for w in words for v in PROGRAM_ENV):''',
                                                             r'''    if False:'''),
        "ssh -o value glob not denied": (r'''                    if any(c in value for c in "*?["):''', r'''                    if False:'''),
        "expansion or escape in a tool argument not denied": (r'''        if "$" in a or "`" in a or SUBST in a or "\\" in a or "{" in a or "}" in a:''',
                                                              r'''        if False:'''),
        "brace in a tool argument not denied": (r'''or "\\" in a or "{" in a or "}" in a:''', r'''or "\\" in a:'''),
        "dashed or leading glob in a tool argument not denied": (r'''        if any(c in a for c in "*?[") and (a.startswith("-") or a[:1] in "*?["):''',
                                                      r'''        if False:'''),
        "tar first word glob not denied": (r'''    if name == "tar" and args and any(c in args[0] for c in "*?["):''',
                                           r'''    if False:'''),
        "assignment with an expansion not denied": (r'''    if any("=" in w and ("{" in w or "}" in w or "$" in w) for w in words):''',
                                                    r'''    if False:'''),
        "tool filter removed": ('    if name not in PROGRAM_TOOLS:', '    if False:'),
        "SSH_SK_HELPER removed from the program variables": ('"TAR_OPTIONS", "SSH_SK_HELPER",', '"TAR_OPTIONS",'),
        "RIPGREP_CONFIG_PATH removed from the program variables": ('"SSH_SK_HELPER", "RIPGREP_CONFIG_PATH",', '"SSH_SK_HELPER",'),
        "RSYNC_CONNECT_PROG removed from the program variables": ('"RIPGREP_CONFIG_PATH", "RSYNC_CONNECT_PROG",', '"RIPGREP_CONFIG_PATH",'),
        "XAuthLocation key removed": ('"knownhostscommand", "xauthlocation",', '"knownhostscommand",'),
        "variable set by a builtin with an expansion not denied": (
            r'''    if setter and any("$" in w or "`" in w or SUBST in w for w in words):''', r'''    if False:'''),
        "sftp -b batch file not denied": ('"sftp": "SDFb"', '"sftp": "SDF"'),
        "escaped setter builtin not unescaped": ('    plain = [w.replace("\\\\", "") for w in words]', '    plain = list(words)'),
        "printf -v attached to an expansion not denied": (
            'or ("printf" in plain and any(w.startswith("-v") for w in plain))', 'or False'),
        "leading equals not stripped from tool names": ('name = name.lstrip("=").rstrip(". ") or name',
                                                       'name = name.rstrip(". ") or name'),
        "environment name normalisation removed": (r'''w.replace("\\", "").replace('"', "").replace("'", "").upper() for w in words''',
                                                   r'''w.upper() for w in words'''),
        "environment name case folding removed": (r'''.replace("'", "").upper() for w in words''',
                                                  r'''.replace("'", "") for w in words'''),
        "ssh -F rule removed": ('"ssh": "F", ', ''),
        "ssh Include key removed": ('SSH_LAUNCH_KEYS = ("include", ', 'SSH_LAUNCH_KEYS = ('),
        "ssh KnownHostsCommand key removed": ('"knownhostscommand",', ''),
        "scp and sftp -S rules removed": ('"scp": "SF", "sftp": "SDFb"', '"sftp": "DFb"'),
        "ssh -o rule removed": ('ssh_o = name in SSH_O_TOOLS', 'ssh_o = False'),
        "ssh ProxyCommand key removed": ('("include", "proxycommand"', '("include", "zzz"'),
        "program option check not called": ('    program_option_rule(name, args)\n', ''),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.X3bProgramOptionTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_program_option_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass X3bProgramOptionTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("X3bProgramOptionTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an X3bProgramOptionTests test: " + name + "\n"
                                + "\n".join(failing))


class AllowlistSortTests(unittest.TestCase):
    """Issue #640 X3b (structural allowlist), sort. Decision-level only; nothing is executed."""

    DENY = [
        "sort --compress-program=sh x", "sort --compress-program sh x", "sort --comp=sh x", "sort --co=sh x",
        "sort --c x", "sort -S 1K --compress-program=bash p", "sort -k1 --compress-program=sh x",
        "sort -t , x --compress-program=sh", "sort -uq x", "sort -x x", "sort --bogus x", "sort --check=bogus x",
    ]
    ALLOW = [
        "sort -u names.txt", "sort -o out.txt names.txt", "sort --check names.txt", "sort -C names.txt",
        "sort --check=quiet names.txt", "sort --sort=numeric x", "sort -k 2,2 -t , data.csv", "sort -S 1K -n x",
        "sort --version", "sort --help", "sort -o --compress-program=sh x", "sort -- --compress-program=sh x",
    ]

    def test_sort_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_sort_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class AllowlistMutationTests(unittest.TestCase):
    """Issue #640 X3b (allowlist), sort: each parser rule must be detected by a named AllowlistSortTests failure."""

    MUTANTS = {
        "compress-program allowed as a long argument": (
            '"long_args": ("random-source", ', '"long_args": ("compress-program", "random-source", '),
        "unlisted long option not denied": (
            '                raise Deny("program-option", name + " option is not in the allowlist: " + a)',
            '                pass'),
        "unlisted short letter skipped instead of denied": (
            '                raise Deny("program-option", name + " short option is not in the allowlist: " + a)',
            '                j += 1'),
        "cluster stops after the first flag letter": (
            '                    j += 1\n                    continue', '                    break'),
        "short value not consumed from the next word": (
            '                    if j == len(a) - 1:\n                        i += 1  # the value is the next word',
            '                    if False:\n                        i += 1  # the value is the next word'),
        "-- does not end the options": (
            '        if a == "--":\n            return', '        if a == "--":\n            pass'),
        "abbreviation resolved against the allowlist only": (
            '            hits = [o for o in spec["full_long"] if o == key] or [o for o in spec["full_long"] if o.startswith(key)]',
            '            hits = [o for o in spec["long_flags"] + spec["long_args"] if o.startswith(key)]'),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.AllowlistSortTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_allowlist_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass AllowlistSortTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistSortTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistSortTests test: " + name + "\n"
                                + "\n".join(failing))


class AllowlistRgTests(unittest.TestCase):
    """Issue #640 X3b (structural allowlist), rg. Decision-level only; nothing is executed."""

    DENY = [
        "rg --pre=sh x y", "rg --pre sh -n x src", "rg --pre-glob=x x src", "rg --hostname-bin=sh x src",
        "rg --hostname-bin sh x src", "rg --pr x", "rg --bogus x", "rg -nZ x", "rg -y x",
    ]
    ALLOW = [
        "rg -n TODO src", "rg --pretty TODO src", "rg --hyperlink-format=default x src", "rg -e --pre=sh x",
        "rg -- --pre=sh x", "rg -i -e foo src", "rg --no-config x", "rg -tpy foo src", "rg -A 2 foo src",
        "rg --count x src", "rg x src",
    ]

    def test_rg_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_rg_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class AllowlistRgMutationTests(unittest.TestCase):
    """Issue #640 X3b (allowlist), rg: each parser rule must be detected by a named AllowlistRgTests failure."""

    MUTANTS = {
        "pre allowed as a long argument": ('"long_args": ("hyperlink-format", "glob",', '"long_args": ("pre", "hyperlink-format", "glob",'),
        "unlisted long option not denied": (
            '                raise Deny("program-option", name + " option is not in the allowlist: " + a)', '                pass'),
        "cluster stops after the first flag letter": (
            '                    j += 1\n                    continue', '                    break'),
        "short value not consumed from the next word": (
            '                    if j == len(a) - 1:\n                        i += 1  # the value is the next word',
            '                    if False:\n                        i += 1  # the value is the next word'),
        "-- does not end the options": (
            '        if a == "--":\n            return', '        if a == "--":\n            pass'),
        "abbreviation resolved against the allowlist only": (
            '            hits = [o for o in spec["full_long"] if o == key] or [o for o in spec["full_long"] if o.startswith(key)]',
            '            hits = [o for o in spec["long_flags"] + spec["long_args"] if o.startswith(key)]'),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.AllowlistRgTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_rg_allowlist_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass AllowlistRgTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistRgTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistRgTests test: " + name + "\n"
                                + "\n".join(failing))


class AllowlistTarTests(unittest.TestCase):
    """Issue #640 X3b (structural allowlist), tar (GNU tar 1.35 grammar). Decision-level only; nothing is executed.
    The DENY list covers the normal, abnormal and boundary forms of the program-naming options; the ALLOW list covers
    ordinary archive commands and the boundary cases where an option value is consumed and names no program."""

    DENY = [
        # normal: the program-naming long options, by name and as a value
        "tar --to-command=sh -xf a.tar", "tar --use-compress-program=sh -xf a.tar", "tar --info-script=sh -xf a.tar",
        "tar --new-volume-script sh -xf a.tar", "tar --rsh-command=sh -xf a.tar", "tar --rmt-command=sh -xf a.tar",
        # normal: a short letter that names a program (-I, -F), alone or in a cluster
        "tar -I sh -xf a.tar", "tar -cIf sh a.tar", "tar -F sh a.tar",
        # abnormal: letters and options the grammar does not list, or a flag given a value
        "tar -y a.tar", "tar -xy a.tar", "tar --bogus -xf a.tar", "tar --to-stdout=x -xf a.tar",
        # abnormal: an abbreviation that is ambiguous or resolves to an unlisted option
        "tar --to -xf a.tar", "tar --f -xf a.tar", "tar --use=sh -xf a.tar", "tar --rm=sh -xf a.tar",
        # abnormal: --checkpoint-action with an exec value, as "=", as the next word, in any case
        "tar --checkpoint-action=exec=sh -xf a.tar", "tar --checkpoint-action exec=sh -xf a.tar",
        "tar --checkpoint-action=Exec=sh -xf a.tar", "tar --checkpoint-=exec=sh -xf a.tar",
        # boundary: options after a file name still count (GNU tar permutes options)
        "tar -xf a.tar -I sh", "tar a.tar --to-command=sh",
        # boundary: an old-style cluster is read as its dashed form
        "tar xIf sh a.tar", "tar Ixf sh a.tar",
        # the GNU and BSD names
        "gtar --to-command=sh -xf a.tar", "bsdtar -Fsh a.tar",
    ]
    ALLOW = [
        # normal archive commands
        "tar -xf a.tar", "tar -czf out.tgz src", "tar -tvf a.tar", "tar -C dir -xf a.tar", "tar --list --file=a.tar",
        "tar --strip-components=1 -xf a.tar", "tar --sort=name -cf a.tar src", "tar -T list.txt -cf a.tar",
        "tar -xf a.tar --no-same-owner", "tar --exclude-vcs -cf a.tar src", "tar --format=gnu -cf a.tar src",
        "gtar -xf a.tar", "bsdtar -xf a.tar",
        # the checkpoint forms that name no program
        "tar --checkpoint=100 -xf a.tar", "tar --checkpoint-action=echo=x -xf a.tar",
        "tar --checkpoint-action=echo=exec -xf a.tar",
        # boundary: an option value is consumed; the next word is a value, not an option
        "tar cfI sh a.tar", "tar -C -I -xf a.tar", "tar --file --to-command=sh -cf out.tar",
        # boundary: after --, every word is a file name
        "tar -xf a.tar -- -I",
        # old-style clusters
        "tar xf a.tar", "tar cf out.tar -C dir .", "tar cvf x.tar src",
        # a glob that does not start a word is a file name
        "tar -xf a.tar names*.txt",
    ]

    def test_tar_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_tar_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_tar_grammar_names_are_in_the_full_list(self):
        # An abbreviation is resolved against full_long, so every listed option must be in it.
        spec = guard.ALLOWLIST["tar"]
        listed = spec["long_flags"] + spec["long_args"] + spec["long_optional_any"] + tuple(spec["long_arg_deny_prefix"])
        for name in listed:
            with self.subTest(option=name):
                self.assertIn(name, spec["full_long"])


class AllowlistTarMutationTests(unittest.TestCase):
    """Issue #640 X3b (allowlist), tar: each rule must be detected by a named AllowlistTarTests failure."""

    MUTANTS = {
        "to-command allowed as a long argument": (
            '"long_args": ("add-file", "after-date",', '"long_args": ("to-command", "add-file", "after-date",'),
        "unlisted long option not denied": (
            '                raise Deny("program-option", name + " option is not in the allowlist: " + a)', '                pass'),
        "-I allowed as a short letter": ('"short_args": "bfCTXLHVKNg",', '"short_args": "bfCTXLHVKNgI",'),
        "-F allowed as a short letter": ('"short_args": "bfCTXLHVKNg",', '"short_args": "bfCTXLHVKNgF",'),
        "info-script allowed as a long argument": (
            '"long_args": ("add-file", "after-date",', '"long_args": ("info-script", "add-file", "after-date",'),
        "rsh-command allowed as a long argument": (
            '"long_args": ("add-file", "after-date",', '"long_args": ("rsh-command", "add-file", "after-date",'),
        "checkpoint-action exec value rule removed": (
            '"long_arg_deny_prefix": {"checkpoint-action": ("exec",)},', '"long_arg_deny_prefix": {},'),
        "checkpoint-action exec value matched case-sensitively": (
            'value.lower().startswith(p)', 'value.startswith(p)'),
        "old-style cluster not read as its dashed form": (
            '        args = ["-" + args[0]] + list(args[1:])', '        args = list(args)'),
        "long option value not consumed from the next word": (
            '                if not eq:\n                    i += 1  # the value is the next word\n                if any(',
            '                if False:\n                    i += 1  # the value is the next word\n                if any('),
        "optional checkpoint value allowed as a separate word": (
            '"long_optional_any": ("backup", "atime-preserve", "checkpoint", ',
            '"long_optional_any": ("backup", "atime-preserve", '),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.AllowlistTarTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_tar_allowlist_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass AllowlistTarTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistTarTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistTarTests test: " + name + "\n"
                                + "\n".join(failing))


class AllowlistRsyncTests(unittest.TestCase):
    """Issue #640 X3b (structural allowlist), rsync. The grammar comes from the rsync manual, not from running rsync
    (rsync is not installed on this host). Decision-level only; nothing is executed."""

    DENY = [
        # normal: the remote shell and the remote program, short and long, attached and separate
        "rsync -e 'sh -c \"rm -rf x\"' a b", "rsync -avze ssh a b", "rsync -esh a b", "rsync -e ssh a b",
        "rsync --rsh=sh a b", "rsync --rsh sh a b", "rsync --rsync-path=sh a b", "rsync --rsync-path sh a b",
        # normal: the remote-option forwarding (-M), attached and separate, and its long form
        "rsync -M--rsync-path=sh a b", "rsync -M sh a b", "rsync --remote-option=--rsync-path=sh a b",
        "rsync -avM sh a b",
        # abnormal: an abbreviation that is ambiguous or resolves to an unlisted program option
        "rsync --rs=sh a b", "rsync --rsync=sh a b", "rsync --rem=sh a b",
        # abnormal: unlisted letters and options, and options that feed or name daemon or user programs
        "rsync -Q a b", "rsync --bogus a b", "rsync --early-input=x a b", "rsync --copy-as=root a b",
        "rsync --daemon", "rsync --config=x", "rsync --dparam=x",
        # boundary: the program variables, in any case and as a prefix assignment
        "RSYNC_RSH=sh rsync a b", "env RSYNC_RSH=sh rsync a b", "RSYNC_SHELL=sh RSYNC_CONNECT_PROG=x rsync rsync://h/m d",
        "RSYNC_CONNECT_PROG=sh rsync rsync://h/m d", "rsync_shell=sh rsync rsync://h/m d",
        # boundary: a dashed glob or a brace in an option is not inspected
        "rsync -e* a b", "rsync --{rsh,x}=sh a b",
    ]
    ALLOW = [
        # normal sync commands
        "rsync -a src/ dst/", "rsync --delete -a a b", "rsync -n -v --progress src/ host:/dst/",
        "rsync --bwlimit=100 -a a b", "rsync -avh a b", "rsync -av --exclude=tmp src dst",
        "rsync -T /tmp/x -a a b", "rsync --timeout=30 --chmod=g+w -a a b", "rsync --delete-after --partial-dir=.p -a a b",
        "rsync -C -a a b", "rsync --files-from list.txt a b",
        # boundary: an option value is consumed; after -- every word is a file name
        "rsync --exclude tmp -a a b", "rsync -a -- -weird a b", "rsync -a -- -e sh a b",
    ]

    def test_rsync_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_rsync_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_rsync_grammar_names_are_in_the_full_list(self):
        spec = guard.ALLOWLIST["rsync"]
        listed = spec["long_flags"] + spec["long_args"]
        for name in listed:
            with self.subTest(option=name):
                self.assertIn(name, spec["full_long"])


class AllowlistRsyncMutationTests(unittest.TestCase):
    """Issue #640 X3b (allowlist), rsync: each rule must be detected by a named AllowlistRsyncTests failure."""

    MUTANTS = {
        "rsh allowed as a long argument": (
            '"long_args": ("backup-dir", "suffix", "chmod",', '"long_args": ("rsh", "backup-dir", "suffix", "chmod",'),
        "rsync-path allowed as a long argument": (
            '"long_args": ("backup-dir", "suffix", "chmod",', '"long_args": ("rsync-path", "backup-dir", "suffix", "chmod",'),
        "remote-option allowed as a long argument": (
            '"long_args": ("backup-dir", "suffix", "chmod",', '"long_args": ("remote-option", "backup-dir", "suffix", "chmod",'),
        "copy-as allowed as a long argument": (
            '"long_args": ("backup-dir", "suffix", "chmod",', '"long_args": ("copy-as", "backup-dir", "suffix", "chmod",'),
        "-e allowed as a short letter": ('"short_args": "Bf@T",', '"short_args": "Bf@Te",'),
        "-M allowed as a short letter": ('"short_args": "Bf@T",', '"short_args": "Bf@TM",'),
        "RSYNC_SHELL removed from the program variables": (
            '"RSYNC_CONNECT_PROG",\n               "RSYNC_SHELL")', '"RSYNC_CONNECT_PROG")'),
        "unlisted long option not denied": (
            '                raise Deny("program-option", name + " option is not in the allowlist: " + a)', '                pass'),
    }

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v",
                                   "test_claude_destructive_guard.AllowlistRsyncTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_rsync_allowlist_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass AllowlistRsyncTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistRsyncTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistRsyncTests test: " + name + "\n"
                                + "\n".join(failing))


class MutationDetectionTests(unittest.TestCase):
    """Linux CI (any host where symlinks can be created): the symlink tests must FAIL for each mutant of Piece A's
    rules. Each mutant is a temporary copy of the hook and this test module in a temporary folder. No repository
    file and no remote branch is changed. If symlinks cannot be created, the class SKIPs: a SKIP is not a PASS."""

    MUTANTS = {
        "raw-spelling realpath reverted": (
            "real = canon(os.path.realpath(raw_form)) if os.path.exists(raw_form) else lex",
            "real = canon(os.path.realpath(lex)) if os.path.exists(lex) else lex"),
        "entry-link check removed": (
            "if os.path.islink(lex) or entry_link:",
            "if os.path.islink(lex):"),
        "unresolvable-dotdot rule removed": (
            "if deny_unresolvable_dotdot and _dotdot_unresolved(p, raw_form):",
            "if False:"),
    }
    SYMLINK_TESTS = ["SymlinkResolutionTests", "DotdotRmTests", "ProtectedRootTests.test_symlink_routes_denied"]

    def _run(self, hook_text):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.realpath(td)
            hooks = os.path.join(root, ".claude", "hooks")
            host = os.path.join(root, "tests", "host")
            os.makedirs(hooks)
            os.makedirs(host)
            write_text(os.path.join(hooks, "guard_destructive.py"), hook_text)
            with open(THIS_FILE, encoding="utf-8") as f:
                write_text(os.path.join(host, "test_claude_destructive_guard.py"), f.read())
            names = ["test_claude_destructive_guard." + n for n in self.SYMLINK_TESTS]
            proc = subprocess.run([sys.executable, "-m", "unittest", "-v"] + names, cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    # Only failures of these symlink-specific tests count as detection; a failure elsewhere is not evidence.
    SYMLINK_FAILURE_TESTS = [
        "test_dangling_symlink_entries_are_refused",
        "test_dotdot_through_symlink_reaches_real_target",
        "test_link_entry_reached_through_dotdot_is_refused",
        "test_symlink_routes_denied",
    ]

    def test_symlink_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        if "skipped 'symlink" in out:
            self.skipTest("symlink creation not permitted here; mutation detection not run")
        self.assertEqual(code, 0, "unmutated copy must pass the symlink tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                names = [re.match(r"(?:FAIL|ERROR): (\S+) \(", ln) for ln in failing]
                detected = any(m and m.group(1) in self.SYMLINK_FAILURE_TESTS for m in names)
                self.assertTrue(detected, "mutant not detected by a symlink test: " + name + "\n" + "\n".join(failing))


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
