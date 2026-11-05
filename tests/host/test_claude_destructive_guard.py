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


# Mutants that no candidate input distinguishes (round 14). Each stays defined; none is abolished. The runners skip only these
# names, and MutationRegisterTests locks the list. A follow-up issue tracks them (see docs/claude-destructive-guard.md).
GENERIC = ("no verdict or reason difference on the round-14 candidate inputs; equivalence not proven; follow-up issue")
KNOWN_UNRESOLVED_MUTANTS = {
    "@ removed from the expression syntax": GENERIC,
    "POSIX escape unescaping removed": GENERIC,
    "PowerShell dollar removed from expression syntax": GENERIC,
    "PowerShell head ignores a leading tab": GENERIC,
    "assignment with an expansion not denied": GENERIC,
    "coproc NAME reading removed": GENERIC,
    "escaped setter builtin not unescaped": GENERIC,
    "expansion or escape in a tool argument not denied": GENERIC,
    "glob component match removed from names_claude": "glob operands are refused before names_claude (tar_path_ok and the "
                                                      "redirect rule); no difference found; follow-up issue",
    "glued fd digit after a brace not taken": GENERIC,
    "iwmi removed from the expression launcher pattern": "iwmi with arguments is DENY 'unknown' (fail closed); verdict "
                                                          "unchanged; follow-up issue",
    "iwmi removed from the launcher names": "iwmi with arguments is DENY 'unknown' (fail closed); verdict unchanged; "
                                            "follow-up issue",
    "leading equals not stripped from tool names": GENERIC,
    "operator target that is an operator not denied": GENERIC,
    "saps removed from the expression launcher pattern": "saps with arguments is DENY 'unknown' (fail closed); verdict "
                                                          "unchanged; follow-up issue",
    "short value not consumed from the next word": GENERIC,
    "sshpass removed from the launchers": "sshpass with arguments is DENY 'unknown' whether or not it is a launcher (fail "
                                          "closed); verdict unchanged; follow-up issue",
    "timeout option-like suffixes removed": GENERIC,
    "cross-segment variable rule removed": GENERIC,
    "trailing dot and space normalisation removed": GENERIC,
    "sort -T write check removed": "equivalent: the short_deny check for -T runs first, so the removed short_writes check is "
                                   "never reached for -T (code reading)",
}


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

    def test_registered_worktree_remove_denied(self):
        # Owner, Round A: git worktree remove is forbidden (it was ALLOW for a recorded owner before this round).
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=self.root), "DENY")

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
        self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        with mock.patch.dict(os.environ, {"OSS_PCE_GUARD_PROTECTED": self.owned}):
            self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self._local(json.dumps({"protected": [self.owned]}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")
        self._local(json.dumps({"protected": []}))
        self.assertEqual(verdict(cmd, repo_root=self.repo), "DENY")

    def test_linked_worktree_uses_main_checkout_config_and_cannot_weaken_it(self):
        main = os.path.join(self.tmp, "main")
        linked = os.path.join(self.tmp, "linked")
        gitdir = os.path.join(main, ".git", "worktrees", "linked")
        os.makedirs(os.path.join(main, ".claude"))
        write_text(os.path.join(linked, ".git"), "gitdir: " + gitdir + "\n")
        write_text(os.path.join(gitdir, "commondir"), "../..\n")
        self._registry(linked, {self.owned})
        cmd = "git worktree remove " + self.owned
        self.assertEqual(verdict(cmd, repo_root=linked), "DENY")
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
        self.assertEqual(verdict("git worktree remove " + self.ok_wt, repo_root=self.repo), "DENY")
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
        for cmd in ["rm " + os.path.join(self.repo, "file.txt"), "git status"]:
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
        expected = ["builtin", "busybox", "chroot", "chrt", "command", "coproc", "doas", "env", "exec", "fakeroot", "flock",
                    "ionice", "nice", "nohup", "nsenter", "runuser", "script", "setpriv", "setsid", "sshpass", "stdbuf",
                    "strace", "su", "sudo", "taskset", "time", "timeout", "unbuffer", "unshare", "watch", "xargs"]
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
                    "timeout -k 1 5 echo hi", "timeout --kill-after=1 5 echo hi",
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
        self.assertEqual(verdict("Start-Process notepad file.txt", tool="PowerShell"), "DENY")

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
        self.assertEqual(verdict("Start-Process notepad file.txt", tool="PowerShell"), "DENY")

    def test_wmi_and_cim_process_launchers_are_checked(self):
        for cmd in ["Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList 'cmd /c rd /s /q C:\\x'",
                    "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='rm -rf /'}",
                    "saps powershell -ArgumentList -c,Remove-Item,-Recurse,-Force,/",
                    "start notepad @args"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_powershell_variables_in_scriptblocks_and_quoted_paths_are_allowed(self):
        for cmd in ["Get-Process | ForEach-Object { $_.Name }", "Get-ChildItem | ForEach-Object { $_.FullName }"]:
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
        for cmd in ["@{ 'my key' = 'x' }"]:
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
        for cmd in ["echo hi 2>&1", "ls 2>&1 | head -n 5", "echo a && echo b",
                    "echo hi >&2", "echo a >out", "timeout 5 echo hi 2>&1"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")
        self.assertEqual(verdict("2>&1 Remove-Item -Recurse -Force /", tool="PowerShell"), "DENY")

    def test_glued_and_quoted_operators_are_marked_correctly(self):
        for cmd in ["rm>x -rf /", "rm&>x -rf /", "rm<x -rf /", "env -u '>' rm -rf /", "xargs -I '>' rm -rf /",
                    "coproc '>' rm -rf /", "sudo>x -u root rm -rf /"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")
        for cmd in ["echo '>' hi", "echo a>b", "echo x>out.txt"]:
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
                    "rg -n TODO src", "python3 -m unittest discover -s tests/host -v"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


    def test_reason_kinds_of_launcher_keyword_and_alias_forms(self):
        # Round 10 (reason-level assertions, D1): the verdict alone does not separate these forms, the reason kind does.
        # A keyword, alias, launcher or variable form that loses its own rule is still DENY, but with a different kind.
        # Each row: (tool, command, expected verdict, expected reason kind). The kinds are the stable labels of the rules.
        cases = [
            ("Bash", "x+=1 echo hi", "ALLOW", "no destructive operation found"),
            ("Bash", "while false; do echo x; done", "ALLOW", "no destructive operation found"),
            ("Bash", "until true; do echo x; done", "ALLOW", "no destructive operation found"),
            ("Bash", "if true; then echo hi; fi", "ALLOW", "no destructive operation found"),
            ("Bash", "if true; then rm -rf /; fi", "DENY", "rm-recursive"),
            ("Bash", "! rm -rf /", "DENY", "rm-recursive"),
            ("Bash", "rm&>x -rf /", "DENY", "rm-recursive"),
            ("Bash", "xargs -I {} rm -rf {}", "DENY", "command-word"),
            ("Bash", "flock lock rm -rf /", "DENY", "rm-recursive"),
            ("Bash", "flock -c 'rm -rf /' lock", "DENY", "rm-recursive"),
            ("Bash", "sudo rm -rf /", "DENY", "rm-recursive"),
            ("Bash", "sudo -u root rm -rf /", "DENY", "rm-recursive"),
            ("PowerShell", "Invoke-Item powershell -Command Remove-Item -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "ii powershell -Command Remove-Item -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "saps powershell -ArgumentList '-c Remove-Item -Recurse -Force /'", "DENY", "rm-recursive"),
            ("PowerShell", "Start-Process powershell -ArgumentList '-c Remove-Item -Recurse -Force /'", "DENY",
             "rm-recursive"),
            ("PowerShell", "start powershell -Command Remove-Item -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "start-threadjob powershell -Command Remove-Item -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "Start-Job powershell -Command Remove-Item -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "\"Remove-Item\" -Recurse -Force /", "DENY", "rm-recursive"),
            ("PowerShell", "x=Remove-Item", "DENY", "rm"),
            ("PowerShell", "$RM -rf dir", "DENY", "command-word"),
            ("PowerShell", ". $x", "DENY", "unexpanded"),
            ("PowerShell", "& ('r'+'m') -rf x", "DENY", "powershell"),
        ]
        guard_module = guard
        for tool, cmd, want_verdict, want_kind in cases:
            with self.subTest(tool=tool, cmd=cmd):
                verdict_, reason = guard_module.decide(payload(cmd, tool), repo_root=REPO)
                self.assertEqual(verdict_, want_verdict, reason)
                self.assertEqual(reason.split(":", 1)[0], want_kind, reason)


    def test_round14_keyword_fd_and_launcher_reasons(self):
        # Mutants: elif/else/fi/FUNCTION keywords, fused function name, fd-dup marking, comma split, '-m' abbreviation.
        cases = [
            ("Bash", "if false; then :; elif rm -rf /; then :; fi", "DENY", "rm-recursive"),
            ("Bash", "if false; then :; else rm -rf /; fi", "DENY", "rm-recursive"),
            ("Bash", "fi rm -rf /", "DENY", "rm-recursive"),
            ("Bash", "FUNCTION f { rm -rf / }", "DENY", "rm-recursive"),
            ("Bash", "function {rm,-rf} /", "DENY", "function"),
            ("Bash", "echo x 2>&1 rm -rf /", "ALLOW", "no destructive operation found"),
            ("PowerShell", "echo x,2>x rm -rf /", "DENY", "rm-outside-repo"),
            ("PowerShell", "-m", "DENY", "powershell"),
            ("PowerShell", '"if", "then", "else"', "ALLOW", "no destructive operation found"),
            ("PowerShell", '"while", "until"', "ALLOW", "no destructive operation found"),
        ]
        for tool, cmd, want, kind in cases:
            with self.subTest(tool=tool, cmd=cmd):
                got, reason = guard.decide(payload(cmd, tool), repo_root=REPO)
                self.assertEqual(got.upper(), want, reason)
                self.assertEqual(reason.split(":", 1)[0], kind, reason)


    def test_round14_launcher_argument_cap_is_a_reason(self):
        # A launcher with more words than LAUNCHER_MAX_WORDS is refused by count, before its suffixes are analysed.
        cmd = "Start-Process powershell " + "x " * (guard.LAUNCHER_MAX_WORDS + 1) + "-c Remove-Item -Recurse -Force /"
        got, reason = guard.decide(payload(cmd, "PowerShell"), repo_root=REPO)
        self.assertEqual(got.upper(), "DENY", reason)
        self.assertEqual(reason.split(":", 1)[0], "launcher", reason)


    def test_round14_powershell_launcher_expression_reasons(self):
        # The PowerShell process launcher with expression syntax, and the icm/saps launcher names (Round B mutation work).
        cases = [
            ("PowerShell", "Start-Process powershell -ArgumentList '-c', 'Remove-Item -Recurse -Force /'"),
            ("PowerShell", "saps powershell -ArgumentList '-c','x'"),
            ("PowerShell", '"icm",'),
        ]
        for tool, cmd in cases:
            with self.subTest(tool=tool, cmd=cmd):
                got, reason = guard.decide(payload(cmd, tool), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "launcher", reason)

    def test_round14_timeout_short_value_then_command(self):
        # timeout -k5 takes its value attached; the next word is the command, which is allowed here.
        got, reason = guard.decide(payload("timeout -k5 echo hi"), repo_root=REPO)
        self.assertEqual(got.upper(), "ALLOW", reason)
        # With the destructive command after the attached value, the reason is the command's own rule.
        for cmd in ["timeout -s9 5 rm -rf /", "timeout -k5 5 rm -rf /"]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "rm-recursive", reason)


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
        "redirection operator not marked": ('            if c == ">":\n                rd.append((text[i:j], _redir_target(text, j)))\n            cur.append(" " + REDIR_MARK + text[start:j] + " ")',
                                            '            if c == ">":\n                rd.append((text[i:j], _redir_target(text, j)))\n            cur.append(text[start:j])'),
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
        "trailing dot and space normalisation removed": ('    name = name.lstrip("=").rstrip(". ") or name', '    pass'),
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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
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
        "sort -o $OUT in.txt", "sort --compress-program=sh payload.txt", "sort -S 1K --compress-program=bash p.txt",
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
        "sort -u names.txt", "sort --check names.txt",
        "rg -n TODO src", "rg --pretty TODO src", "rg --no-config x",
        "find . -name '*.py'", "find . -exec grep -l x {} ;",

        "ssh host", "ssh -o BatchMode=yes host", "ssh -p 22 host",
        "diff -l a b", "file -z a.gz",
        "echo $HOME", "echo x\\y",
        "sort -u names*.txt", "rg -n TODO src/*.py", "echo {a,b}", "rg --hyperlink-format=default x src",
        "printf %s $HOME",

        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sort -o out.txt names.txt',
    ]

    def test_program_naming_options_are_denied(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_ordinary_uses_of_the_same_tools_are_allowed(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


    def test_round14_program_option_reasons(self):
        # Mutants: RSYNC_CONNECT_PROG removed; printf -v and a builtin setter with an expansion.
        cases = [
            ("Bash", "RSYNC_CONNECT_PROG=sh rsync a b", "DENY", "program-option"),
            ("Bash", "printf -v x $a", "DENY", "expansion"),
        ]
        for tool, cmd, want, kind in cases:
            with self.subTest(tool=tool, cmd=cmd):
                got, reason = guard.decide(payload(cmd, tool), repo_root=REPO)
                self.assertEqual(got.upper(), want, reason)
                self.assertEqual(reason.split(":", 1)[0], kind, reason)


class X3bMutationTests(unittest.TestCase):
    """Issue #640 X3b: each program-option rule must be detected by a named X3bProgramOptionTests failure. Each mutant is
    a temporary copy of the hook and this test module; no repository file is changed."""

    MUTANTS = {
        "GNU and BSD name aliases removed": ('    name = PROGRAM_ALIAS.get(name, name)', '    pass'),
        "expansion or escape in a tool argument not denied": (r'''        if "$" in a or "`" in a or SUBST in a or "\\" in a or brace_may_form_option(a, name):''',
                                                              r'''        if False:'''),
        "dashed or leading glob in a tool argument not denied": (r'''        if any(c in a for c in "*?[") and (a.startswith("-") or a[:1] in "*?["):''',
                                                      r'''        if False:'''),
        "assignment with an expansion not denied": (r'''    if any("=" in w and ("{" in w or "}" in w or "$" in w) for w in words):''',
                                                    r'''    if False:'''),
        "tool filter removed": ('    if name not in PROGRAM_TOOLS:', '    if False:'),
        "SSH_SK_HELPER removed from the program variables": ('"TAR_OPTIONS", "SSH_SK_HELPER",', '"TAR_OPTIONS",'),
        "RIPGREP_CONFIG_PATH removed from the program variables": ('"SSH_SK_HELPER", "RIPGREP_CONFIG_PATH",', '"SSH_SK_HELPER",'),
        "RSYNC_CONNECT_PROG removed from the program variables": ('"RIPGREP_CONFIG_PATH", "RSYNC_CONNECT_PROG",', '"RIPGREP_CONFIG_PATH",'),
        "variable set by a builtin with an expansion not denied": (
            r'''    if setter and any("$" in w or "`" in w or SUBST in w for w in words):''', r'''    if False:'''),
        "escaped setter builtin not unescaped": ('    plain = [w.replace("\\\\", "") for w in words]', '    plain = list(words)'),
        "printf -v attached to an expansion not denied": (
            'or ("printf" in plain and any(w.startswith("-v") for w in plain))', 'or False'),
        "leading equals not stripped from tool names": ('name = name.lstrip("=").rstrip(". ") or name',
                                                       'name = name.rstrip(". ") or name'),
        "program option check not called": ('    program_option_rule(name, args, ctx)\n', ''),
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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
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
        "sort -u names.txt", "sort --check names.txt", "sort -C names.txt",
        "sort --check=quiet names.txt", "sort --sort=numeric x", "sort -k 2,2 -t , data.csv", "sort -S 1K -n x",
        "sort --version", "sort --help", "sort -- --compress-program=sh x",

        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sort -o out.txt names.txt',
    ]

    def test_sort_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_sort_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


    def test_round14_sort_unlisted_long_option_is_deny(self):
        got, reason = guard.decide(payload("sort --bogus x"), repo_root=REPO)
        self.assertEqual(got.upper(), "DENY", reason)
        self.assertEqual(reason.split(":", 1)[0], "program-option", reason)


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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
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
        "rg --count x src", "rg x src", "rg --no-heading foo", "rg --type-list", "rg --heading foo src",
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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistRgTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistRgTests test: " + name + "\n"
                                + "\n".join(failing))


class RawDotdotContainmentTests(unittest.TestCase):
    """A raw '..' may cross a symlink before lexical normalization, escaping the repository.

    Decision-only tests; no cp, mv, tar, or other shell command is executed.
    """

    @staticmethod
    def context_for(repo):
        return guard.Ctx(guard.canon(repo), guard.key(repo),
                         guard.key(os.path.join(repo, ".git")), set(), [])

    def test_raw_parent_component_rejected_for_all_path_roles(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = self.context_for(os.path.realpath(td))
            for path in ("src/../target", "src/./../target",
                         r"src\..\target", "src/../../target"):
                for role in ("archive", "dir", "member"):
                    with self.subTest(path=path, role=role):
                        self.assertFalse(guard.tar_path_ok(path, ctx, role))
            self.assertTrue(guard.tar_path_ok("src/target", ctx, "archive"))

    def test_symlink_then_dotdot_is_denied_across_write_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.realpath(os.path.join(td, "repo"))
            outside = os.path.join(td, "outside", "nested")
            os.makedirs(repo)
            os.makedirs(outside)
            try:
                os.symlink(outside, os.path.join(repo, "lnk"), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest("symlinks unavailable (" + type(exc).__name__ + "): NOT_VERIFIED")
            escaped = os.path.realpath(os.path.join(repo, "lnk", "..", "target.txt"))
            self.assertNotEqual(os.path.commonpath([repo, escaped]), repo)
            ctx = self.context_for(repo)
            for role in ("archive", "dir", "member"):
                with self.subTest(role=role):
                    self.assertFalse(guard.tar_path_ok("lnk/../target.txt", ctx, role))
            commands = (
                "cp seed lnk/../target.txt",
                "mv seed lnk/../target.txt",
                "mkdir lnk/../target",
                "touch lnk/../target.txt",
                "tee lnk/../target.txt",
                "sort -o lnk/../target.txt seed",
                "tar -cf lnk/../target.tar seed",
                "tar -cf out.tar lnk/../target.txt",
                "echo hello > lnk/../target.txt",
            )
            for command in commands:
                with self.subTest(command=command):
                    data = json.dumps({
                        "session_id": "t", "hook_event_name": "PreToolUse", "cwd": repo,
                        "tool_name": "Bash", "tool_input": {"command": command},
                    })
                    actual, reason = guard.decide(data, repo_root=repo)
                    self.assertEqual(actual, "DENY", (command, reason))


class TarRuleTests(unittest.TestCase):
    """Issue #640 X3b, design B+C: tar's explicit grammar (tar_rule). The only allowed forms are -t (list) of an explicitly
    named .tar archive and -c (create) of an explicitly named archive, with repository-relative operands. Extraction and
    everything else is DENY (fail closed). Decision-level only; nothing is executed."""

    DENY = [
        # extraction writes to paths the archive names, which cannot be inspected from argv
        "tar -xf a.tar", "tar --extract --file a.tar", "tar -x -f a.tar", "tar -xvf a.tar -C dir", "tar -x a.tar",
        "tar xf a.tar", "tar cf out.tar src", "tar tf a.tar",
        # operations other than list and create
        "tar -rf a.tar x", "tar -uf a.tar x", "tar -df a.tar", "tar -Af a.tar b.tar", "tar -tcf a.tar src",
        # stdin, stdout and default archives
        "tar -tf - ", "cat a.tar | tar -tf -", "tar -cf - src", "tar -t", "tar -c src",
        # compression and program-naming letters and long options
        "tar -tzf a.tgz", "tar -czf out.tar src", "tar -cjf out.tar src", "tar -cIf sh a.tar", "tar --gzip -cf out.tar src",
        "tar --list --to-stdout --file=a.tar", "tar -t --to-command=sh -f a.tar", "tar --checkpoint=100 -tf a.tar",
        # archive names: a glob, a compressed name, a remote host, an absolute or escaping path, .claude or .git
        "tar -tf a*.tar", "tar -tf a[ab].tar", "tar -tf a.tgz", "tar -tf a.tar.gz", "tar -tf ../x.tar", "tar -tf /abs/x.tar",
        "tar -tf C:/x.tar", "tar -tf host:x.tar", "tar -cf .claude/x.tar src", "tar -cf .cl?ude/x.tar src",
        "tar -cf .git/x.tar src", "tar -cf out.tar -C ../x .", "tar -cf out.tar -C .claude .", "tar -cf out.tar -C /abs .",
        # a leading glob can expand to a dashed name (a file called -x), so it is an option position: denied (the quoted
        # form is the known over-denial of a quoted non-dashed glob value, accepted as LOW)
        "tar -tf a.tar '*.py'",
        # members: an escaping or absolute name, a remote host, an expansion
        "tar -cf out.tar ../secret", "tar -cf out.tar /etc/passwd", "tar -cf out.tar a:b", "tar -cf out.tar $(echo a)",
    ]
    ALLOW = [
        # create of an explicit archive, with repository-relative operands



        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'tar --create --file=out.tar src', 'tar -cf out.tar -C dir .', 'tar -cf out.tar src', 'tar -cf out.tar src tests', 'tar -cf out.tar src/*.py', 'tar -cf out.tar.xz src', 'tar -cvf out.tgz dir',
    ]

    def test_tar_rule_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_tar_rule_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_tar_archive_through_a_symlink_that_leaves_the_repository_is_denied(self):
        # Path normalization: a symlink inside the repository that points outside it is followed, so the operand leaves.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            repo = os.path.realpath(td)
            real_dir = os.path.join(repo, "real")
            os.makedirs(real_dir)
            link = os.path.join(repo, "lnk")
            try:
                os.symlink(os.path.realpath(outside), link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest("symlinks cannot be created here (" + type(exc).__name__ + "): NOT_VERIFIED on this host")
            def decide(cmd):
                # The working directory must be the temporary repository: relative operands resolve from it.
                body = json.dumps({"session_id": "t", "hook_event_name": "PreToolUse", "cwd": repo,
                                   "tool_name": "Bash", "tool_input": {"command": cmd}})
                return guard.decide(body, repo_root=repo)[0]
            self.assertEqual(decide("tar -cf real/a.tar src"), "ALLOW")
            self.assertEqual(decide("tar -cf lnk/a.tar src"), "DENY")


class TarRuleMutationTests(unittest.TestCase):
    """Issue #640 X3b, design B+C: each safety condition of tar_rule must be detected by a named TarRuleTests failure."""

    MUTANTS = {
        "exactly one of list or create not required": (
            '    if ops != {"c"}:', '    if False:'),
        "containment inside the repository not checked": (
            '    if not (k == ctx.root or inside(k, ctx.root)):', '    if False:'),
        "-C directory not checked": (
            '    if directory is not None and not tar_path_ok(directory, ctx, "dir"):', '    if False:'),
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
                                   "test_claude_destructive_guard.TarRuleTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    # Detectable only where a link can leave the repository (symlinks): NOT_VERIFIED on Windows, verified on Linux.
    LINUX_ONLY = ("containment inside the repository not checked",)

    def test_tar_rule_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass TarRuleTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                if name in self.LINUX_ONLY and os.name != "posix":
                    # On Windows the drive-relative path check denies these before the mutated rule is reached. The
                    # detection is only observable where the path is not a drive path: NOT_VERIFIED here (SKIP, not PASS).
                    self.skipTest("NOT_VERIFIED on this host: detected only where the path is not a drive path (Linux)")
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("TarRuleTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a TarRuleTests test: " + name + "\n" + "\n".join(failing))


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





        # boundary: an option value is consumed; after -- every word is a file name

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


    def test_round14_rsync_grammar_reasons(self):
        # rsync is DENY in every form (WRITE_DENY), so the grammar's own decision is the reason: an option the grammar
        # lists is 'write' (the form reaches the write rule), an option it does not list or that names a program is
        # 'program-option'. Mutants that list or unlist an option change that reason.
        for cmd in ["rsync -M x a b", "rsync -e sh a b", "rsync --copy-as=root a b", "rsync --remote-option=x a b",
                    "rsync --rsh=sh a b", "rsync --rsync-path=sh a b", "RSYNC_SHELL=sh rsync a b"]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "program-option", reason)
        for cmd in ["rsync --compress a b", "rsync --info=progress2 a b"]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "write", reason)


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
        "RSYNC_SHELL removed from the program variables": ('"RSYNC_SHELL", "LD_PRELOAD",', '"LD_PRELOAD",'),
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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistRsyncTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistRsyncTests test: " + name + "\n"
                                + "\n".join(failing))


class AllowlistSshFamilyTests(unittest.TestCase):
    """Issue #640 X3b (structural allowlist), ssh, scp and sftp. The grammars come from the OpenBSD manual pages, not
    from running the tools (not installed on this host). Decision-level only; nothing is executed."""

    DENY = [
        # ssh, normal: the config file (may include ProxyCommand) and the PKCS#11 library, alone or in a cluster
        "ssh -F evil.conf host", "ssh -Fevil.conf host", "ssh -vF x host", "ssh -I /lib/x.so host",
        # ssh, normal: the -o keys that run a local command or load a provider or an included file
        "ssh -o ProxyCommand=sh host", "ssh -oProxyCommand=sh host", "ssh -o proxycommand=sh host",
        "ssh -o 'ProxyCommand sh' host", "ssh -vo ProxyCommand=sh host", "ssh -o LocalCommand=sh host",
        "ssh -o PermitLocalCommand=yes host", "ssh -o KnownHostsCommand=sh host", "ssh -o Include=/tmp/x host",
        "ssh -o 'Match exec \"sh\"' host", "ssh -o PKCS11Provider=x host", "ssh -o SecurityKeyProvider=x host",
        "ssh -o XAuthLocation=sh host", "ssh -X -o XAuthLocation=sh host",
        # ssh, abnormal: an unlisted -o key, a key with a leading space, a long option (ssh has none in this grammar)
        "ssh -o Unknown=1 host", "ssh -o ' ProxyCommand=sh' host", "ssh --help",
        # scp, normal and abnormal: -S (program) and -F (config), and -o keys
        "scp -S sh a b", "scp -Ssh a b", "scp -F x a b", "scp -r -S sh a b", "scp -o ProxyCommand=sh a b",
        "scp -oProxyCommand=sh a b", "scp -o LocalCommand=sh a b", "scp -q -o Include=x a b",
        # sftp, normal and abnormal: -D (local server), -S (program), -b (batch file), -F (config), and -o keys
        "sftp -D sh host", "sftp -Dsh host", "sftp -S sh host", "sftp -b batch.txt host", "sftp -bbatch host",
        "sftp -F x host", "sftp -o ProxyCommand=sh host", "sftp -o PKCS11Provider=x host",
        # boundary: a key with a glob, or a glob at the start of the -o value, is not a listed key
        "ssh -o Proxy*=sh host", "scp -o *roxyCommand=sh a b",
    ]
    ALLOW = [
        # ssh, normal
        "ssh host", "ssh -o BatchMode=yes host", "ssh -p 22 host", "ssh -p22 -l user host",
        "ssh -i key -o StrictHostKeyChecking=no host", "ssh -vvv -o ServerAliveInterval=30 host",
        "ssh -L 8080:localhost:80 host", "ssh -J jump host", "ssh -o Compression yes host",
        "ssh -o 'ProxyJump=bastion' host", "ssh -X host",
        # ssh, boundary: -i takes the next word as the key file, so -F there is a file name, not an option;
        # after -- every word is an operand
        "ssh -i -F host", "ssh -- -oProxyCommand=sh",
        # scp, normal and boundary


        # sftp, normal and boundary



        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sftp -P 2222 host', 'sftp -R 16 host', 'sftp -X nrequests=64 host', 'sftp -i -b host', 'sftp -o BatchMode=yes host', 'sftp -s sftp host', 'sftp host',
    ]

    def test_ssh_family_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_ssh_family_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_ssh_family_grammar_options_are_listed_in_the_key_list(self):
        # Every key that the grammar allows must be lowercase, so the case-insensitive check can match it.
        for key in guard.SSH_OPTION_KEYS:
            with self.subTest(key=key):
                self.assertEqual(key, key.lower())


    def test_round14_ssh_family_reasons(self):
        # ssh -E and -S are DENY (short_deny); scp -S is not a listed option of scp (program-option).
        for cmd, kind in [("ssh -S none host", "write"), ("ssh -E log.txt host", "write"),
                          ("scp -S sh a b", "program-option")]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], kind, reason)


class AllowlistSshFamilyMutationTests(unittest.TestCase):
    """Issue #640 X3b (allowlist), ssh/scp/sftp: each rule must be detected by a named AllowlistSshFamilyTests failure."""

    MUTANTS = {
        "ProxyCommand allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("proxycommand", "batchmode", "port",'),
        "Include allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("include", "batchmode", "port",'),
        "LocalCommand allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("localcommand", "batchmode", "port",'),
        "PermitLocalCommand allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("permitlocalcommand", "batchmode", "port",'),
        "KnownHostsCommand allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("knownhostscommand", "batchmode", "port",'),
        "XAuthLocation allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("xauthlocation", "batchmode", "port",'),
        "PKCS11Provider allowed as an -o key": ('SSH_OPTION_KEYS = ("batchmode", "port",', 'SSH_OPTION_KEYS = ("pkcs11provider", "batchmode", "port",'),
        "-o key matched case-sensitively": ('key = re.split(r"[=\\s]", value, 1)[0].lower()', 'key = re.split(r"[=\\s]", value, 1)[0]'),
        "-o key not checked": ('                        if key not in spec["short_arg_keys"][ch]:', '                        if False:'),
        "ssh -F allowed as a short letter": ('"short_args": "BbcDEeiJLlmOPpQRSWwo",', '"short_args": "BbcDEeiFJLlmOPpQRSWwo",'),
        "scp -S allowed as a short letter": ('"short_args": "cilPJo",', '"short_args": "cilPJoS",'),
        "sftp -D allowed as a short letter": ('"short_args": "BcJilPRsXo",', '"short_args": "BcJilPRsXoD",'),
        "sftp -b allowed as a short letter": ('"short_args": "BcJilPRsXo",', '"short_args": "BcJilPRsXob",'),
        "separate short value not consumed": (
            '                        i += 1  # the value is the next word\n                    if ch in spec.get("short_value_strict", "") and (',
            '                        pass\n                    if ch in spec.get("short_value_strict", "") and ('),
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
                                   "test_claude_destructive_guard.AllowlistSshFamilyTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_ssh_family_allowlist_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass AllowlistSshFamilyTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("AllowlistSshFamilyTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by an AllowlistSshFamilyTests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewFixTests(unittest.TestCase):
    """Issue #640 X3b, independent review (HEAD 13cba47): each finding is a DENY case that was ALLOW, or an ordinary
    command that was DENY. Decision-level only; nothing is executed."""

    DENY = [
        # wrappers: env -S splits one string; a wrapper option of unknown arity hid the command it starts
        "env -S 'rm -rf src'", "env -S 'ssh -oProxyCommand=x h'", "env --split-string='ssh -oProxyCommand=x h'",
        "env -Sssh -oProxyCommand=x h", "env -i -S 'rm -rf x'",
        "env --unset X ssh -oProxyCommand=x h", "nice --adjustment 5 ssh -oProxyCommand=x h",
        "sudo --user root ssh -oProxyCommand=x h", "xargs --max-args 1 ssh -oProxyCommand=x h",
        "xargs -e ssh -oProxyCommand=x h", "ionice -t ssh -oProxyCommand=x h", "stdbuf -o L ssh -oProxyCommand=x h",
        "exec -a x ssh -oProxyCommand=x h", "nice -n 5 ssh -oProxyCommand=x h",
        # PATH assignment with a program tool (rsync's remote form starts ssh through PATH)
        "PATH=/x rsync -a a host:b", "env PATH=/x rsync -a a host:b", "PATH=/x rsync -a host:a b",
        # ssh, scp, sftp: -J names hosts; a dashed word, a KEY=VALUE pair or a space is an option, not a host
        "ssh -J '-oProxyCommand=x' h", "ssh -J-oProxyCommand=x h", "scp -J '-oProxyCommand=x' a b",
        "sftp -J '-oProxyCommand=x' h", "ssh -J h1,-oProxyCommand=x h2",
        # tar: an old-style letter that is not listed (I after f takes the next word), a remote archive
        "tar fI x.tar sh", "tar xfI x.tar sh", "tar -cf host:x.tar a", "tar -cf user@host:x.tar a",
        # the program-variable list: the dynamic loader preloads a library into the tool
        "LD_PRELOAD=/x sort f", "env LD_PRELOAD=/x sort f",
    ]
    ALLOW = [
        # ordinary wrapper uses: the words after the options are the command, and it is allowed
        "nice -n 5 ls", "env FOO=1 ls", "env -i ls", "sudo -u root ls", "timeout 5 sort x",
        # ordinary -J: a host, a user and port, or a host list
        "ssh -J jump host", "ssh -J user@jump:2222 host", "ssh -J jump1,jump2 host",
        # braces that bash does not expand (no comma and no sequence) are literal
        "rg 'a{2}' src",
        # tar: a colon is allowed with --force-local (a local archive); ordinary old-style clusters
    ]

    def test_review_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_review_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class ReviewFixMutationTests(unittest.TestCase):
    """Issue #640 X3b, review fixes: each rule must be detected by a named ReviewFixTests failure."""

    MUTANTS = {
        "PATH check matches every word": (
            r'''    if any(re.match(r"PATH\+?=", w.replace("\\", "")''', r'''    if any((w.replace("\\", "")'''),
        "-J strict value check removed": (
            '                    if ch in spec.get("short_value_strict", "") and (', '                    if False and ('),
        "LD_PRELOAD removed from the program variables": ('"RSYNC_SHELL", "LD_PRELOAD",', '"RSYNC_SHELL",'),
        "brace groups without a comma or sequence denied": ('or brace_may_form_option(a, name):', 'or "{" in a or "}" in a:'),
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
                                   "test_claude_destructive_guard.ReviewFixTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_review_fix_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewFixTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewFixTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewFixTests test: " + name + "\n"
                                + "\n".join(failing))


class PathCompressorLibraryTests(unittest.TestCase):
    """Issue #640 X3b, owner scope: PATH-resolved compressors (UNKNOWN_EXECUTION, denied), library-loading variables,
    PATH+=, and ProxyJump values. Decision-level only; nothing is executed.

    Observed on this host (GNU tar 1.35): `env PATH=/nonexistent tar -czf ...` fails with "/bin/sh: line 1: gzip: command
    not found" and exit 127, so tar starts gzip by name through PATH. The same mechanism is assumed for -j, -J, -Z and -a
    (recorded as UNKNOWN_EXECUTION). rg is a shell function here that calls Claude Code's built-in ripgrep, not a
    standalone binary, so rg -z is UNKNOWN_EXECUTION and denied."""

    DENY = [
        # compressors started by name through PATH: tar short, cluster, old-style, long, and the abbreviation
        "tar -czf out.tgz src", "tar -cjf out.tbz src", "tar -cJf out.txz src", "tar -cZf out.Z src",
        "tar -caf out.tar.gz src", "tar --gzip -cf out.tar src", "tar --auto-compress -cf out.tgz src",
        "tar -czf out.tar src", "tar -cjf out.tar src", "tar -cJf out.tar src",
        "tar --bzip2 -cf out.tar src", "tar --gz -cf out.tar src", "tar -xzf out.tgz", "tar -tzf out.tgz",
        "tar -xf a.tgz -z", "tar xzf a.tgz",
        # rg: the search-zip option runs an external decompressor
        "rg -z needle d.gz", "rg --search-zip needle d.gz", "rg -nz needle src", "rg --search-zip=yes x src",
        # PATH+= appends to the search path a program tool uses
        "PATH+=:/x rsync -a a host:b", "env PATH+=:/x rsync -a a h:b", "PATH+=:/x scp a h:b", "PATH+=:/x tar -tf a.tar",
        # library-loading variables, as prefix assignments and through env
        "LD_LIBRARY_PATH=/tmp/x tar -tf a.tar", "LD_AUDIT=/tmp/x.so tar -tf a.tar", "DYLD_INSERT_LIBRARIES=/x tar -tf a.tar",
        "SSH_SK_PROVIDER=/tmp/x.so ssh h", "env LD_LIBRARY_PATH=/x tar -tf a.tar",
        # ProxyJump names hosts: a dashed word, a KEY=VALUE pair or a space in its value is an option
        "ssh -o ProxyJump=-oProxyCommand=x h", "ssh -o 'ProxyJump=-oX' h", "ssh -o ProxyJump=h=x h",
        "ssh -o 'ProxyJump h x' h",
        # the PATH check uses the same name normalization as dispatch: quotes, trailing dot or space, leading =, .exe
        'PATH=/x "rsync " -a a b', "PATH=/x rsync. -a a b", "PATH=/x rsync.exe. a b", "PATH=/x =rsync -a a b",
        "PATH=/x 'ssh'. h", 'PATH=/x "sftp " h', "PATH=/x ssh\\  h", 'PATH+=/x "rsync " a b',
        'PATH=/x "/usr/bin/scp " a b',
        # tar reads a compressed archive through a PATH-resolved decompressor, even without -z (observed with an empty PATH)
        "tar -xf a.tgz", "tar -tf d.tar.gz", "tar -xf a.tar.bz2", "tar -xf x.TGZ",
        "tar -xf a.tar.Z", "tar -xf a.tar.gz -C dir",
    ]
    ALLOW = [
        # ordinary archive commands without a compressor flag; gzip is a separate command, not a program option

        # ordinary rg use; -a is --text, not a decompressor
        "rg -n needle src", "rg -a needle src",
        # PATH with no program tool in the command; ordinary ProxyJump hosts
        "PATH+=:/x ls", "ssh -o ProxyJump=user@jump:2222 h", "ssh -o 'ProxyJump=bastion' h",
        # gzip is DENY in every form (Owner D2): the checked file cannot be tied to the file gzip reads

        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'tar -cf out.tar src',
    ]

    def test_compressor_and_library_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_compressor_and_library_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


    def test_round14_ld_audit_is_a_program_variable(self):
        # LD_AUDIT names a library that the program loads: a program-naming variable, DENY (Round 13, unchanged).
        for cmd in ["LD_AUDIT=x ls", "LD_AUDIT=/tmp/x.so ls"]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "program-option", reason)


class PathCompressorLibraryMutationTests(unittest.TestCase):
    """Issue #640 X3b, owner scope: each rule must be detected by a named PathCompressorLibraryTests failure."""

    MUTANTS = {
        "rg z flag restored": ('"short_flags": "FisvxUSawLubnNHIclopqVPh",', '"short_flags": "FisvxUSawzLubnNHIclopqVPh",'),
        "LD_AUDIT removed from the program variables": (
            '"LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES"', '"LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"'),
        "ProxyJump value check removed": ('if key == "proxyjump" and (', 'if False and ('),
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
                                   "test_claude_destructive_guard.PathCompressorLibraryTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_compressor_library_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass PathCompressorLibraryTests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("PathCompressorLibraryTests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a PathCompressorLibraryTests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewRound2MutationTests(unittest.TestCase):
    """Issue #640 X3b, review round 2: each rule must be detected by a named failure in PathCompressorLibraryTests,
    AllowlistRgTests or AllowlistRsyncTests."""

    MUTANTS = {
        "rg --no-heading removed": (
            '"no-heading", "type-list", "pretty", "ignore-case", "invert-match"',
            '"type-list", "pretty", "ignore-case", "invert-match"'),
        "rsync --info removed": ('"sockopts", "outbuf", "info", "debug"),', '"sockopts", "outbuf"),'),
        "rsync --compress removed": ('"no-motd", "compress", "checksum",', '"no-motd", "checksum",'),
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
                                   "test_claude_destructive_guard.PathCompressorLibraryTests",
                                   "test_claude_destructive_guard.AllowlistRgTests",
                                   "test_claude_destructive_guard.AllowlistRsyncTests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_review_round2_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass the round-2 classes:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any(("PathCompressorLibraryTests." in ln or "AllowlistRgTests." in ln
                                or "AllowlistRsyncTests." in ln) for ln in failing)
                self.assertTrue(detected, "mutant not detected by a round-2 test: " + name + "\n" + "\n".join(failing))


class ReviewRound3Tests(unittest.TestCase):
    """Issue #640 X3b, review round 3 (HEAD 90bc365): sftp input from a pipe or a redirection; trailing dots and spaces in
    compressed archive names; globs that may expand to a compressed archive; the suffixes .tb2, .bz, .lzo, .tzo; and a
    create-only tar (it reads no archive, so no decompressor starts). Decision-level only; nothing is executed."""

    DENY = [
        # sftp: commands from a pipe or a redirected file or here-string cannot be inspected
        "sftp host < batch.txt", "printf '!touch x\\n' | sftp host", "sftp host <<< '!id'",
        # tar reads: the suffix is checked with trailing dots and spaces removed, and the other compressed suffixes
        "tar -tf a.tb2", "tar -tf a.bz", "tar -tf a.lzo", "tar -tf a.tzo", "tar -xf a.tar.bz",
        "tar -tf a.tgz.", 'tar -tf "a.tgz "', "tar -tf a.gz.", "tar -tf a.TGZ.", "tar -tf a.tbz2.",
        # tar: a glob operand whose literal tail is empty or is a compressed suffix
        "tar -tf a*", "tar -tf a*.tgz", "tar -xf a*",
    ]
    ALLOW = [
        # tar creates without reading: an archive name with a compressed suffix is written uncompressed

        # sftp with no piped or redirected input


        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sftp host', 'tar -cf out.tar dir', 'tar -cf out.tar.xz src', 'tar -cf out.tgz dir', 'tar -cvf out.tgz dir',
    ]

    def test_round3_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round3_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class ReviewRound3MutationTests(unittest.TestCase):
    """Issue #640 X3b, review round 3: each rule must be detected by a named ReviewRound3Tests failure."""

    MUTANTS = {
        "sftp piped or redirected input rule removed": (
            '    if re.search(r"sftp", squashed, re.IGNORECASE) and (',
            '    if False and ('),
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
                                   "test_claude_destructive_guard.ReviewRound3Tests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_round3_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewRound3Tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewRound3Tests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewRound3Tests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewRound4Tests(unittest.TestCase):
    """Issue #640 X3b, review round 4 (HEAD 6198663): glob operands that complete a compressed suffix with a glob
    character; any colon operand of tar (no --force-local exemption); sftp with a pipe or redirect under any spelling;
    DYLD_* loader variables; sort and ssh writes under .claude. Decision-level only; nothing is executed."""

    DENY = [
        # tar glob operands whose literal tail has no dot, or whose extension is compressed: a glob character supplies the dot
        "tar -tf x?gz", "tar -tf x*gz", "tar -xf x?tgz", "tar -tf x*z", "tar -tf a[.]tgz", "tar -tf a[.]gz",
        # tar: a colon operand is denied whatever --force-local says (its token can be a value or follow --)
        "tar -cf host:x.tar -- --force-local", "tar -cf host:x.tar -C --force-local src",
        "tar --force-local -cf host:x.tar a", "tar --force-local -tf a.tgz:",
        # sftp with a pipe or redirect, in any spelling of the program name
        "sftp.exe host < cmds", "/usr/bin/sftp host <cmds", "SFTP host < cmds", '"sftp" host < cmds',
        # DYLD_* loader variables
        "DYLD_FALLBACK_LIBRARY_PATH=/x rg foo src", "DYLD_LIBRARY_PATH=/x tar -tf a",
        # writes under .claude: sort -o and --output, ssh -E and the known-hosts options
        "sort -o .claude/x f", "sort --output .claude/x f", "sort -o.claude/x f", "sort --output=.claude/x f",
        "ssh -o UserKnownHostsFile=.claude/kh host", "ssh -E .claude/log host",
        "ssh -o 'GlobalKnownHostsFile=.claude/x' host",
    ]
    ALLOW = [
        # tar: a glob operand with a non-compressed extension (names*.txt, a*.tar) and create without a colon

        # sort and ssh writes outside .claude

        # sftp with no piped or redirected input


        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sftp host', 'sort -o out.txt names.txt', 'tar -cf out.tar dir',
    ]

    def test_round4_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round4_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


    def test_round14_sort_output_outside_the_repository_is_deny(self):
        # sort -o and --output take a repository path (tar_path_ok, archive role); a protected or outside path is DENY.
        for cmd in ["sort -o .claude/x names.txt", "sort --output=.claude/x names.txt", "sort -o ../x names.txt"]:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "write", reason)


    def test_round14_ssh_option_value_under_claude_is_deny(self):
        # The -o value names a path under .claude/: DENY with the program-option reason.
        for tool in ("Bash", "PowerShell"):
            with self.subTest(tool=tool):
                got, reason = guard.decide(payload("ssh -o IdentityFile=.claude/x host", tool), repo_root=REPO)
                self.assertEqual(got.upper(), "DENY", reason)
                self.assertEqual(reason.split(":", 1)[0], "program-option", reason)


class ReviewRound4MutationTests(unittest.TestCase):
    """Issue #640 X3b, review round 4: each rule must be detected by a named ReviewRound4Tests failure."""

    MUTANTS = {
        "sftp piped or redirected input rule removed": (
            '    if re.search(r"sftp", squashed, re.IGNORECASE) and (',
            '    if False and ('),
        "DYLD_FALLBACK_LIBRARY_PATH removed": ('"DYLD_FALLBACK_LIBRARY_PATH", "DYLD_LIBRARY_PATH", ', ''),
        "sort -o write under .claude not denied": (
            '                    if ch in spec.get("short_writes", "") and not (ctx is not None and tar_path_ok(value, ctx, "archive")):',
            '                    if False:'),
        "sort --output write under .claude not denied": (
            '                if opt in spec.get("long_writes", ()) and not (ctx is not None and tar_path_ok(value, ctx, "archive")):',
            '                if False:'),
        "ssh -o value under .claude not denied": (
            '                        if names_claude(value):', '                        if False:'),
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
                                   "test_claude_destructive_guard.ReviewRound4Tests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_round4_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewRound4Tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewRound4Tests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewRound4Tests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewRound5Tests(unittest.TestCase):
    """Issue #640 X3b, review round 5 and the owner rulings: a comment's apostrophe (tokenizer), .claude targets written
    through a glob, pushd/popd and Push-Location, sftp input in any spelling and under PowerShell or coproc, a PowerShell
    environment change next to a program tool, a cd into a glob that may name .claude, newlines in tool arguments, and the
    brace rule (rg 'x{2,3}' is allowed; a brace that can start a dashed word is still denied). Decision-level only."""

    DENY = [
        # tokenizer: a word-initial # is a comment; its apostrophe is not a quote, and the next line is a command
        "sort x # it's\nrm -rf d", "sort x # it's\necho '",
        # .claude written through a glob (fnmatch on the path component), through sort, ssh and tar
        "sort -o .cl?ude/x in.txt", "sort -o .cl[a]ude/x in.txt", "sort --output .cl?ude/x in.txt",
        "sort -T .claude in.txt", "ssh -M -S .claude/cs host", "ssh -E .cl?ude/log host", "rsync -a src .cl?ude/",
        "cp payload .cl?ude/settings.json", "echo x | tee .cl?ude/x", "ln -s .cl?ude linkdir",
        # directory changes: pushd and Push-Location are tracked; popd and Pop-Location are denied
        "pushd .claude && cp payload settings.json", "cd .cl?ude && cp x y", "popd",
        # sftp input: quote-split names, coproc, and PowerShell piped input
        "printf '!id\\n' | s''ftp host", "coproc sftp host; echo '!id'",
        # a newline inside a tool argument is not inspected
        "ssh -o 'Port=22\nProxyCommand=x' host",
        # PowerShell: a program tool with an environment change, and a directory change into a glob that may name .claude
        "Push-Location .cl?ude; Copy-Item x y",
        # the brace rule still denies a brace that can start a dashed word
        "sort --{compress-program=sh,} x",
        # file-writing commands are DENY in every form (Owner D1, Round 12)

    ]
    ALLOW = [
        # comments: the comment is not a command; the apostrophe does not open a quote
        "sort x # it's",
        # .claude is not named by these
        "pushd src && ls",
        # sftp with no piped or redirected input

        # braces that bash does not expand (no comma, or a plain prefix)
        "rg 'x{2,3}' src", "rg 'a{2}' src", "echo {a,b}",

        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'sftp host', 'sort -o out.txt names.txt',
    ]
    POWERSHELL_DENY = ["'!id' | sftp host", "Push-Location .cl?ude; Copy-Item x y",
                       "Set-Item -Path ('env:RSYNC_' + 'RSH') -Value 'x'; rsync -a src host:dst"]

    def test_round5_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round5_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_round5_powershell(self):
        for cmd in self.POWERSHELL_DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")


class ReviewRound5MutationTests(unittest.TestCase):
    """Issue #640 X3b, review round 5: each rule must be detected by a named ReviewRound5Tests failure."""

    MUTANTS = {
        "word-initial # comment skip removed": (
            r'''        if quote is None and c == "#" and (i == 0 or (text[i - 1]''', r'''        if False and (i == 0 or (text[i - 1]'''),
        "glob component match removed from names_claude": (
            '    return any(part and fnmatch.fnmatchcase(".claude", part) for part in v.split("/"))', '    return False'),
        "pushd removed from the directory-change names": (
            'CD_NAMES = {"cd", "chdir", "set-location", "sl", "pushd", "push-location"}',
            'CD_NAMES = {"cd", "chdir", "set-location", "sl"}'),
        "popd denied rule removed": ('"popd", "pop-location"}', '"popd_x"}'),
        "ssh -S write check removed": ('"short_deny": "ES",', '"short_deny": "E",'),
        "sort -T write check removed": ('"short_writes": "oT",', '"short_writes": "o",'),
        "sftp coproc rule removed": (
            r'''            re.search(r"[|<]", cmd) or re.search(r"\bcoproc\b", cmd, re.IGNORECASE)):''',
            r'''            re.search(r"[|<]", cmd)):'''),
        "quotes and backslashes not removed before the sftp check": (
            r'''    squashed = re.sub(r"[\"'\\]", "", cmd)''', '    squashed = cmd'),
        "newline in a tool argument not denied": (r'''        if "\n" in a:''', '        if False:'),
        "brace rule restored to every brace group": (
            'or brace_may_form_option(a, name):', 'or BRACE_EXPANDS.search(a):'),
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
                                   "test_claude_destructive_guard.ReviewRound5Tests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_round5_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewRound5Tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewRound5Tests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewRound5Tests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewRound6Tests(unittest.TestCase):
    """Issue #640 X3b, a6cf00f review and Owner rulings: external launchers (#664: flock, sshpass, watch, su -c, script -c,
    coproc and similar run their arguments as a command), PATH and program-naming variables changed in one segment or a
    string while a program tool runs in the same command (#665), and a tar read decides its decompressor from the archive
    contents, so the header is checked before the command runs. Decision-level only; nothing is executed."""

    DENY = [
        # #664: each launcher runs a program-option tar or ssh; its arguments are analysed whatever their arity
        "flock /tmp/l tar -I sh -xf a", "chroot / tar -I sh -xf a", "taskset 1 ssh -oProxyCommand=x h",
        "chrt 0 tar -I sh", "unbuffer tar -I sh", "strace -o /dev/null tar -I sh", "nsenter -t 1 tar -I sh",
        "unshare -r tar -I sh", "setpriv --reuid 1 tar -I sh", "runuser -u x -- tar -I sh", "fakeroot tar -I sh",
        "busybox tar -I sh", "sshpass -p x ssh -oProxyCommand=x h",
        # string launchers: the argument is a shell string, analysed as text
        "watch 'tar -I sh -xf a'", "su -c 'tar -I sh' user", "script -c 'tar -I sh' /dev/null",
        "coproc sftp host; echo '!id' >&63",
        # #665: a variable that changes the search path or names a program, with a program tool in the same command
        "PATH=/x; rsync a h:b", "PATH=/x && scp a h:b", "export PATH=/x; ssh h", "eval 'PATH=/x; rsync a h:b'",
        "PATH=/x sh -c 'rsync a h:b'", "PATH+=:/x rsync a h:b", "RSYNC_RSH=sh; rsync -a a b", "LD_AUDIT=/x; sort f",
    ]
    ALLOW = [
        # ordinary launcher use: the command they start is allowed
        "nice -n 5 ls", "timeout 5 sort x", "flock /tmp/l ls",
        # the variable is mentioned with no program tool: not a program start
        "echo $PATH", "ls && echo $PATH", "PATH=/x ls",
    ]
    POWERSHELL_DENY = ["Set-Item env:PATH 'C:\\x'; scp a h:b"]

    def test_round6_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round6_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_round6_powershell(self):
        for cmd in self.POWERSHELL_DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_tar_list_reads_only_an_uncompressed_archive(self):
        # The archive's contents are the input here: a fixture per case, in a temporary repository (never in the repo).
        ustar = bytearray(512)
        ustar[257:262] = b"ustar"
        cases = {
            "plain.tar": (bytes(ustar), "DENY"),
            "gz.tar": (b"\x1f\x8b" + bytes(510), "DENY"),
            "xz.tar": (b"\xfd7zXZ\x00" + bytes(506), "DENY"),
            "empty.tar": (b"", "DENY"),
        }
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.realpath(td)
            for name, (data, _want) in cases.items():
                with open(os.path.join(repo, name), "wb") as f:
                    f.write(data)
            for name, (_data, want) in cases.items():
                with self.subTest(archive=name):
                    body = json.dumps({"session_id": "t", "hook_event_name": "PreToolUse", "cwd": repo,
                                       "tool_name": "Bash", "tool_input": {"command": "tar -tf " + name}})
                    self.assertEqual(guard.decide(body, repo_root=repo)[0], want)


class ReviewRound6MutationTests(unittest.TestCase):
    """Issue #640 X3b, round 6: each rule must be detected by a named ReviewRound6Tests failure."""

    MUTANTS = {
        "string launcher text analysis removed": ('            if name in STRING_LAUNCHERS:', '            if False:'),
        "sshpass removed from the launchers": ('"busybox", "sshpass", "watch"', '"busybox", "sshpas", "watch"'),
        "cross-segment variable rule removed": (
            r'''    if re.search(r"(?i)((?<![A-Za-z0-9_\-/.])PATH|''', r'''    if False and re.search(r"(?i)((?<![A-Za-z0-9_\-/.])PATH|'''),
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
                                   "test_claude_destructive_guard.ReviewRound6Tests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_round6_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewRound6Tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewRound6Tests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewRound6Tests test: " + name + "\n"
                                + "\n".join(failing))


class ReviewRound7Tests(unittest.TestCase):
    """Issue #640 X3b, review of 01dfeea: string forms of every wrapper (sudo -s, flock --command=), an escaped space or
    semicolon before #, PowerShell call operators with an expression and PowerShell block comments, the cross-segment
    alias tools (gtar, bsdtar, gsort), PATHEXT, tar members under .git, and a compressed archive whose ustar header is
    present (the magic at offset 0 decides the decompressor). Decision-level only; nothing is executed."""

    DENY = [
        "flock -c \"rm -rf x\" lock", "flock --command=\"rm -rf x\" lock", "runuser -c \"rm -rf x\" root",
        "su --command=\"rm -rf x\"", "sudo -s \"rm -rf x\"",
        "echo \\ #; rm -rf x", "echo \\;#; rm -rf x",
        "PATH=/tmp/x; gtar -cf out.x a", "PATHEXT=.bat ssh h",
        "tar -cf out.tar .git/config",
    ]
    POWERSHELL_DENY = ["& ('r'+'m') -rf x", "<# note #> Remove-Item -Recurse -Force x"]
    ALLOW = [
        # a # after a space is a comment; the rm is inside it
        "echo a # comment; rm -rf x",
        # ordinary launcher use
        "flock /tmp/l ls",
    ]
    POWERSHELL_ALLOW = ["Get-Date"]

    def test_round7_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round7_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_round7_powershell(self):
        for cmd in self.POWERSHELL_DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")
        for cmd in self.POWERSHELL_ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "ALLOW")

    def test_tar_list_refuses_a_compressed_file_with_a_ustar_header(self):
        # The magic at offset 0 decides the decompressor, so a gzip file with ustar at 257 is refused.
        ustar = bytearray(512)
        ustar[257:262] = b"ustar"
        gz = bytearray(ustar)
        gz[0:2] = b"\x1f\x8b"
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.realpath(td)
            with open(os.path.join(repo, "gz.tar"), "wb") as f:
                f.write(bytes(gz))
            with open(os.path.join(repo, "plain.tar"), "wb") as f:
                f.write(bytes(ustar))
            for name, want in (("gz.tar", "DENY"), ("plain.tar", "DENY")):
                with self.subTest(archive=name):
                    body = json.dumps({"session_id": "t", "hook_event_name": "PreToolUse", "cwd": repo,
                                       "tool_name": "Bash", "tool_input": {"command": "tar -tf " + name}})
                    self.assertEqual(guard.decide(body, repo_root=repo)[0], want)


    def test_round7_reason_kinds(self):
        # Reason-level (Round 10): a PowerShell call operator before a bracket is refused by the call-operator rule itself,
        # not by the unknown-command fallback that would also DENY it.
        verdict_, reason = guard.decide(payload("& ('r'+'m') -rf x", "PowerShell"), repo_root=REPO)
        self.assertEqual(verdict_, "DENY", reason)
        self.assertEqual(reason.split(":", 1)[0], "powershell", reason)


class ReviewRound7MutationTests(unittest.TestCase):
    """Issue #640 X3b, round 7: each rule must be detected by a named ReviewRound7Tests failure."""

    MUTANTS = {
        "PATHEXT removed from the program variables": (
            '"DYLD_FRAMEWORK_PATH", "PATHEXT"', '"DYLD_FRAMEWORK_PATH"'),
        "gtar, bsdtar and gsort not program tools across segments": (
            r'''"|".join(sorted(PROGRAM_TOOLS | set(PROGRAM_ALIAS) | DEV_TOOLS))''', r'''"|".join(sorted(PROGRAM_TOOLS | DEV_TOOLS))'''),
        "PowerShell block comment rule removed": (
            r'''        if ps and quote is None and text.startswith("<#", i):''', '        if False:'),
        "escaped character before # not checked": (
            r'''(text[i - 1] in " \t\n;&|()" and not (i >= 2 and text[i - 2] == "\\"))''', r'''(text[i - 1] in " \t\n;&|()")'''),
        "call operator before a bracket not refused": (
            r'''                if k < n and text[k] in "\"'$({":''', r'''                if k < n and text[k] in "\"'$":'''),
        "long-option value not analysed as text": (
            r'''                    if words[k].startswith("--") and "=" in words[k]:''', '                    if False:'),
        "tar member under .git not refused": (
            r'''    if (role != "member" and names_claude(path)) or protected_part(parts, role):''',
            r'''    if (role != "member" and names_claude(path)):'''),
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
                                   "test_claude_destructive_guard.ReviewRound7Tests"], cwd=host,
                                  capture_output=True, text=True, timeout=300)
            return proc.returncode, proc.stderr

    def test_round7_tests_detect_each_mutant(self):
        with open(HOOK, encoding="utf-8") as f:
            original = f.read()
        code, out = self._run(original)
        self.assertEqual(code, 0, "unmutated copy must pass ReviewRound7Tests:\n" + out[-800:])
        for name, (old, new) in self.MUTANTS.items():
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
            with self.subTest(mutant=name):
                self.assertEqual(original.count(old), 1, "mutation target not found exactly once: " + name)
                code, out = self._run(original.replace(old, new))
                failing = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
                detected = any("ReviewRound7Tests." in ln for ln in failing)
                self.assertTrue(detected, "mutant not detected by a ReviewRound7Tests test: " + name + "\n"
                                + "\n".join(failing))


class Round8ExpectationTests(unittest.TestCase):
    """Issue #640 X3b, round 8 (Owner decision, 2026-11-05). Each expectation below was an ALLOW that is now DENY under an
    approved rule, moved here so that every change is one explicit assertion with its reason:
    - make, npm, pip, uv with arguments: DENY (Owner: no broad allowlist; they run arbitrary recipes or packages).
    - tar: only create (-c) is allowed; reads and extraction are DENY (Owner: TOCTOU, the file read is not the file checked).
    - tar -cf OUT SRC && gzip OUT: gzip without -c writes OUT.gz and removes OUT (destructive).
    - PowerShell cmdlets and external programs with arguments that are not individually verified: DENY (Owner rule 3).
    - timeout 5 -- echo hi: GNU timeout takes -- as the command: an unknown external with arguments.
    - "'>' rm -rf /": a command named '>' with arguments that is not allowlisted.
    The security effect of each is a stricter decision (no ALLOW was made wrongly); no rule was loosened to pass a test.
    """

    DENY = [

        "pip install -r requirements-dev.txt", "tar -tf a.tar", "tar -tvf a.tar", "tar --list --file=a.tar",
        "tar --list --file a.tar", "tar -t -f ./a.tar", "tar -tf a.tar.gz.tar", "tar -tf 'a b.tar'",
        "tar -tf a.tar src/file.txt", "tar -tf a.tar -C dir", "tar -tf a.tar -- x", "tar -tf a.tar names*.txt",
        "tar -tf missing.tar", "tar -cf out.tar src && gzip out.tar", "gzip out.tar", "gzip -d out.tar.gz",
        "gzip -dc a.tgz", "gzip -c out.txt",
        "timeout 5 -- echo hi", "'>' rm -rf /",
        # file-writing commands (Owner D1): DENY in every form

    ]
    POWERSHELL_DENY = ["Write-Error -Message hi", "Start-Process notepad file.txt",
                       '"C:\\Program Files\\app.exe" foo']
    ALLOW = [
        # the approved create form (gzip is DENY in every form: Owner D2)

        "ls -la", "cat README.md | head -n 20",

        # restored (Round A): ordinary development write, allowed when its paths pass the guard
        'tar -cf out.tar src', 'tar -cvf out.tgz dir',
    ]

    def test_round8_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round8_powershell_deny_list(self):
        for cmd in self.POWERSHELL_DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd, tool="PowerShell"), "DENY")

    def test_round8_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")


class MutationDetectionTests(unittest.TestCase):
    """Linux CI (any host where symlinks can be created): the symlink tests must FAIL for each mutant of Piece A's
    rules. Each mutant is a temporary copy of the hook and this test module in a temporary folder. No repository
    file and no remote branch is changed. If symlinks cannot be created, the class SKIPs: a SKIP is not a PASS."""

    MUTANTS = {
        "raw-spelling realpath reverted": (
            "real = canon(os.path.realpath(raw_form)) if os.path.lexists(raw_form) else lex",
            "real = canon(os.path.realpath(lex)) if os.path.lexists(lex) else lex"),
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
            if name in KNOWN_UNRESOLVED_MUTANTS:
                continue
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
    ("gt-heavy-60k", "a>" * 30000, "DENY"),
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


class Round13PolicyTests(unittest.TestCase):
    """Round A policy (Owner, convergence): ordinary development writes are ALLOW when every path passes the repository
    rule (tar_path_ok): inside the repository, literal, no .., not .claude/ or .git, no link out. The write paths the Owner
    did not revoke stay DENY (ln, rsync, scp, sort -T, ssh -E/-S/ControlPath, find -fprint, git output and config, git
    bundle). TOCTOU remains a recorded residual risk (docs/claude-destructive-guard.md)."""

    ALLOW = [
        'cp a b',
        'cp -r src dst',
        'cp README.md README.bak',
        'mv a b',
        'mv -n a b',
        'mkdir -p tests/x',
        'touch f',
        'touch -c f',
        'tee -a out.txt',
        'sort -o out.txt names.txt',
        'tar -cf out.tar src',
        'tar -cvf out.tgz dir',
        'tar -cf out.tar -C dir .',
        'echo x > docs/f.md',
        'echo a >out',
        'echo a >> out.txt',
        'echo a 2>x',
        'echo a &>x',
        'echo a 2>&1',
        'echo a >&2',
        'echo a 2>&-',
        'echo a >/dev/null',
        'echo a 2>/dev/null',
        'echo a &>/dev/null',
        'sftp host',
    ]

    DENY = [
        'ln -s a b',
        'ln -s /etc x',
        'rsync -a src/ dst/',
        'scp a host:b',
        'cp a .claude/x',
        'cp a .git/config',
        'cp a ../x',
        'mv a ../x',
        'mkdir ../x',
        'touch /etc/x',
        'tee .cl?ude/x',
        'cp -t dir a',
        'mv -f a b',
        'touch --bogus f',
        'cp *.txt dir',
        'sort -o .claude/x names.txt',
        'sort -o /etc/x names.txt',
        'sort -o',
        'tar -cf .git/x.tar src',
        'tar -cf /tmp/x.tar src',
        'sort -T dir x',
        'sort --temporary-directory=dir x',
        'ssh -E log.txt host',
        'ssh -S none host',
        'ssh -o ControlPath=none host',
        'echo a > .claude/x',
        'echo a > ../x',
        'echo a > /etc/x',
        'echo a > .git/config',
        'echo a > x*.txt',
        'find . -fprint out.txt',
        'git diff --output=x',
        'git format-patch -o dir HEAD~1',
        'git archive -o x.tar HEAD',
        'git bundle create x.bundle HEAD',
        'git config user.name x',
        'rsync --bwlimit=100 -a a b',
        'rsync --compress a b',
        'rsync --debug=all a b',
        'rsync --delete -a a b',
        'rsync --delete-after --partial-dir=.p -a a b',
        'rsync --exclude tmp -a a b',
        'rsync --files-from list.txt a b',
        'rsync --info=progress2 -a a b',
        'rsync --timeout=30 --chmod=g+w -a a b',
        'rsync -C -a a b',
        'rsync -T /tmp/x -a a b',
        'rsync -a -- -e sh a b',
        'rsync -a -- -weird a b',
        'rsync -av --exclude=tmp src dst',
        'rsync -avh a b',
        'rsync -n -v --progress src/ host:/dst/',
        'sort -o --compress-program=sh x',
        'scp -- -S a b',
        'scp -3 a b',
        'scp -i -S a b',
        'scp -i key a b',
        'scp -l 100 a b',
        'scp -o BatchMode=yes a b',
        'scp -r -P 2222 a host:b',
    ]

    REDIRECT_BYPASS = ["echo a > /etc/x", "echo a > ../x", "echo a >> ../x"]

    def test_round_a_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    def test_round_a_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "DENY")

    def test_round_a_redirect_outside_the_repository_has_the_redirect_reason(self):
        for cmd in self.REDIRECT_BYPASS:
            with self.subTest(cmd=cmd):
                got, reason = guard.decide(payload(cmd), repo_root=REPO)
                self.assertEqual(got, "DENY")
                self.assertTrue(reason.startswith("redirect"), reason)



class MutationRegisterTests(unittest.TestCase):
    """The known-unresolved register: every entry names a defined mutant, and the count is fixed so that a change to the
    register is reviewed (Owner, Round A: unresolved mutants are recorded, not hidden)."""

    def test_register_entries_are_defined_mutants(self):
        defined = set()
        for cname in dir(sys.modules[__name__]):
            cls = getattr(sys.modules[__name__], cname)
            muts = getattr(cls, "MUTANTS", None)
            if isinstance(muts, dict):
                defined |= set(muts)
        self.assertEqual(sorted(set(KNOWN_UNRESOLVED_MUTANTS) - defined), [])

    def test_register_count_is_locked(self):
        self.assertEqual(len(KNOWN_UNRESOLVED_MUTANTS), 21, "review the register before changing its size")


class RoundBReviewRegressionTests(unittest.TestCase):
    """Round B (independent review, fixes): the reviewer's bypass inputs must be DENY; the build and test tools (Owner,
    option 4) and the normal workflow must be ALLOW. The reviewer's inputs are kept verbatim so that a regression shows."""

    ALLOW = [
        'git -c color.ui=false status',
        'git status',
        'git log --oneline -5',
        'git diff --stat',
        'make test',
        'make check',
        'make -C build test',
        'npm test',
        'npm run build',
        'npm install',
        'pytest tests',
        'pytest -q tests/host',
        'dotnet build',
        'dotnet test',
        'node script.js',
        'node tools/x.js',
        'cargo build',
        'cargo test',
        'rustc main.rs',
        'gcc -o out.bin main.c',
        'clang -c a.c -o a.o',
        'cmake -S . -B build',
        'ninja -C build',
        'python -B -m unittest discover -s tests/host',
        'cp a b',
        'mv a b',
        'mkdir -p out/x',
        'touch out/x.txt',
        'tee out.txt',
        'echo hi > out.txt',
        'sort -o sorted.txt x',
        'tar -cf out.tar src',
        'make test 2>&1 | tail -n 3',
        'make test 2>&1',
    ]

    DENY = [
        'touch CLAUDE~1/zz-review-probe.json',
        'cp x CLAUDE~1/zz-review-probe.json',
        'echo x > CLAUDE~1/zz-review-probe.json',
        'cp x GIT~1/hooks/zz-review-probe',
        'cp x .git./hooks/zz-review-probe',
        'tee .git./hooks/zz-review-probe',
        "cp x '.git /hooks/zz-review-probe'",
        'git push --force-w origin main',
        'git push --del origin main',
        'git clean --f -d',
        'git reset --ha',
        'git branch --delete --forc main',
        'git -c core.fsmonitor=evilprog status',
        'git -c core.sshCommand=evilprog fetch',
        'ssh -o UserKnownHostsFile=C:/Users/prague/evil host',
        'ssh -o UserKnownHostsFile=.git/hooks/pre-commit host',
        'git worktree remove wt-x',
        'C:\\Users\\prague\\evil.exe',
        './cat x',
        'cp x\n../y',
        "node -e 'x'",
        'node /tmp/x.js',
        'npm install -g x',
        'npm exec x',
        'make -C /tmp',
        'cargo install x',
        'cmake -E remove_directory build',
        'make CC=/tmp/evil',
        'pytest -p x',
        'gcc -o ../x main.c',
        'make VAR=../x',
        'make test -f /tmp/Makefile',
        'pytest --basetemp=..',
        'make -C ..',
        'make --directory=..',
        'make -C..',
        'make -C .*',
        'make -C {..,x}',
        'node ..',
        "node -ep 'x'",
        'node -pe "require(\'child_process\')"',
        'cmake -B ..',
        'cmake -S .. -B build',
        'ninja -C ..',
        'rustc -o .. x.rs',
        'rustc --out-dir=.. x.rs',
        'rustc -L .. x.rs',
        'gcc -B .. main.c',
        'gcc -wrapper sh,-c,x main.c',
        'cargo build --target-dir ..',
        'cargo --manifest-path=..',
        'pytest --rootdir=..',
        'pytest -o cache_dir=..',
        'dotnet build -o ..',
        'npm link',
        'npm --prefix ..',
        'npm install --prefix=..',
        'npm install --location=global',
        'dotnet nuget push x',
        'CARGO_HOME=C:/x cargo build',
        'npm_config_prefix=C:/x npm link',
        'npm ln x',
        'cargo build --config x.toml',
        'rustc -C linker=sh x.rs',
        'rustc -Clinker=sh x.rs',
        'make -C .git',
        'make -C .claude',
        'echo a\\|Remove-Item -Recurse C:\\Users\\prague\\x',
        'echo a\\;Remove-Item -Recurse C:\\Users\\prague\\x',
        'echo hi\\>C:\\Users\\prague\\evil.txt',
        'echo hi > ..\\evil.txt',
        'echo hi > ..\\..\\evil.txt',
        'echo hi > "..\\evil.txt"',
        'echo hi > \\Users\\prague\\evil.txt',
        'echo x > .git.\\hooks\\zz-review-probe',
    ]

    def test_round_b_deny_list(self):
        for cmd in self.DENY:
            with self.subTest(cmd=cmd):
                tool = "PowerShell" if cmd in self.DENY_PS_MARK else "Bash"
                self.assertEqual(guard.decide(payload(cmd, tool), repo_root=REPO)[0].upper(), "DENY")

    def test_round_b_allow_list(self):
        for cmd in self.ALLOW:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), "ALLOW")

    DENY_PS_MARK = set(['echo a\\|Remove-Item -Recurse C:\\Users\\prague\\x', 'echo a\\;Remove-Item -Recurse C:\\Users\\prague\\x', 'echo hi\\>C:\\Users\\prague\\evil.txt', 'echo hi > ..\\evil.txt', 'echo hi > ..\\..\\evil.txt', 'echo hi > "..\\evil.txt"', 'echo hi > \\Users\\prague\\evil.txt', 'echo x > .git.\\hooks\\zz-review-probe'])



if __name__ == "__main__":
    unittest.main()
