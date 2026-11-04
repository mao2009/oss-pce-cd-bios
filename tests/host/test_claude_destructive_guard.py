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
        "brace split applied to Bash too": ('(ps and c in "{}")', '(c in "{}")'),
        "coproc handling removed": ('        if w0.lower() == "coproc":', '        if False:'),
        "PowerShell scriptblock braces not split": ('        if c in ";&|()\\n" or (ps and c in "{}"):', '        if c in ";&|()\\n":'),
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
        "trailing dot and space normalisation removed": ('        name = name.rstrip(". ") or name', '        pass'),
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
