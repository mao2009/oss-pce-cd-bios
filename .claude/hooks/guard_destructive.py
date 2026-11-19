#!/usr/bin/env python3
"""Claude Code PreToolUse guard: deny destructive shell commands, fail-closed.

Reads hook JSON on stdin. A DENY prints hookSpecificOutput JSON with
permissionDecision "deny" (exit 0). An ALLOW prints nothing (exit 0), so the
normal permission flow still applies. The command is only inspected, never run.
Rules: docs/claude-destructive-guard.md.
"""
import fnmatch
import json
import os
import posixpath
import re
import shlex
import sys
import threading
import unicodedata

HOOK_TIMEOUT = 5.0
# ponytail: hard size cap bounds the work; the thread timeout cannot stop C-level code.
MAX_CMD_BYTES = 64 * 1024
MAX_DEPTH = 8
ALLOW, DENY = "ALLOW", "DENY"
SHELL_TOOLS = ("Bash", "PowerShell")
KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done",
            "while", "until", "(", ")"}
# run-12: first words that open a conditional/loop scope whose branch
# outcome is not known statically (Gu1P/Gu1Y/Gu1n follow-ups). The body
# is still analysed (DENY/taint detection unchanged); only the cwd it
# leaves is marked uncertain when it moved.
COND_OPENERS = {"if", "while", "until", "for", "case", "select"}
COND_CLOSERS = {"fi", "done", "esac"}
PS_COND_WORDS = {"if", "elseif", "else", "while", "for", "foreach", "switch"}
WRAPPERS = {"sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time",
            "nice", "ionice", "timeout", "setsid", "stdbuf", "xargs"}
# External launchers (#664): each runs its arguments as a command. Their options are not parsed (wrapper_candidates analyses
# every suffix of the words), so any option arity is covered. String launchers (watch, su -c, script -c) also re-analyse the
# text of every suffix, because their argument is a shell string.
WRAPPERS |= {"flock", "chroot", "taskset", "chrt", "unbuffer", "strace", "nsenter", "unshare", "setpriv", "runuser",
             "fakeroot", "busybox", "sshpass", "watch", "su", "script", "coproc"}
# Every wrapper's argument can be a shell string (sudo -s "rm -rf x", flock --command="..."): all of them are re-analysed as text.
STRING_LAUNCHERS = WRAPPERS
WRAPPER_ARGS = {
    "xargs": {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "-e"},
    "env": {"-u", "-C", "-S"},
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-T", "-U"},
    "doas": {"-u", "-C"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p", "-t"},
    "timeout": {"-s", "-k"},
}
GIT_OPT_ARG = {"-c", "--git-dir", "--work-tree", "--namespace"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
PS_HOSTS = {"powershell", "pwsh"}
PS_RM = {"remove-item", "ri", "del", "erase"}
# PowerShell cmdlets that start a process or a job from their arguments (their arguments are analysed as a command).
PS_LAUNCH_NAMES = {"start-process", "start", "saps", "invoke-item", "ii", "invoke-command", "icm", "start-job",
                   "sajb", "start-threadjob", "invoke-wmimethod", "invoke-cimmethod", "iwmi", "icim"}
LAUNCHER_MAX_WORDS = 256
# PowerShell process launchers whose argument expression is not inspected: a launcher with expression syntax
# (comma arrays, parentheses, @(), scriptblocks, variables) is an execution form the guard cannot parse, so it is denied.
PS_PROC_LAUNCH_RE = re.compile(
    r"(?i)(?<![\w-])(start-process|start|saps|invoke-item|ii|invoke-wmimethod|invoke-cimmethod|iwmi|icim|"
    r"invoke-command|icm|start-job|sajb|start-threadjob)(?![\w-])")
EXPR_SYNTAX_RE = re.compile(r"[,()@{}$]")
# PowerShell method call syntax (.Delete(), $x[0].Delete()). The "(" would split the call from its object, so the call
# is refused on the raw text before any split. Property access ($_.Name) is not a call and is not refused.
METHOD_CALL_RE = re.compile(r"(?i)\.\s*(?:\w+\s*\(|['\"(])|-m(?:e|em\w*)?\b")
CD_NAMES = {"cd", "chdir", "set-location", "sl", "pushd", "push-location"}
# A coproc name is only a name before a compound command (Piece D, #636).
COPROC_COMPOUND = frozenset({"{", "(", "((", "[[", "if", "until",
                                "for", "while", "case", "select", "!"})
# External commands whose arguments only read data, or name plain files (writes into .claude/ are checked by WRITE_NAMES).
# None of them runs another program or evaluates its arguments. Any other external command with arguments is DENY.
SAFE_EXTERNAL = {"cat", "head", "tail", "wc", "ls", "echo", "printf", "pwd", "true", "false", "diff", "stat", "file",
                 "which", "sleep", "uname", "whoami", "basename", "dirname", "realpath", "readlink", "grep"}
# Ordinary development writes (Owner, Round A): cp, mv, mkdir, touch and tee are allowed when each option is in
# SAFE_WRITE_OPTS and each operand is a repository path that tar_path_ok accepts (see safe_write_rule).
SAFE_WRITE = {"cp", "mv", "mkdir", "touch", "tee"}
# Never allowed: ln creates links the guard cannot follow to the place they point at; rsync and scp copy to or from
# another location or host, which is not inspected from argv (Owner Round 13, not revoked).
WRITE_DENY = {"ln", "rsync", "scp"}
# Always denied: destructive by nature, or cannot be inspected.
ALWAYS_DENY = {"unlink", "shred", "rmdir", "rd", "dd", "mkfs", "truncate", "wipefs",
               "srm", "cmd", "cmd.exe",
               # popd and Pop-Location return to a directory the guard cannot know (the stack is not tracked): denied.
               "popd", "pop-location"}
PY_NAME = re.compile(r"^python(3(\.\d+)?)?$|^py$")
# A parenthesised group runs in a subshell: its cwd never reaches the
# parent, but a filesystem change inside it outlives the subshell, so taint
# still merges. split_segments emits these sentinels for unquoted parens
# (quoted or escaped parens stay ordinary words); analyze_text keeps a
# stack of pre-subshell cwds. Control characters are rejected on input, so
# \x02 can never come from the command text itself.
SUBSHELL_OPEN = "\x02("
SUBSHELL_CLOSE = "\x02)"
# A pipeline stage boundary (Bash only): each stage of `a | b` runs in
# a subshell, so its cwd never reaches the parent, while a filesystem
# change inside it still taints later judgments. `||` and `&&` are
# sequential (same shell) and never emit this. Control characters are
# rejected on input, so \x02 can never come from the command text itself.
PIPE_BOUND = "\x02|"
# A background-list boundary (Bash only): `a & b` backgrounds the whole
# `a` list in a subshell. The parent continues from the cwd the list
# started with (bg_start), while taint still propagates. Same \x02
# safety as PIPE_BOUND.
BG_BOUND = "\x02&"
# A top-level command boundary (Bash `;` or newline): no scope effect, but
# it ends a background list and a pipeline, so analyze_text restores the
# pipeline-entry cwd here and refreshes the entry snapshots. Same \x02
# safety as PIPE_BOUND. `&&`/`||` are sequential inside a list and never
# emit this.
CMD_BOUND = "\x02;"
# A Bash coprocess-compound boundary: `coproc { ...; }` runs its body in a
# subshell (like `( ... )`), so the body shares the parent taint but its
# cwd is restored at the close. The `{` stays a literal word of the
# `coproc ...` segment (so coproc dispatch sees exactly what it saw
# before); only the scope is new. Same \x02 safety as PIPE_BOUND.
COPROC_OPEN = "\x02c{"
COPROC_CLOSE = "\x02c}"
# A Bash brace-group boundary: `{ ...; }` runs in the calling shell, so it
# shares cwd like a plain sequence — but as a pipeline stage the whole
# group still runs in one subshell. Scoping it lets pipeline entries stay
# exact when the first stage spans several segments. Only a command-
# position `{` opens a group (mid-word `{a,b}` stays literal, so brace
# expansion words keep their current validation). A `}` closes a group
# only as a standalone word (a `}` inside a word stays literal). A stray
# `}` with no open group stays an ordinary word, as before.
BBRACE_OPEN = "\x02b{"
BBRACE_CLOSE = "\x02b}"
# run-12: a PowerShell conditional/loop body in braces. The location
# change inside persists when the branch runs, but the branch outcome
# is not known statically, so the close marks the cwd uncertain.
PSCOND_OPEN = "\x02p{"
PSCOND_CLOSE = "\x02p}"
# A PowerShell non-conditional `{ ... }` group (statement list,
# scriptblock argument, hashtable). Like PSCOND it carries no cwd scope
# (the calling scope applies), but unlike PSCOND it is needed for stdin
# provenance: a bare interpreter inside a pipe-fed scriptblock still reads
# the pipe (Issue #637). The loop skips these like the other sentinels.
PSBRACE_OPEN = "\x02P{"
PSBRACE_CLOSE = "\x02P}"
# run-12 OQwd: `&&`/`||` terminate the pipeline like `;` (but do not
# start a new background list). The enclosing cwd is restored first.
PIPE_END = "\x02e"
# PowerShell `{ ... }` groups (statement bodies, scriptblocks, hashtables)
# run in the calling scope for cwd purposes: `if`/`while`/`foreach` bodies
# execute in the current scope, so a Set-Location inside persists like a
# Bash brace group. Only `& { ... }` (child scope, uncertain) stays
# refused elsewhere. Brace depth is still tracked so an unclosed `{`
# (a shell syntax error) is refused instead of guessed.

# Placeholder for a $(...), `...`, <(...) or >(...) body. NUL is rejected on input,
# so the placeholder can never collide with user text.
SUBST = "\x00SUBST\x00"
EVAL_LIKE = SHELLS | PS_HOSTS | {"eval", "source", "."}
INTERP = {"perl", "node", "ruby", "php", "awk", "gawk", "lua"}
# Commands that write to their arguments: a .claude/ target is denied for these.
WRITE_NAMES = {"tee", "cp", "mv", "install", "rsync", "touch", "mkdir", "ln", "chmod",
               "chown", "scp", "curl", "wget", "tar", "unzip", "zip", "patch",
               "copy", "move", "copy-item", "move-item", "rename-item", "set-content",
               "add-content", "out-file", "new-item", "cpi", "mi", "ni", "sc", "ac"}
DANGER_PY = re.compile(r"rmtree|remove|unlink|rmdir|removedirs|system|subprocess|popen|"
                       r"shutil|spawn|exec|eval|__import__|open\s*\(|write|truncate|"
                       r"ctypes|socket|replace|rename|chmod|chown|symlink|copy|move|dump\b|link|"
                       r"fileio|touch|mkdir|makedirs|mkfifo|mknod|utime|"
                       r"getattr|globals|locals|vars|builtins|__dict__", re.I)
# X3a (Issue #639): launcher classification by command name. Names only. Option grammars (X3b), wrapper and
# shell grammars (X3c) and interpreter or tool forms (X3d) are decided by their own units. A name in no class is
# UNKNOWN (UNKNOWN_CHECKED: the existing rules apply). A launcher class is FORM_DEPENDENT: its status depends on
# the form it is given, so the name alone never proves safety or danger.
LAUNCH_TABLE = (
    ("NONE", "echo printf cat ls pwd head tail wc uniq cut tr basename dirname realpath date stat du df "
             "test true false which type grep egrep fgrep"),
    ("FIXED_HELPER", "diff file"),
    ("OPTION", "sort rg find tar rsync ssh scp sftp"),
    ("GIT", "git"),
    ("PKG", "npm npx yarn pnpm pip pip3 uv pipx"),
    ("DANGEROUS", "rm"),
)
LAUNCH_CLASS = {name: cls for cls, names in LAUNCH_TABLE for name in names.split()}
LAUNCH_CLASS.update({n: "CD" for n in CD_NAMES})
LAUNCH_CLASS.update({n: "WRAPPER" for n in WRAPPERS})
LAUNCH_CLASS.update({n: "SHELL" for n in SHELLS | PS_HOSTS | {"eval", "source", "."}})
LAUNCH_CLASS.update({n: "INTERP" for n in INTERP})
LAUNCH_CLASS.update({n: "DANGEROUS" for n in PS_RM | ALWAYS_DENY})


def launch_class(name):
    """Class of a normalised command name (basename, lowercase, no .exe)."""
    if PY_NAME.match(name):
        return "PY"
    return LAUNCH_CLASS.get(name, "UNKNOWN")


def name_status(name):
    """Name-level status. UNKNOWN_EXECUTION is never given to a name: it comes from a form (X3b to X3d) or from
    the fail-closed conditions of analyze_argv (function and coproc without a command)."""
    cls = launch_class(name)
    if cls == "DANGEROUS":
        return "KNOWN_DANGEROUS"
    if cls in ("NONE", "FIXED_HELPER", "CD"):
        return "KNOWN_SAFE"
    if cls == "UNKNOWN":
        return "UNKNOWN_CHECKED"
    return "FORM_DEPENDENT"


def handoff(name):
    """Boundary to X3c: a WRAPPER hands the rest of argv to unwrap(). Other classes do not hand off."""
    return launch_class(name) == "WRAPPER"
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
# A word that starts with a redirection (>out, 2>/dev/null, &>x, <in) and the bare operator forms (>, 2>, >>, <).
# Marker for an unquoted redirection operator (see split_segments). Control characters are rejected on input, so a
# \x01 never comes from the command text itself.
REDIR_MARK = "\x01"


def strip_redirections(words):
    """Remove redirections from argv everywhere: a redirection never changes argv. A marked operator takes the next
    word as its target; an fd number directly before a marked operator goes with it. Targets are checked by the
    redirect rules (check_redirect), not here."""
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w.startswith(REDIR_MARK):
            if i + 1 < len(words) and words[i + 1].startswith(REDIR_MARK):
                # An operator whose target is another operator (> 2>x) has no target: a syntax error in the shell.
                raise Deny("redirect", "redirection with no target is not inspected")
            i += 2 if i + 1 < len(words) else 1
            continue
        out.append(w)
        i += 1
    return out
MNT = re.compile(r"^/mnt/([A-Za-z])(/.*)?$")
MSYS = re.compile(r"^/([A-Za-z])(/.*)?$")  # Git Bash drive form: /c/Users/... is C:/Users/...
# Generic roots only. The home directory is derived at run time (home_roots); owner-specific roots come
# from the untracked local configuration (local_roots), never from this public file.
PROTECTED_BASE = ["/", "/home", "/mnt", "/mnt/c", "/mnt/c/users"]
REQUIRE_ENV = "OSS_PCE_GUARD_REQUIRE_LOCAL"


class Deny(Exception):
    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


# Piece D (#636) chain rule. A segment that changes the filesystem invalidates
# the hook-time filesystem state (symlink targets, existence, cwd-relative
# resolution) that later destructive-path judgments rely on. The taint is set
# by an earlier segment in the same command and read by rm, write and redirect
# judgments for later segments. The taint never clears within one command.
# ln and rmdir are already DENY in every form; cd/pushd move the tracked cwd.
class Ctx:
    def __init__(self, cwd, root, git, registry, protected, tainted=False,
                 cwd_fuzzy=False, exec_out=False):
        self.cwd = cwd            # canonical absolute path
        self.root = root          # case-folded canonical repo root
        self.git = git            # case-folded canonical .git directory
        self.registry = registry  # case-folded worktree paths with a recorded owner
        self.protected = protected
        self.tainted = tainted    # Piece D (#636): an earlier segment changed the filesystem
        self.cwd_fuzzy = cwd_fuzzy  # run-12: cwd is one of several branch outcomes (fail closed)
        self.pending_write = False  # Piece D (#636): validated write option awaiting publish
        self.exec_out = exec_out  # Issue #637: a same-shell nested eval persistently
        # redirected stdin (set during analyze_argv, consumed per segment by analyze_text)

    def copy(self, **kw):
        c = Ctx(self.cwd, self.root, self.git, self.registry, self.protected,
                self.tainted, self.cwd_fuzzy, self.exec_out)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


def _cond_entry(ctx):
    """Snapshot a conditional/loop entry (run-12 OQww)."""
    return {"cwd": ctx.cwd, "fuzzy": ctx.cwd_fuzzy, "tainted": ctx.tainted,
            "then_taint": ctx.tainted, "arm": None, "moved": False}


def _taint(ctx):
    """Mark that this command already changed the filesystem (Piece D, #636)."""
    if ctx is not None:
        ctx.tainted = True


def _require_untainted(ctx, what):
    """Refuse a destructive path judgment after a same-command filesystem change."""
    if ctx is not None and ctx.tainted:
        raise Deny("chain", what + " after a filesystem change in the same command is not inspected")


def _nested_text(text, ctx, ps, depth, fed=False, outer_funcs=None, merge_cwd=False,
                 merge_exec=False):
    """Analyse nested command text, propagating chain taint back to the parent.

    merge_cwd (eval only, #636): the nested text runs in the calling shell,
    so a cwd change inside it moves the parent too. Every other nested form
    (sh -c, command/process substitution, wrapper strings) runs in a child
    process or a subshell, so its cwd is discarded while taint still merges.
    fed (Issue #637): the nested text inherits this segment's stdin when the
    nested form shares it (substitutions, eval, group wrappers); a fresh
    child shell (sh -c) is still analysed from its own stdin unless the
    caller passes the inherited mark. outer_funcs: visible shell function
    definitions for the same-shell nested forms (eval, substitutions).
    merge_exec (eval only, Issue #637): the nested text runs in the calling
    shell, so a persistent stdin redirect inside it (e.g. `exec < file`)
    moves the parent's stdin too. Returns the nested persistent stdin
    effect for callers that merge it themselves.
    """
    sub = ctx.copy()
    nested_end = analyze_text(text, sub, ps, depth, inherited_fed=fed, outer_funcs=outer_funcs)
    if sub.tainted:
        ctx.tainted = True
    if merge_cwd:
        ctx.cwd = sub.cwd
        ctx.cwd_fuzzy = sub.cwd_fuzzy
    if merge_exec and nested_end:
        ctx.exec_out = True
    return nested_end


def _nested_text_from(text, ctx, entry_tainted, ps, depth, fed=False, outer_funcs=None):
    """Analyse one alternative string from the entry taint snapshot (Piece D, #636).

    Alternative parses of the same invocation (wrapper strings, launcher
    suffixes) are not sequential commands, so a visit must not see taint added
    by another visit of the same text. Taint found here still propagates back
    for later segments."""
    sub = ctx.copy()
    sub.tainted = entry_tainted
    analyze_text(text, sub, ps, depth, inherited_fed=fed, outer_funcs=outer_funcs)
    if sub.tainted:
        ctx.tainted = True


def _record_validated_write(ctx):
    """Record a validated write option for end-of-validation publish (Piece D, #636)."""
    if ctx is not None:
        ctx.pending_write = True


def _publish_pending_write(ctx, name):
    """Publish a recorded write-option taint after full argv validation."""
    if getattr(ctx, "pending_write", False):
        ctx.pending_write = False
        _require_untainted(ctx, name)
        _taint(ctx)


def _nested_argv(words, ctx, ps, depth, fed=False, outer_funcs=None):
    """Analyse nested argv, propagating chain taint back to the parent."""
    sub = ctx.copy()
    analyze_argv(words, sub, ps, depth, fed, outer_funcs)
    if sub.tainted:
        ctx.tainted = True


# Builtins that run in the calling shell (so the caller cwd moves too)
# when reached through command/builtin/time. Anything else reached that
# way runs in a child process (or is refused), so its cwd is discarded.
# source/. execute a file the guard cannot inspect and stay refused.
SAME_SHELL_NAMES = frozenset({"command", "builtin", "eval"}) | CD_NAMES


def same_shell_target_index(name, args, ctx):
    """Index of the command word for same-shell wrappers, or None.

    None means "not a plain execution form": fall through to the existing
    suffix analysis (fail closed, e.g. command -v only describes)."""
    i = 0
    if name == "command":
        while i < len(args) and args[i] == "-p":
            i += 1
        if i < len(args) and args[i] == "--":
            # Piece D (#636): `command -- cd sub` runs cd in the calling
            # shell (verified on Bash): consume one terminator so the
            # wrapped builtin merges cwd. Other dash forms still describe.
            i += 1
        elif i < len(args) and args[i].startswith("-"):
            return None
    elif name == "time":
        # Exact grammar (may DENY, e.g. time -o); side-effect free here.
        i = wrapper_command_index(name, args, ctx)
    if i >= len(args):
        return None
    if name == "builtin" and args[i].startswith("-"):
        # Piece D (#636): `builtin -- cd sub` runs cd in the calling
        # shell (verified on Bash): consume one terminator.
        if args[i] == "--" and i + 1 < len(args):
            i += 1
        else:
            return None
    return i


def _nested_argv_same_shell(words, ctx, ps, depth, fed=False, outer_funcs=None,
                            via_builtin=False):
    """Same-shell wrapper target: merge chain taint and cwd back to the parent."""
    sub = ctx.copy()
    analyze_argv(words, sub, ps, depth, fed, outer_funcs, via_builtin)
    if sub.tainted:
        ctx.tainted = True
    if sub.exec_out:
        # `command`/`builtin`/`time` reach shell internals (`eval`, `cd`)
        # in the calling shell, so a persistent stdin redirect inside the
        # target moves the parent's stdin too (verified on Bash).
        ctx.exec_out = True
    ctx.cwd = sub.cwd
    ctx.cwd_fuzzy = sub.cwd_fuzzy


def canon(path, normalize=True):
    """Canonical absolute form: forward slashes, /mnt/c and c:/ both -> C:/.

    normalize=False keeps the raw ".." spelling, so that a caller can resolve it on the filesystem
    (a symlink followed by ".." must reach its real target, not the lexical one).
    """
    p = path.replace("\\", "/")
    # Absolute POSIX-form inputs are normalised before the drive mapping, so /abc/../mnt/c/users becomes
    # /mnt/c/users and then C:/users, and /a/../home becomes /home, not A:/home. Drive-letter inputs start
    # with a letter, so C:/../x is not normalised here and keeps its drive.
    if normalize and p.startswith("/"):
        p = posixpath.normpath(p)
    m = MNT.match(p) or MSYS.match(p)
    if m:
        p = m.group(1).upper() + ":" + (m.group(2) or "/")
    drive = ""
    m = re.match(r"^([A-Za-z]):", p)
    if m:
        drive = m.group(1).upper() + ":"
        p = p[2:] or "/"
    if normalize:
        p = posixpath.normpath(p)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    return drive + p


def key(path):
    return canon(path).casefold()


ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/])")


def _absolute_root(value):
    """A protected root in the same form resolve() gives a target: real path when it exists, else lexical.

    A relative value is refused (fail closed).
    """
    if not ABSOLUTE.match(value):
        raise Deny("home", "a protected root value is not absolute")
    lexical = canon(value)
    return canon(os.path.realpath(lexical)) if os.path.exists(lexical) else lexical


def home_roots():
    """Home directories to protect, derived from the environment at run time (expanduser, HOME, USERPROFILE).

    An unset or unexpanded value is ignored; a relative value is refused. An empty result makes the caller
    fail closed. The values are additive: they cannot remove a default root.
    """
    raw = [os.path.expanduser("~"), os.environ.get("HOME", ""), os.environ.get("USERPROFILE", "")]
    roots = []
    for value in raw:
        if value and value != "~":
            roots.append(_absolute_root(value))
    return sorted(set(roots))


def main_worktree_root(root):
    """Root of the main checkout. A linked worktree (.git is a file) resolves through its commondir.

    Fail closed: a .git file that cannot be resolved refuses, because the main checkout's local configuration
    could not be found.
    """
    git = os.path.join(root, ".git")
    if os.path.isdir(git) or not os.path.exists(git):
        return root
    try:
        with open(git, encoding="utf-8") as f:
            m = re.match(r"gitdir:\s*(.+)$", f.read().strip())
        if not m:
            raise ValueError("no gitdir line")
        gitdir = os.path.normpath(os.path.join(root, m.group(1).strip()))
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = os.path.normpath(os.path.join(gitdir, f.read().strip()))
    except Exception:
        raise Deny("config", "linked worktree git directory cannot be resolved")
    if os.path.basename(common) != ".git":
        raise Deny("config", "linked worktree common directory is not a .git directory")
    return os.path.dirname(common)


def _load_local_dict(path):
    """Parsed guard-local.json, or None if the file is absent. Unreadable or not an object: refuse."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        raise Deny("config", "local guard configuration is unreadable")
    if not isinstance(data, dict):
        raise Deny("config", "local guard configuration is malformed")
    return data


def _read_local_config(path):
    """Protected entries from one guard-local.json. Missing file: none. Unreadable or malformed: refuse."""
    data = _load_local_dict(path)
    if data is None:
        return []
    listed = data.get("protected", [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise Deny("config", "local guard configuration is malformed")
    return listed


def local_roots(root):
    """Extra protected roots kept outside the public tree. Additive only; never removes a default.

    Sources, all unioned: the environment variable OSS_PCE_GUARD_PROTECTED (os.pathsep separated),
    .claude/guard-local.json in this checkout, and the same file in the main checkout when this is a linked
    worktree. A missing file adds nothing. An unreadable, malformed, unresolvable or relative entry refuses.

    Required mode (OSS_PCE_GUARD_REQUIRE_LOCAL set to anything other than "" or "0"): the main checkout's file
    must exist and list "required" paths, and each required path must be among the configured protected
    roots. Anything that cannot be confirmed refuses.
    """
    values = []
    env = os.environ.get("OSS_PCE_GUARD_PROTECTED", "")
    values += [item.strip() for item in env.split(os.pathsep) if item.strip()]
    values += _read_local_config(os.path.join(root, ".claude", "guard-local.json"))
    main = main_worktree_root(root)
    main_cfg = os.path.join(main, ".claude", "guard-local.json")
    if os.path.normcase(main) != os.path.normcase(root):
        values += _read_local_config(main_cfg)
    if os.environ.get(REQUIRE_ENV, "") not in ("", "0"):
        cfg = _load_local_dict(main_cfg)
        if cfg is None:
            raise Deny("config", "required local guard configuration is missing")
        required = cfg.get("required")
        if not isinstance(required, list) or not required or not all(isinstance(r, str) for r in required):
            raise Deny("config", "required local guard configuration lists no required paths")
        resolved = {_absolute_root(item) for item in values if item}
        for r in required:
            if _absolute_root(r) not in resolved:
                raise Deny("config", "a required protected path is not configured")
    return [_absolute_root(item) for item in values if item]


def inside(k, base):
    return k.startswith(base.rstrip("/") + "/")


def is_protected(k, ctx):
    """True if k is a protected path, or an ancestor of one."""
    for p in ctx.protected:
        if p == k or p.startswith(k.rstrip("/") + "/"):
            return True
    return False


def _dotdot_unresolved(p, raw_form):
    """True if a real ".." component in p cannot be resolved here. p is the slash-normalised path; raw_form the
    raw spelling. Refused when the whole path does not exist, or when the directory that a ".." follows does not
    exist (Windows resolves ".." lexically, so a path can "exist" through an absent directory)."""
    parts = p.split("/")
    if ".." not in parts:
        return False
    if not os.path.exists(raw_form):
        return True
    for i, comp in enumerate(parts):
        if comp == "..":
            before = "/".join(parts[:i]) or "/"
            # A bare drive alias before ".." (/c/.., /mnt/c/.., /cygdrive/c/..) is refused: Git Bash resolves
            # /c/.. to /, not to the drive root that the native mapping would give.
            if re.fullmatch(r"/(mnt/|cygdrive/)?[A-Za-z]", posixpath.normpath(before)):
                return True
            # Test the native form: /c/... and /mnt/c/... are mapped to C:/... first. An unmapped alias such as
            # /cygdrive/... is not native here, so it does not exist and the path is refused (fail-closed).
            if not os.path.exists(canon(before, normalize=False)):
                return True
    return False


def resolve_full(raw, ctx, deny_unresolvable_dotdot=False, for_cd=False):
    """Return (lexical canonical, real canonical, final entry is a link) for a path argument.

    The real path resolves the raw spelling on the filesystem, so a symlink followed by ".." reaches its
    real target. The final entry is checked as a link after its parent directory is resolved, so a link
    reached through ".." is seen as a link. Absent paths keep the lexical form.

    deny_unresolvable_dotdot (rm only): a path with a real ".." component that does not resolve at hook time
    is refused, because a link created earlier in the same command could change where ".." leads. Only a
    path that has a ".." component is refused; an absent plain path is not.
    """
    if not raw:
        raise Deny("path", "empty path")
    if "$" in raw or "`" in raw:
        raise Deny("unexpanded", "variable or substitution in path")
    if any(ch in raw for ch in "*?[]{}"):
        raise Deny("glob", "glob or brace in path")
    if raw == "~" or raw[:2] in ("~/", "~\\"):
        raw = os.path.expanduser("~") + raw[1:]
    elif raw.startswith("~"):
        raise Deny("unexpanded", "~user path")
    p = raw.replace("\\", "/")
    if not (p.startswith("/") or re.match(r"^[A-Za-z]:/", p)):
        if ":" in p:
            raise Deny("path", "drive-relative path")
        if ctx.cwd_fuzzy and not for_cd:
            # run-12: a relative path cannot be judged from an uncertain
            # working directory (conditional branch outcomes). Absolute
            # paths do not depend on the cwd and stay checkable.
            raise Deny("cwd-uncertain", "a relative path with an uncertain working directory is not inspected")
        p = ctx.cwd.rstrip("/") + "/" + p
    lex = canon(p)
    raw_form = canon(p, normalize=False)
    if deny_unresolvable_dotdot and _dotdot_unresolved(p, raw_form):
        raise Deny("rm-unresolvable-dotdot", "a path with .. that does not resolve here is denied")
    real = canon(os.path.realpath(raw_form)) if os.path.lexists(raw_form) else lex
    parent, base = os.path.split(raw_form)
    entry_link = base not in ("", ".", "..") and os.path.islink(
        os.path.join(os.path.realpath(parent) if parent else ".", base))
    return lex, real, entry_link


def resolve(raw, ctx, for_cd=False):
    """Return (lexical canonical, real canonical) for a path argument."""
    lex, real, _ = resolve_full(raw, ctx, for_cd=for_cd)
    return lex, real


def _cd_target(target, ctx):
    """Resolve a cd target and the certainty it leaves (run-12 Gu1P/OQwR)."""
    lex, real = resolve(target, ctx, for_cd=True)
    if not ctx.tainted and not os.path.isdir(real):
        # Nothing earlier in this command could have created the
        # directory. An explicit path (with a slash) cannot come from
        # CDPATH, so the shell keeps the old cwd for certain. A bare
        # name may still resolve through CDPATH (which the hook cannot
        # see, including command-prefix assignments), so only the cwd
        # is kept while the result is marked uncertain.
        if "/" in target.replace("\\", "/"):
            return ctx.cwd, ctx.cwd_fuzzy
        return ctx.cwd, True
    p = target.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:/", p) or p == "~" or p.startswith("~/"):
        return real, False
    return real, ctx.cwd_fuzzy


def strip_quotes(tok):
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        return tok[1:-1]
    return tok


def words_of(seg, ps):
    try:
        if ps:
            # PowerShell: the command name may be quoted in pieces ("Remove"-Item, r"m"). The first piece is one word
            # with its quotes removed, so "C:\Program Files\x.exe" stays one word too.
            m = re.match(r"""\s*((?:"[^"]*"|'[^']*'|[^\s"'])+)""", seg)
            head = []
            if m:
                head = [m.group(1).replace('"', "").replace("'", "")]
                seg = seg[m.end():]
            return head + [strip_quotes(t) for t in shlex.split(seg, posix=False)]
        # Keep Windows-style backslashes literal (C:\x) instead of shell-escaping them.
        seg = re.sub(r"\\(?=[A-Za-z0-9.:_\-/])", r"\\\\", seg)
        return shlex.split(seg, posix=True)
    except ValueError:
        raise Deny("parse", "shell syntax could not be parsed")


def _matched(text, start):
    depth, q, j = 1, None, start
    while j < len(text):
        c = text[j]
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start:j], j + 1
        j += 1
    raise Deny("parse", "unbalanced command substitution")


def _unescape(word):
    """Drop quotes and unquoted backslash escapes: .cl\\aude and ".claude" both give .claude."""
    out, q, i, n = [], None, 0, len(word)
    while i < n:
        c = word[i]
        if q == "'":
            if c == "'":
                q = None
            else:
                out.append(c)
        elif c == "\\" and i + 1 < n:
            out.append(word[i + 1])
            i += 2
            continue
        elif q == '"':
            if c == '"':
                q = None
            else:
                out.append(c)
        elif c in "'\"":
            q = c
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _redir_target(text, j):
    """Raw word after a '>' whose '>' ends at j-1. "" for fd duplication (>&1) or no word.

    Linear: the word stops at unquoted whitespace or an operator character.
    """
    n = len(text)
    if j < n and text[j] in "|>":
        j += 1
    if j < n and text[j] == "&":
        if j + 1 < n and (text[j + 1].isdigit() or text[j + 1] == "-"):
            return ""
        j += 1
    while j < n and text[j] in " \t":
        j += 1
    start, q = j, None
    while j < n:
        c = text[j]
        if q:
            if c == q:
                q = None
            elif c == "\\" and q == '"':
                j += 1
        elif c == "\\":
            j += 1
        elif c in "'\"":
            q = c
        elif c in " \t\n;&|()<>":
            break
        j += 1
    return text[start:min(j, n)]


def check_redirect(raw, op=">", ctx=None, ps=False):
    """Deny a redirect target that names .claude/ or cannot be known statically."""
    if not raw:
        return
    if any(ch in raw for ch in "$`") or SUBST in raw:
        raise Deny("unexpanded", "redirect target with a variable or substitution is not inspected")
    if any(ch in raw for ch in "*?[]{}"):
        raise Deny("glob", "redirect target with a glob or brace is not statically known")
    target = raw.replace('"', "").replace("'", "") if ps else _unescape(raw)
    if not target:
        raise Deny("path", "empty redirect target")
    # Check the raw spelling (Windows C:\...\.claude) and the unescaped one (.cl\aude).
    for v in (raw, target):
        if ".claude" in v.replace("\\", "/").casefold():
            raise Deny("claude-write", "redirect into .claude/ is denied")
    # A duplicate or close of a file descriptor (2>&1, >&-) and the null device write no file. Any other redirect writes
    # a file: it is allowed only when the path is a repository path (the cp rule), and DENY otherwise (Owner, Round A).
    if op.endswith("&") and re.fullmatch(r"\d+|-", raw):
        return
    if target.casefold() in ("/dev/null", "nul"):
        return
    if ctx is not None and tar_path_ok(target, ctx, "archive"):
        # Piece D (#636): after the path check, so a tainted bad target keeps
        # its original reason and only a would-be ALLOW is refused and taints.
        _require_untainted(ctx, "redirect")
        _taint(ctx)
        return
    raise Deny("redirect", "redirect writes outside the repository or to a protected path: " + raw)


def split_segments(text, ps=False):
    """Split on unquoted ; & | ( ) and newlines (plus "{}," for PowerShell).

    Returns (segments, substitution bodies, redirect targets per segment).
    Each substitution body belongs to the segment being built when "$(...)",
    backticks or "<(...)"/">(...)" was seen (i.e. the segment that will hold
    its SUBST placeholder). A redirect target is the raw word after each
    unquoted '>' ("" when there is none). Stdin provenance (which segment
    is pipe-fed) is NOT returned here; `_stdin_prepass` computes it over
    these segments. A pipe-fed flag is True when the segment's stdin is a
    pipe from the previous segment: the separator that starts it
    is a single `|` (not `||`, and `|&` keeps the pipe through the `&`). A whitespace-only segment (a line
    continuation after `|`, or `(`/`{` directly after a pipe) carries the flag over without changing it.
    A `;` (or newline, `&`, `||`, `,` for PowerShell) ends the pipe for the next segment only outside
    any pipe-fed compound; inside a pipe-fed `if/while/until/for/case/select/function`, `(...)` or
    `{...}` the flag is inherited across `;` (MAJOR-1: `... | if c; then node; fi` stays marked).
    An independent command after the top-level pipeline (`... | cat; node`) stays unmarked.
    Too deep compound nesting is fail-closed DENY (depth limit).
    """
    segs, subs, cur, redirs, rd = [], [], [], [], []
    group_stack = []  # open Bash scopes, innermost last: "brace" or "coproc"
    ps_group_stack = []  # open PowerShell `{` groups: True when a conditional/loop body
    ps_cond_pending = False  # run-12: a PS conditional opener awaits its `{` across `(...)`
    ps_bdepth = 0  # open PowerShell `{ ... }` groups (calling scope; only balance is tracked)
    pipe_open = False  # run-12: the last boundary opened a pipeline stage not yet started
    cur_blank = True  # no non-whitespace appended since the last flush
    quote, i, n = None, 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            cur.append(c)
            if not c.isspace():
                cur_blank = False
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n and not ps:
            if text[i + 1] == "\n":          # line continuation
                i += 2
                continue
            cur.append(text[i:i + 2])
            cur_blank = False
            i += 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            body, i = _matched(text, i + 2)
            subs.append(body)
            cur.append(SUBST)
            cur_blank = False
            continue
        if c in "<>" and quote is None and i + 1 < n and text[i + 1] == "(":
            body, i = _matched(text, i + 2)  # process substitution
            subs.append(body)
            cur.append(SUBST)
            cur_blank = False
            continue
        if c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise Deny("parse", "unterminated backtick")
            subs.append(text[i + 1:j])
            cur.append(SUBST)
            cur_blank = False
            i = j + 1
            continue
        if c == "$" and quote is None and i + 1 < n and text[i + 1] in "'\"":
            raise Deny("parse", "ANSI-C or locale quoting not inspected")
        if quote == '"':
            if c == '"':
                quote = None
            cur.append(c)
            if not c.isspace():
                cur_blank = False
            i += 1
            continue
        if ps and quote is None and text.startswith("<#", i):
            # A PowerShell block comment <# ... #> is skipped; the commands in it are not run here.
            j = text.find("#>", i + 2)
            i = n if j < 0 else j + 2
            continue
        if quote is None and c == "#" and (i == 0 or (text[i - 1] in " \t\n;&|()" and not (i >= 2 and text[i - 2] == "\\"))):
            # A word-initial unquoted # starts a comment that runs to the end of the line. Quotes inside it are not
            # quotes (sort x # it's): the comment is skipped, and the newline that ends it still separates commands.
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "'\"":
            if quote is None and i + 1 < n and text[i + 1] == c:
                i += 2                       # empty pair: r""m is rm
                continue
            quote = c
            cur.append(c)
            cur_blank = False
            i += 1
            continue
        if c in "<>":
            # An unquoted redirection operator is marked and set off by spaces, so it is never glued to a word
            # (rm>x is rm, >, x). A quoted '>' is an ordinary word and is not marked.
            j = i
            while j < n and text[j] == c:
                j += 1
            # The operator takes one more character when it is >& or <& (fd dup) or >| (clobber).
            if j < n and text[j] in ("&|" if c == ">" else "&"):
                j += 1
            # A digit run glued to the operator in the source is its fd number (2>x). A spaced digit is an argument.
            k = 0
            while i - k - 1 >= 0 and text[i - k - 1].isdigit():
                k += 1
            start = i
            if k and (i - k == 0 or text[i - k - 1] in " \t\n;&|(){},") and "".join(cur[-k:]) == text[i - k:i]:
                del cur[-k:]
                cur_blank = not any(not ch.isspace() for ch in cur)
                start = i - k
            if c == ">":
                rd.append((text[i:j], _redir_target(text, j)))
            cur.append(" " + REDIR_MARK + text[start:j] + " ")
            cur_blank = False
            i = j
            continue
        if quote is None and c == "&" and i + 1 < n and text[i + 1] == ">":
            j = i + 1
            while j < n and text[j] == ">":
                j += 1
            rd.append((text[i:j], _redir_target(text, j)))
            cur.append(" " + REDIR_MARK + text[i:j] + " ")
            cur_blank = False
            i = j
            continue
        if ps and c == "&" and quote is None:
            # PowerShell call operator (& "x", & $cmd); && is a chain, not a call.
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if prev != "&" and nxt != "&":
                k = i + 1
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] in "\"'$({":
                    raise Deny("powershell", "call operator with a string or variable is not inspected")
        # PowerShell scriptblocks ({ ... }) are command bodies wherever they appear (try, ForEach-Object, Invoke-Command,
        # Start-Job -ScriptBlock). Each block boundary starts a segment, so the commands inside are analysed.
        if c in ";&|()\n" or (ps and c in "{},"):
            if c == "\n" and pipe_open and cur_blank:
                # run-12 GuIir8: a newline continues a pipeline whose
                # next stage has not started. Blank lines and comments
                # append no words, so cur stays blank through them and
                # the pipeline state survives (verified on Bash). Issue
                # #637: a ps pipeline continues the same way, so its next
                # stage stays pipe-fed there too.
                i += 1
                continue
            flushed_head = "".join(cur).split()
            if ps and flushed_head and flushed_head[0].lower() in PS_COND_WORDS:
                # run-12 Gu1n/OQwX: `if ($c) {` flushes `if ` at `(` long
                # before the `{`: remember the opener across the paren
                # segments (newlines included) so the body still gets
                # its marker. Only the block itself consumes it.
                ps_cond_pending = True
            segs.append("".join(cur))
            redirs.append(rd)
            cur, rd = [], []
            cur_blank = True
            if c == "|":
                # Piece D (#636, CRITICAL-1): each Bash pipeline stage runs
                # in a subshell. `||` is sequential (same shell); `|&`
                # pipes stderr too and is still a pipeline boundary.
                # Issue #637: a PowerShell `|` is a pipeline too (its stages
                # share stdin), so a pipe-fed bare interpreter reads its
                # program from the pipe in either shell. The PIPE_BOUND is
                # still skipped by the cwd machine for ps (its handler is
                # ps-gated), so only stdin provenance observes it there.
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt == "|":
                    i += 1  # consume ||: ends an open pipeline, not the list
                    if pipe_open:
                        segs.append(PIPE_END)
                        redirs.append([])
                    pipe_open = False
                else:
                    segs.append(PIPE_BOUND)
                    redirs.append([])
                    pipe_open = True
                    if nxt == "&" and not ps:
                        i += 1  # consume |&: still a pipeline boundary
                        # A ps `|&` keeps the `&` for the call-operator rule
                        # below (a consumed `&` would skip that check).
            elif c == "&" and not ps:
                # A single & backgrounds the whole list in a subshell;
                # && is sequential. &> is consumed as a redirect before
                # reaching here.
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt == "&":
                    i += 1  # consume &&: ends an open pipeline, not the list
                    if pipe_open:
                        segs.append(PIPE_END)
                        redirs.append([])
                    pipe_open = False
                elif nxt != ">":
                    segs.append(BG_BOUND)
                    redirs.append([])
                    pipe_open = False
            elif c == "(":
                # Piece D (#636): an unquoted paren opens a subshell.
                # The sentinel is its own segment so analyze_text can
                # scope the group without touching word parsing.
                segs.append(SUBSHELL_OPEN)
                redirs.append([])
                pipe_open = False
            elif c == ")":
                segs.append(SUBSHELL_CLOSE)
                redirs.append([])
                pipe_open = False
            elif ps and c == "{":
                # Piece D (#636): a PowerShell `{ ... }` group (statement
                # body, scriptblock, hashtable) runs in the calling scope
                # for cwd purposes, like a Bash brace group, so no scope
                # marker is emitted. Only balance is tracked for the
                # fail-closed unclosed-group check. `& { ... }` stays
                # refused by the call-operator rule above.
                # run-12: a conditional/loop body gets a marker as well:
                # its location change persists when taken, but the outcome
                # is uncertain, so the close marks the cwd uncertain.
                head = "".join(cur).split()
                is_cond = ps_cond_pending or (bool(head) and head[0].lower() in PS_COND_WORDS)
                ps_cond_pending = False
                ps_group_stack.append(is_cond)
                if is_cond:
                    segs.append(PSCOND_OPEN)
                    redirs.append([])
                else:
                    # Issue #637: a non-conditional ps `{ ... }` carries no
                    # cwd scope, but a pipe-fed scriptblock still reads the
                    # pipe, so its open/close is marked for stdin provenance.
                    # The cwd machine skips these like the other sentinels.
                    segs.append(PSBRACE_OPEN)
                    redirs.append([])
                ps_bdepth += 1
                pipe_open = False
            elif ps and c == "}":
                if ps_bdepth > 0:
                    ps_bdepth -= 1
                if ps_group_stack:
                    was_cond = ps_group_stack.pop()
                    segs.append(PSCOND_CLOSE if was_cond else PSBRACE_CLOSE)
                    redirs.append([])
                pipe_open = False
                # A stray `}` stays an ordinary word, as before.
            if c == ";" or c == "\n":
                # A top-level command boundary ends a background list and
                # a pipeline. No scope effect; analyze_text restores the
                # pipeline-entry cwd and refreshes entry snapshots here.
                # (A newline that continues a pipeline is consumed by the
                # pipe_open check above before reaching this boundary.)
                segs.append(CMD_BOUND)
                redirs.append([])
                pipe_open = False
            i += 1
            continue
        if c == "{" and not ps:
            # A `{` right after a `coproc` header (`coproc [NAME]`,
            # optionally led by `VAR=x` assignments) opens a
            # coprocess-compound subshell scope. The `{` stays a literal
            # word of the `coproc ...` segment (so coproc dispatch sees
            # exactly what it saw before); only the scope is new. This
            # applies mid-command too (`echo ok; coproc { ...; }`).
            # run-12 Gu02: the full split is skipped unless the `{` is at
            # a word boundary after a short header mentioning coproc. A
            # glued `{` (`x{`) can be neither a header nor a group open,
            # and the bounded prefix scan keeps brace-heavy input linear.
            nxt = text[i + 1] if i + 1 < n else ""
            if cur_blank or "".join(cur[-4:]).endswith((" ", "\t")):
                head = []
                total = 0
                for el in cur:
                    head.append(el)
                    total += len(el)
                    if total >= 512:
                        break
                if "coproc" in "".join(head)[:512].lower():
                    parts = [w for w in "".join(cur).split() if not ASSIGN.match(w)]
                    if (parts and parts[0].lower() == "coproc" and len(parts) <= 2
                            and "{" not in "".join(cur)):
                        segs.append(COPROC_OPEN)
                        redirs.append([])
                        group_stack.append("coproc")
                        cur.append(c)
                        cur_blank = False
                        i += 1
                        continue
            if cur_blank and (nxt == "" or nxt in " \t\n"):
                # Piece D (#636, CRITICAL-1): a command-position `{`
                # followed by a blank opens a brace group (calling
                # shell). A glued `{` (`{echo`, `{a,b}`) is a literal
                # word (verified on Bash: no group opens, and a later
                # `}` does not close). cur_blank tracks the blank-buffer
                # rule without rebuilding the segment on every `{`.
                segs.append("".join(cur))
                redirs.append(rd)
                cur, rd = [], []
                cur_blank = True
                segs.append(BBRACE_OPEN)
                redirs.append([])
                group_stack.append("brace")
                i += 1
                continue
        if c == "}" and not ps and cur_blank:
            # run-12 Gu02: a `}` closes the innermost open scope, not
            # always a brace group (`{ coproc { ...; }; ...; }` leaked
            # the coprocess body into the calling shell). run-12 Gu1A:
            # the `}` must also stand alone (verified on Bash: `}echo`
            # and `}#c` do not close the group).
            nxt = text[i + 1] if i + 1 < n else ""
            if group_stack and (nxt == "" or nxt in " \t\n;&|()<>"):
                kind = group_stack.pop()
                segs.append("".join(cur))
                redirs.append(rd)
                cur, rd = [], []
                cur_blank = True
                segs.append(BBRACE_CLOSE if kind == "brace" else COPROC_CLOSE)
                redirs.append([])
                i += 1
                continue
            # A stray `}` stays an ordinary word (KEYWORD-skipped).
        cur.append(c)
        if not c.isspace():
            cur_blank = False
        i += 1
    if quote:
        raise Deny("parse", "unterminated quote")
    if ps_bdepth:
        raise Deny("parse", "unbalanced PowerShell grouping is not inspected")
    segs.append("".join(cur))
    redirs.append(rd)
    return segs, subs, redirs


def short_has(arg, letters):
    return arg.startswith("-") and not arg.startswith("--") and any(
        ch in arg[1:] for ch in letters)


def inside_claude(path, ctx):
    """True if canonical path (case-folded or not) is .claude/ or below it."""
    k = key(path)
    claude_dir = ctx.root.rstrip("/") + "/.claude"
    return k == claude_dir or inside(k, claude_dir)


def rm_rule(args, ps, ctx):
    recursive, targets, end = False, [], False
    for a in args:
        if end or a == "-" or not a.startswith("-"):
            targets.append(a)
        elif a == "--":
            end = True
        elif ps:
            low = a[1:].lower()
            if low and "recurse".startswith(low):
                recursive = True
        elif a.startswith("--"):
            if a == "--recursive":
                recursive = True
            elif a not in ("--force", "--interactive", "--verbose"):
                raise Deny("rm", "unknown rm long option")
        else:
            for ch in a[1:]:
                if ch in "rRd":
                    recursive = True
                elif ch not in "fiv":
                    raise Deny("rm", "unknown rm option")
    if recursive:
        raise Deny("rm-recursive", "recursive delete is denied")
    if len(targets) != 1:
        raise Deny("rm", "rm needs exactly one target")
    lex, real, entry_link = resolve_full(targets[0], ctx, deny_unresolvable_dotdot=True)
    rk = real.casefold()
    if os.path.islink(lex) or entry_link:
        raise Deny("rm-symlink", "symlink delete is denied")
    if not inside(rk, ctx.root) or rk == ctx.root:
        raise Deny("rm-outside-repo", "rm target is not inside the repo root")
    if inside(rk, ctx.git) or rk == ctx.git:
        raise Deny("rm-git", "rm inside .git is denied")
    claude_dir = ctx.root.rstrip("/") + "/.claude"
    if inside(rk, claude_dir) or rk == claude_dir:
        raise Deny("rm-guard", "rm inside .claude (guard and settings) is denied")
    if is_protected(rk, ctx):
        raise Deny("rm-protected", "rm target is or contains a protected path")
    if not os.path.isfile(real):
        raise Deny("rm", "rm target is not a regular file")
    # Piece D (#636): last, so a tainted rm that hook-state already refuses
    # keeps its original reason and only a would-be ALLOW is refused here.
    _require_untainted(ctx, "rm")
    # An accepted removal changes the filesystem: later judgments must not
    # validate against the pre-removal snapshot.
    _taint(ctx)


def worktree_remove(args, ctx):
    """Always refuse git worktree remove, even for registered non-protected paths.

    Registry records ownership, not permission to delete. No option, target, or
    Owner approval bypasses this rule.
    """
    raise Deny("git-worktree-remove", "git worktree remove is always denied")


def git_long_is(a, names):
    """True if a is one of the git long options, or an unambiguous prefix of one: git accepts --forc for --force, so a prefix
    of a denied option is denied (fail closed; a prefix that also names an allowed option is over-denied)."""
    if not a.startswith("--") or len(a) < 3:
        return False
    key = a[2:].split("=", 1)[0]
    return any(n[2:].startswith(key) for n in names)


def git_rule(args, ctx):
    entry_ctx = ctx
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "--config-env" or args[i].startswith("--config-env="):
            raise Deny("git-config", "git --config-env is not inspected")
        if args[i] == "-c" or (args[i].startswith("-c") and not args[i].startswith("-C")):
            # git -c key=value runs a program for some keys (core.fsmonitor, core.sshCommand, core.pager): only the keys
            # that change formatting are allowed.
            raw_key = args[i + 1] if args[i] == "-c" and i + 1 < len(args) else args[i][2:]
            key = raw_key.split("=", 1)[0].lower()
            if not key.startswith(GIT_SAFE_CONFIG):
                raise Deny("git-config", "git -c key is not in the allowlist: " + key)
            i += 2 if args[i] == "-c" else 1
            continue
        if args[i] == "-C" and i + 1 < len(args):
            ctx = ctx.copy(cwd=resolve(args[i + 1], ctx)[1])
            i += 2
        elif args[i] in GIT_OPT_ARG:
            i += 2
        else:
            i += 1
    # Aliases are not resolved: an alias set on the command line could hide a subcommand.
    if any("alias." in a.casefold() for a in args[:i]):
        raise Deny("git-alias", "git alias set on the command line is denied")
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    if sub == "bundle":
        raise Deny("git-write", "git bundle writes a named file: DENY (Owner Round 13 audit)")
    if sub in ("diff", "format-patch", "archive", "log", "show", "blame") and any(
            a == "-o" or a.startswith(("-o", "--output")) for a in rest):
        raise Deny("git-write", "git output option writes a named file: DENY (Owner Round 13 audit)")
    if sub == "config":
        writes = {"--add", "--replace-all", "--unset", "--unset-all", "--remove-section", "--rename-section", "--edit",
                  "-e", "--global", "--system", "--file", "-f", "--worktree", "--blob"}
        positional = [a for a in rest if not a.startswith("-")]
        if any(a in writes or a.startswith(("--file=", "--blob=")) for a in rest) or len(positional) > 1:
            raise Deny("git-write", "git config write is DENY (Owner Round 13 audit)")
    if sub == "clean" and any(short_has(a, "f") or git_long_is(a, ("--force",)) for a in rest):
        raise Deny("git-clean", "git clean with force is denied")
    if sub == "reset" and any(git_long_is(a, ("--hard", "--merge")) for a in rest):
        raise Deny("git-reset", "git reset --hard/--merge is denied")
    if sub == "checkout" and (any(a in ("-f", "--", ".") or git_long_is(a, ("--force",)) for a in rest)):
        raise Deny("git-checkout", "forced or path checkout can discard changes")
    if sub == "restore":
        if "--staged" in rest and not any(a in ("--worktree", "-W") for a in rest):
            return
        raise Deny("git-restore", "git restore can discard working-tree changes")
    if sub == "switch" and any(a == "-f" or git_long_is(a, ("--force", "--discard-changes")) for a in rest):
        raise Deny("git-switch", "forced switch can discard changes")
    if sub == "branch" and any(a in ("-D", "-f") or git_long_is(a, ("--force",)) for a in rest):
        raise Deny("git-branch", "forced branch delete or move is denied")
    if sub == "stash" and rest[:1] in (["clear"], ["drop"]):
        raise Deny("git-stash", "stash clear/drop is denied")
    if sub == "push" and any(
            git_long_is(a, ("--force", "--force-with-lease", "--mirror", "--delete", "--prune"))
            or a.startswith(("--force-with-lease=", "+", ":")) or short_has(a, "fd")
            for a in rest):
        raise Deny("git-push", "force or delete push is denied")
    if sub == "worktree" and rest:
        if rest[0] == "prune":
            raise Deny("git-worktree-prune", "git worktree prune is denied")
        if rest[0] == "remove":
            worktree_remove(rest[1:], ctx)
            raise Deny("git-worktree-remove", "git worktree remove is DENY (Owner, Round A)")
    # Piece D (#636) chain rule: a subcommand that can rewrite the working tree
    # (checkout, merge, stash push or pop, ...) swaps what hook-time symlink
    # resolution saw, so later destructive segments must not rely on it.
    rewrite = sub in ("checkout", "switch", "merge", "pull", "apply", "am",
                      "rebase", "cherry-pick", "revert", "mv", "rm", "clone")
    if sub == "reset":
        # Only the option portion counts: after --, words are pathspecs.
        for a in rest:
            if a == "--":
                break
            if git_long_is(a, ("--keep",)):
                rewrite = True
                break
    if sub == "stash" and rest[:1] not in (["list"], ["show"]):
        rewrite = True
    if sub == "submodule" and rest and rest[:1] != ["status"]:
        rewrite = True
    if sub == "worktree" and rest[:1] in (["add"], ["move"]):
        # Piece D (#636, MAJOR-3): add creates directories and move
        # relocates them, so later path judgments must not rely on the
        # hook-time state. list stays read-only; remove/prune are DENY.
        rewrite = True
    if rewrite:
        entry_ctx.tainted = True


# X3b (Issue #640): program-naming options. An option whose value names a program that the tool starts is an
# execution form the guard does not analyse, so it is DENY (UNKNOWN_EXECUTION). Every tool in PROGRAM_TOOLS is decided by
# its structural grammar in ALLOWLIST below; a tool not in ALLOWLIST is never allowed through this rule.
# GNU and BSD variants of the same tools take the same program-naming options.
PROGRAM_ALIAS = {"gsort": "sort", "gtar": "tar", "bsdtar": "tar"}
PROGRAM_TOOLS = {"sort", "rg", "tar", "rsync", "ssh", "scp", "sftp"}
# Build and test tools (Owner decision, Round A option 4): allowed in a trusted repository, within the path rules of
# dev_tool_rule. They run project code (Makefile recipes, npm scripts, test code, build scripts) that the hook cannot
# inspect; this is a documented limit (docs/claude-destructive-guard.md).
DEV_TOOLS = {"make", "npm", "pytest", "dotnet", "node", "cargo", "rustc", "gcc", "clang", "cmake", "ninja"}
# Options that run code or write outside the repository, per tool. A long option (--x, or -x with more than one letter)
# matches by prefix; a single letter matches inside a combined cluster (-pe, -ep). Owner decision, Round A option 4.
DEV_DENY_OPTS = {"node": {"-e", "--eval", "-p", "--print", "-i", "--interactive"},
                 "pytest": {"-p", "-o", "--basetemp", "--rootdir", "--cache", "--confcutdir"},
                 "npm": {"-g", "--global", "--prefix", "--location", "--cache", "--userconfig", "--globalconfig"},
                 "cmake": {"-E"}, "dotnet": {"--interactive"}, "cargo": {"--config"},
                 "gcc": {"-B", "-wrapper", "-specs", "-fplugin"}, "clang": {"-B", "-wrapper", "-fplugin"}}
# Subcommands that run code outside the repository or publish (per tool).
DEV_DENY_WORDS = {"npm": {"exec", "x", "publish", "login", "adduser", "token", "unpublish", "deprecate", "link", "ln"},
                  "cargo": {"install", "publish", "login"}, "dotnet": {"tool", "nuget"}}
# git -c keys that do not run programs or change where git writes (color, advice, output and log formatting).
GIT_SAFE_CONFIG = ("color.", "advice.", "core.quotepath", "i18n.", "log.", "status.")
# File-name suffixes of compressed archives. tar reads them through a PATH-resolved decompressor (see program_option_rule).
COMPRESSED_SUFFIXES = (".gz", ".tgz", ".bz2", ".tbz", ".tbz2", ".tb2", ".bz", ".xz", ".txz", ".lz", ".lzma", ".lz4", ".lzo",
                       ".tzo", ".zst", ".tzst", ".z", ".taz", ".tlz")


def tar_only_creates(args):
    """True when tar creates an archive and reads none: -c or --create, and no -x -t -d -r -u -A (or their long forms).
    Over-approximated on purpose: a letter in any dashed word counts."""
    letters, longs = set(), set()
    for a in args:
        if a.startswith("--"):
            longs.add(a[2:].split("=")[0])
        elif a.startswith("-") and len(a) > 1:
            letters |= set(a[1:])
    creates = "c" in letters or "create" in longs
    reads = bool(letters & set("xtdrAu")) or bool(longs & {"extract", "get", "list", "diff", "compare", "append",
                                                          "catenate", "concatenate", "update", "delete"})
    return creates and not reads
# A brace group that bash expands: it has a comma or a sequence (x{I,}, {1..3}). A brace group without either is literal.
BRACE_EXPANDS = re.compile(r"\{[^{}]*(,|\.\.)[^{}]*\}")


def brace_may_form_option(a, name):
    """A brace group bash expands can make a word that is an option. Any braced tar word counts (tar's old-style cluster
    reads its first letters). Otherwise only a word that can expand to a dashed word counts: a group after a plain prefix
    (x{2,3}) expands to plain words, so it is not an option. A group with no prefix can start with a dash."""
    m = BRACE_EXPANDS.search(a)
    if not m:
        return False
    if name == "tar":
        return True
    prefix = a[:m.start()]
    return prefix == "" or prefix.startswith("-")
# Builtins that set a variable from their arguments (export $A$B, declare, read, ...).
SETTER_BUILTINS = ("export", "declare", "typeset", "local", "readonly", "read")
# Environment variables that name a program the tool starts (prefix assignments: VAR=x tool, env VAR=x tool).
PROGRAM_ENV = ("RSYNC_RSH", "SSH_ASKPASS", "TAR_OPTIONS", "SSH_SK_HELPER", "RIPGREP_CONFIG_PATH", "RSYNC_CONNECT_PROG",
               "RSYNC_SHELL", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "SSH_SK_PROVIDER",
               "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "PATHEXT",
               "CARGO_HOME", "CARGO_TARGET_DIR", "NPM_CONFIG_PREFIX", "NPM_CONFIG_USERCONFIG",
               "NPM_CONFIG_GLOBALCONFIG", "NPM_CONFIG_CACHE")
# ssh, scp and sftp -o KEY=VALUE (ssh_config keywords, case-insensitive). Only the keys listed here are allowed. A key that
# runs a local command or loads a provider or an included file (ProxyCommand, LocalCommand, PermitLocalCommand,
# KnownHostsCommand, Include, Match, PKCS11Provider, SecurityKeyProvider, XAuthLocation, ...) is not listed, so it is DENY.
SSH_OPTION_KEYS = ("batchmode", "port", "user", "hostname", "stricthostkeychecking", "userknownhostsfile",
                   "globalknownhostsfile", "identityfile", "identitiesonly", "identityagent", "addkeystoagent",
                   "serveraliveinterval", "serveralivecountmax", "connecttimeout", "compression", "loglevel",
                   "forwardagent", "forwardx11", "forwardx11trusted", "tcpkeepalive", "numberofpasswordprompts",
                   "passwordauthentication", "pubkeyauthentication", "kbdinteractiveauthentication",
                   "preferredauthentications", "ciphers", "macs", "kexalgorithms", "hostkeyalgorithms",
                   "controlmaster", "controlpersist", "requesttty", "escapechar", "checkhostip",
                   "hashknownhosts", "exitonforwardfailure", "sendenv", "setenv", "localforward", "remoteforward",
                   "dynamicforward", "proxyjump", "tunnel", "tunneldevice", "pubkeyacceptedalgorithms")


# X3b structural allowlist (Issue #640, replaces the per-option denylist one tool at a time). Each tool lists every
# short letter and long option it accepts: an option that is not listed is DENY (UNKNOWN_EXECUTION). Abbreviations of
# a long option are resolved against the tool's FULL long-option list, so a prefix that could name an unlisted program
# option is denied. "full_long" is the tool's documented long-option list; "allow_*" are the accepted ones.
ALLOWLIST = {
    "sort": {
        "full_long": ("ignore-leading-blanks", "dictionary-order", "ignore-case", "general-numeric-sort",
                      "ignore-nonprinting", "month-sort", "human-numeric-sort", "numeric-sort", "random-sort",
                      "random-source", "reverse", "sort", "version-sort", "batch-size", "check", "compress-program",
                      "debug", "files0-from", "key", "merge", "output", "stable", "buffer-size", "field-separator",
                      "temporary-directory", "parallel", "unique", "zero-terminated", "help", "version"),
        "short_flags": "bdfgiMhnRrVcCmsuz",
        "short_args": "kotTS",
        "short_writes": "oT",
        "short_deny": "T",
        "long_writes": ("output",),
        "long_deny": ("temporary-directory",),
        "long_flags": ("ignore-leading-blanks", "dictionary-order", "ignore-case", "general-numeric-sort",
                       "ignore-nonprinting", "month-sort", "human-numeric-sort", "numeric-sort", "random-sort",
                       "reverse", "version-sort", "merge", "stable", "unique", "zero-terminated", "debug",
                       "help", "version"),
        "long_args": ("random-source", "sort", "batch-size", "key", "output", "buffer-size", "field-separator",
                      "temporary-directory", "parallel", "files0-from"),
        "long_optional": ("check",),
        "optional_values": ("diagnose-first", "quiet", "silent"),
    },
    "rg": {
        "full_long": ("pre", "pre-glob", "hostname-bin", "heading", "no-heading", "type-list", "pretty", "hyperlink-format", "ignore-case",
                      "invert-match", "line-regexp", "word-regexp", "case-sensitive", "smart-case", "fixed-strings",
                      "multiline", "multiline-dotall", "null-data", "pcre2", "text", "search-zip", "count",
                      "count-matches", "files-with-matches", "files-without-match", "json", "line-number",
                      "no-line-number", "with-filename", "no-filename", "column", "byte-offset", "only-matching",
                      "quiet", "stats", "trim", "vimgrep", "files", "no-config", "no-messages", "follow",
                      "unrestricted", "sort-files", "include-zero", "mmap", "block-buffered", "line-buffered",
                      "passthru", "crlf", "version", "help", "debug", "trace", "no-unicode", "binary",
                      "one-file-system", "glob", "iglob", "type", "type-not", "type-add", "type-clear", "regexp",
                      "file", "replace", "threads", "max-depth", "max-count", "max-columns", "colors",
                      "context-separator", "field-context-separator", "field-match-separator", "path-separator",
                      "sort", "sortr", "engine", "dfa-size-limit", "regex-size-limit", "max-filesize", "ignore-file",
                      "color", "encoding", "context", "after-context", "before-context", "generate", "hidden",
                      "no-ignore", "no-ignore-dot", "no-ignore-exclude", "no-ignore-files", "no-ignore-global",
                      "no-ignore-parent", "no-ignore-vcs", "no-require-git", "no-messages", "null"),
        "short_flags": "FisvxUSawLubnNHIclopqVPh",
        "short_args": "egfrtTABCmMEjd",
        "long_flags": ("heading", "no-heading", "type-list", "pretty", "ignore-case", "invert-match", "line-regexp", "word-regexp", "case-sensitive",
                       "smart-case", "fixed-strings", "multiline", "multiline-dotall", "null-data", "pcre2", "text",
                       "count", "count-matches", "files-with-matches", "files-without-match", "json",
                       "line-number", "no-line-number", "with-filename", "no-filename", "column", "byte-offset",
                       "only-matching", "quiet", "stats", "trim", "vimgrep", "files", "no-config", "no-messages",
                       "follow", "unrestricted", "sort-files", "include-zero", "mmap", "block-buffered",
                       "line-buffered", "passthru", "crlf", "version", "help", "debug", "trace", "no-unicode",
                       "binary", "one-file-system", "hidden", "no-ignore", "no-ignore-dot", "no-ignore-exclude",
                       "no-ignore-files", "no-ignore-global", "no-ignore-parent", "no-ignore-vcs", "no-require-git",
                       "null"),
        "long_args": ("hyperlink-format", "glob", "iglob", "type", "type-not", "type-add", "type-clear", "regexp",
                      "file", "replace", "threads", "max-depth", "max-count", "max-columns", "colors",
                      "context-separator", "field-context-separator", "field-match-separator", "path-separator",
                      "sort", "sortr", "engine", "dfa-size-limit", "regex-size-limit", "max-filesize", "ignore-file",
                      "color", "encoding", "context", "after-context", "before-context", "generate"),
    },
    # rsync (OPTION SUMMARY of rsync.html, fetched from the upstream manual; rsync is not installed on this host, so
    # this grammar is not verified by running rsync). --rsh/-e, --rsync-path, -M/--remote-option, --early-input,
    # --copy-as and the daemon options are not listed, so they are DENY.
    "rsync": {
        "full_long": ("verbose", "info", "debug", "stderr", "quiet", "no-motd", "checksum", "archive", "recursive",
                      "inc-recursive", "no-inc-recursive", "relative", "no-implied-dirs", "backup", "backup-dir",
                      "suffix", "update", "inplace", "append", "append-verify", "dirs", "old-dirs", "mkpath", "links",
                      "copy-links", "copy-unsafe-links", "safe-links", "insecure-links", "confine-root", "munge-links",
                      "copy-dirlinks", "keep-dirlinks", "hard-links", "perms", "executability", "chmod", "acls", "xattrs",
                      "owner", "group", "devices", "copy-devices", "write-devices", "specials", "drop-D", "times",
                      "atimes", "open-noatime", "crtimes", "omit-dir-times", "omit-link-times", "super", "fake-super",
                      "sparse", "preallocate", "dry-run", "whole-file", "no-whole-file", "checksum-choice",
                      "one-file-system", "block-size", "rsh", "rsync-path", "existing", "ignore-non-existing",
                      "ignore-existing", "remove-source-files", "del", "delete", "delete-before", "delete-during",
                      "delete-delay", "delete-after", "delete-excluded", "ignore-missing-args", "delete-missing-args",
                      "ignore-errors", "force", "max-delete", "max-size", "min-size", "max-alloc", "partial",
                      "partial-dir", "delay-updates", "delay-symlinks", "prune-empty-dirs", "numeric-ids", "usermap",
                      "groupmap", "chown", "timeout", "contimeout", "ignore-times", "size-only", "modify-window",
                      "temp-dir", "fuzzy", "compare-dest", "copy-dest", "link-dest", "compress", "compress-choice",
                      "compress-level", "compress-threads", "skip-compress", "cvs-exclude", "filter", "exclude",
                      "exclude-from", "include", "include-from", "files-from", "from0", "old-args", "secluded-args",
                      "trust-sender", "copy-as", "address", "port", "sockopts", "blocking-io", "outbuf", "stats",
                      "8-bit-output", "human-readable", "progress", "itemize-changes", "remote-option", "out-format",
                      "log-file", "log-file-format", "password-file", "early-input", "list-only", "bwlimit", "stop-after",
                      "stop-at", "fsync", "write-batch", "only-write-batch", "read-batch", "protocol", "iconv",
                      "checksum-seed", "ipv4", "ipv6", "nice", "no-nice", "version", "help", "daemon", "config", "dparam",
                      "no-detach"),
        "short_flags": "vqcarRbulLkKHpEAXogDtUNOJSnWxdmIyCzFiPs0468h",
        "short_args": "Bf@T",
        "long_flags": ("verbose", "quiet", "no-motd", "compress", "checksum", "archive", "recursive", "inc-recursive",
                       "no-inc-recursive", "relative", "no-implied-dirs", "backup", "update", "inplace", "append",
                       "append-verify", "dirs", "old-dirs", "mkpath", "links", "copy-links", "copy-unsafe-links",
                       "safe-links", "copy-dirlinks", "keep-dirlinks", "hard-links", "perms", "executability", "acls",
                       "xattrs", "owner", "group", "devices", "specials", "times", "atimes", "open-noatime", "crtimes",
                       "omit-dir-times", "omit-link-times", "super", "fake-super", "sparse", "preallocate", "dry-run",
                       "whole-file", "no-whole-file", "one-file-system", "existing", "ignore-non-existing",
                       "ignore-existing", "remove-source-files", "del", "delete", "delete-before", "delete-during",
                       "delete-delay", "delete-after", "delete-excluded", "ignore-missing-args", "delete-missing-args",
                       "ignore-errors", "force", "partial", "delay-updates", "delay-symlinks", "prune-empty-dirs",
                       "numeric-ids", "ignore-times", "size-only", "fuzzy", "cvs-exclude", "from0", "old-args",
                       "secluded-args", "blocking-io", "stats", "8-bit-output", "human-readable", "progress",
                       "itemize-changes", "list-only", "fsync", "ipv4", "ipv6", "no-nice", "version", "help"),
        "long_args": ("backup-dir", "suffix", "chmod", "block-size", "checksum-choice", "max-delete", "max-size",
                      "min-size", "max-alloc", "partial-dir", "usermap", "groupmap", "chown", "timeout", "contimeout",
                      "modify-window", "temp-dir", "compare-dest", "copy-dest", "link-dest", "compress-choice",
                      "compress-level", "compress-threads", "skip-compress", "filter", "exclude", "exclude-from",
                      "include", "include-from", "files-from", "bwlimit", "stop-after", "stop-at", "out-format",
                      "log-file", "log-file-format", "password-file", "write-batch", "only-write-batch", "read-batch",
                      "protocol", "iconv", "checksum-seed", "nice", "address", "port", "sockopts", "outbuf", "info", "debug"),
    },
    # ssh (ssh(1), OpenBSD manual, fetched). -F (config file, which may include ProxyCommand) and -I (PKCS#11 library)
    # are not listed, so they are DENY. -o KEY is checked against SSH_OPTION_KEYS. ssh has no long options here.
    "ssh": {
        "full_long": (),
        "long_flags": (),
        "long_args": (),
        "short_flags": "46AaCfGgKkMNnqsTtVvXxYyZ",
        "short_args": "BbcDEeiJLlmOPpQRSWwo",
        "short_deny": "ES",
        "short_value_strict": "J",
        "short_arg_keys": {"o": SSH_OPTION_KEYS},
    },
    # scp (scp(1), OpenBSD manual, fetched). -F (config) and -S (program for the encrypted connection) are not listed.
    "scp": {
        "full_long": (),
        "long_flags": (),
        "long_args": (),
        "short_flags": "346ABCOpqrRTv",
        "short_args": "cilPJo",
        "short_value_strict": "J",
        "short_arg_keys": {"o": SSH_OPTION_KEYS},
    },
    # sftp (sftp(1), OpenBSD manual, fetched). -D (local sftp server run directly), -S (program) and -b (batch file, whose
    # ! lines run local commands) are not listed, so they are DENY. -F (config) is not listed.
    "sftp": {
        "full_long": (),
        "long_flags": (),
        "long_args": (),
        "short_flags": "46AaCfNpqrv",
        "short_args": "BcJilPRsXo",
        "short_value_strict": "J",
        "short_arg_keys": {"o": SSH_OPTION_KEYS},
    },
}


def allowlist_rule(name, args, ctx=None):
    """Walk the arguments under the tool's grammar. Anything the grammar does not list is denied. A '--' ends the
    options; the words after it are file names. A non-option word (a file name, or - for stdin) is allowed."""
    spec = ALLOWLIST[name]
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            return
        if a.startswith("--"):
            key, eq, val = a[2:].partition("=")
            hits = [o for o in spec["full_long"] if o == key] or [o for o in spec["full_long"] if o.startswith(key)]
            if len(hits) != 1:
                raise Deny("program-option", name + " long option is not resolved: " + a)
            opt = hits[0]
            if opt in spec["long_args"]:
                value = val if eq else (args[i] if i < len(args) else "")
                if opt in spec.get("long_deny", ()):
                    raise Deny("write", name + " " + opt + " writes a file: DENY in every form (Owner Round 13)")
                if opt in spec.get("long_writes", ()) and not (ctx is not None and tar_path_ok(value, ctx, "archive")):
                    raise Deny("write", name + " " + opt + " writes outside the repository or a protected path: " + a)
                if opt in spec.get("long_writes", ()):
                    _record_validated_write(ctx)
                if not eq:
                    i += 1  # the value is the next word
                if any(value.lower().startswith(p) for p in spec.get("long_arg_deny_prefix", {}).get(opt, ())):
                    raise Deny("program-option", name + " option value names a program: " + a)
            elif opt in spec.get("long_optional_any", ()):
                pass  # an optional value comes only after '=' and names no program
            elif opt in spec.get("long_optional", ()):
                if eq and val not in spec["optional_values"]:
                    raise Deny("program-option", name + " option value is not allowed: " + a)
            elif opt in spec["long_flags"]:
                if eq:
                    raise Deny("program-option", name + " flag takes no value: " + a)
            else:
                raise Deny("program-option", name + " option is not in the allowlist: " + a)
            continue
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in spec["short_flags"]:
                    j += 1
                    continue
                if ch in spec["short_args"]:
                    value = a[j + 1:] if j < len(a) - 1 else (args[i] if i < len(args) else "")
                    if ch in spec.get("short_deny", ""):
                        raise Deny("write", name + " -" + ch + " writes a file: DENY in every form (Owner Round 13)")
                    if ch in spec.get("short_writes", "") and not (ctx is not None and tar_path_ok(value, ctx, "archive")):
                        raise Deny("write", name + " -" + ch + " writes outside the repository or a protected path: " + a)
                    if ch in spec.get("short_writes", ""):
                        _record_validated_write(ctx)
                    if j == len(a) - 1:
                        i += 1  # the value is the next word
                    if ch in spec.get("short_value_strict", "") and (
                            value.startswith("-") or "=" in value or any(c.isspace() for c in value)):
                        # -J names jump hosts; a value that is a dashed word or a KEY=VALUE pair is an option, not a host.
                        raise Deny("program-option", name + " -" + ch + " value is not a plain host list: " + a)
                    if ch in spec.get("short_arg_keys", {}):
                        # -o KEY=VALUE (or KEY VALUE): the key must be one the grammar lists.
                        key = re.split(r"[=\s]", value, 1)[0].lower()
                        if key not in spec["short_arg_keys"][ch]:
                            raise Deny("program-option", name + " -" + ch + " key is not in the allowlist: " + a)
                        if names_claude(value):
                            raise Deny("program-option", name + " -" + ch + " value names a path under .claude: " + a)
                        parts = re.split(r"[=\s]", value, 1)
                        rest = parts[1] if len(parts) > 1 else ""
                        if key in ("userknownhostsfile", "globalknownhostsfile") and rest not in ("/dev/null", "NUL") and not (
                                ctx is not None and tar_path_ok(rest, ctx, "archive")):
                            raise Deny("write", name + " -o " + key + " writes a known-hosts file outside the repository or "
                                       "into .git/.claude: " + a)
                        if key == "proxyjump" and (rest.startswith("-") or "=" in rest or any(c.isspace() for c in rest)):
                            # ProxyJump names hosts; a dashed word or a KEY=VALUE pair in its value is an option, not a host.
                            raise Deny("program-option", name + " -o ProxyJump value is not a plain host list: " + a)
                    break  # the rest of the cluster is the value
                raise Deny("program-option", name + " short option is not in the allowlist: " + a)
            continue
        # a file name, or - for stdin: not an option


def tar_path_ok(path, ctx, role):
    """A tar operand the grammar accepts. role: "archive" (written or read as a whole), "dir" (-C), or "member" (a name
    inside the archive or on the command line). Archive and dir must be literal (no glob), relative, inside the repository,
    without .. and without a link that leaves it, and must not be .claude/ or .git. Members may be globs (they only read),
    but still may not escape the repository. Fail closed."""
    if not path or ":" in path or "\n" in path or path.startswith("-"):
        return False
    if role != "member" and any(c in path for c in "*?["):
        return False
    # Check the original components before normpath: link/../x can escape the repository
    # when link is a symlink, even though its lexical normalization is just x.
    slash_path = path.replace("\\", "/")
    if ".." in slash_path.split("/"):
        return False
    norm = posixpath.normpath(slash_path)
    parts = norm.split("/")
    if norm.startswith("/") or ABSOLUTE.match(path) or ".." in parts:
        return False
    if (role != "member" and names_claude(path)) or protected_part(parts, role):
        return False
    # The literal part before the first glob component is what is resolved (a glob cannot be resolved as a path); links
    # in that part are followed, so a member glob under a link that leaves the repository is refused.
    base_parts = []
    for p in parts:
        if any(c in p for c in "*?["):
            break
        base_parts.append(p)
    # Every literal prefix is resolved, not only the full path: a name that does not exist yet (a.tar) is resolved
    # lexically, so a link in the middle (lnk/a.tar with lnk -> outside) would otherwise be missed.
    prefixes = ["/".join(base_parts[:i]) for i in range(1, len(base_parts) + 1)] or ["."]
    k = None
    for pre in prefixes:
        _lex, real = resolve(pre, ctx)
        k = key(real)
        if not (k == ctx.root or inside(k, ctx.root)):
            return False
    if role != "member" and (k == ctx.git or inside(k, ctx.git) or inside_claude(real, ctx)):
        return False
    return True


def tar_rule(args, ctx):
    """The explicit tar grammar: the only tar forms that are allowed. A read is -t (list) of an explicit .tar archive. A
    write is -c (create) to an explicit archive. Everything else is DENY (fail closed): extraction (-x) writes to paths the
    archive names, which cannot be inspected from argv; old-style clusters; stdin (-) and default archives; compression
    letters and long options; globs, colons, absolute or .. paths and links that leave the repository in any operand."""
    ops, archive, directory, members = set(), None, None, []
    i, options_done = 0, False

    def value_of(rest):
        nonlocal i
        if rest:
            return rest
        if i >= len(args):
            raise Deny("tar", "tar option needs a value")
        v = args[i]
        i += 1
        return v

    while i < len(args):
        a = args[i]
        i += 1
        if a == "-":
            raise Deny("tar", "tar with stdin or stdout (-) is not inspected")
        if not options_done and a == "--":
            options_done = True
            continue
        if options_done or not a.startswith("-"):
            members.append(a)
            continue
        if a.startswith("--"):
            opt, eq, val = a[2:].partition("=")
            if opt in ("list", "create") and not eq:
                ops.add("t" if opt == "list" else "c")
            elif opt == "verbose" and not eq:
                pass
            elif opt in ("file", "directory"):
                v = val if eq else value_of("")
                if opt == "file":
                    if archive is not None:
                        raise Deny("tar", "tar with a repeated -f archive is not inspected")
                    archive = v
                else:
                    if directory is not None:
                        raise Deny("tar", "tar with a repeated -C directory is not inspected")
                    directory = v
            else:
                raise Deny("tar", "tar long option is not in the tar grammar: " + a)
            continue
        j = 1
        while j < len(a):
            ch = a[j]
            j += 1
            if ch in "tc":
                ops.add(ch)
            elif ch == "v":
                pass
            elif ch in "fC":
                v = value_of(a[j:])
                if ch == "f":
                    if archive is not None:
                        raise Deny("tar", "tar with a repeated -f archive is not inspected")
                    archive = v
                else:
                    if directory is not None:
                        raise Deny("tar", "tar with a repeated -C directory is not inspected")
                    directory = v
                break
            else:
                raise Deny("tar", "tar option letter is not in the tar grammar: " + ch)
    if ops != {"c"}:
        # Owner ruling: a read of an existing archive cannot be checked against the file tar actually reads (TOCTOU), and the
        # decompressor is chosen from its contents. Only create (-c) is allowed.
        raise Deny("tar", "tar is allowed only with -c (create): reads of an archive are not inspected (TOCTOU)")
    if archive is None:
        raise Deny("tar", "tar without an explicit -f archive (stdin, tape or default) is not inspected")
    if not tar_path_ok(archive, ctx, "archive"):
        raise Deny("tar", "tar archive is not an inspectable repository path")
    if directory is not None and not tar_path_ok(directory, ctx, "dir"):
        raise Deny("tar", "tar -C directory is not an inspectable repository path")
    for m in members:
        if not tar_path_ok(m, ctx, "member"):
            raise Deny("tar", "tar member name is not an inspectable repository path")
    # Piece D (#636): after the operand checks, so a tainted bad operand keeps
    # its original reason and only a would-be ALLOW is refused and taints.
    _require_untainted(ctx, "tar")
    _taint(ctx)


def program_option_rule(name, args, ctx=None):
    name = PROGRAM_ALIAS.get(name, name)
    if name not in PROGRAM_TOOLS:
        return
    # The guard reads the words the shell passes on, not the shell's own expansion or escape. For these tools an
    # argument that contains an expansion or a backslash cannot be checked here, so it is denied (fail closed).
    # Windows backslash paths in these tools are denied too; this is a documented over-denial.
    # Brace expansion and globs are expanded by the shell before the tool runs, so they can make an option name or
    # value that is not in the word the guard reads. A glob in a file argument does not form an option, so only
    # dashed arguments (and tar's first cluster word) are checked for globs.
    for a in args:
        if "\n" in a:
            raise Deny("program-option", name + " argument with a newline is not inspected")
        if "$" in a or "`" in a or SUBST in a or "\\" in a or brace_may_form_option(a, name):
            raise Deny("program-option", name + " argument with an expansion or escape is not inspected")
        # A glob that starts the word (*-compress-program, [-]-to-command) can expand to a dashed file name, so it is an
        # option; a glob after a letter (names*.txt) cannot start with a dash.
        if any(c in a for c in "*?[") and (a.startswith("-") or a[:1] in "*?["):
            raise Deny("program-option", name + " option with a glob is not inspected")
    if name == "tar":
        # tar has its own explicit grammar (tar_rule). The ALLOWLIST grammar below applies to the other tools only.
        if ctx is None:
            raise Deny("tar", "tar is decided with the working context; none was given")
        tar_rule(args, ctx)
        return
    allowlist_rule(name, args, ctx)
    # Piece D (#636): a validated write option (sort -o/--output) mutates
    # the filesystem. The check runs once, after full argv validation.
    _publish_pending_write(ctx, name)


SAFE_WRITE_OPTS = {"cp": {"-r", "-R", "-p", "-v", "-n"}, "mv": {"-v", "-n"}, "mkdir": {"-p", "-v"},
                   "touch": {"-a", "-m", "-c"}, "tee": {"-a"}}


def safe_write_rule(name, args, ctx):
    """cp, mv, mkdir, touch and tee (Owner, Round A). Every option must be listed for the command, and every operand must be
    a repository path that tar_path_ok accepts: inside the repository, literal, no .., not .claude/ or .git, and no link that
    leaves the repository. A glob, a variable or a path outside the repository is DENY. The check runs before the command;
    a path replaced after the check is a residual TOCTOU risk (docs/claude-destructive-guard.md)."""
    allowed = SAFE_WRITE_OPTS[name]
    for a in args:
        if any(c in a for c in "$`") or SUBST in a:
            raise Deny("unexpanded", name + " argument with an expansion is not inspected: " + a)
        if a == "--":
            continue
        if a.startswith("-") and a != "-":
            if a not in allowed:
                raise Deny("program-option", name + " option is not in the SAFE_WRITE_OPTS list: " + a)
            continue
        if not tar_path_ok(a, ctx, "dir"):
            raise Deny("program-option", name + " operand is not a plain repository path: " + a)
    # Piece D (#636): after the operand checks, so a tainted malformed write
    # keeps its original reason and only a would-be ALLOW is refused and taints.
    _require_untainted(ctx, name)
    _taint(ctx)


def dev_tool_opt_denied(name, a):
    """True if a dev-tool argument is one of the tool's denied options (see DEV_DENY_OPTS for the matching rule)."""
    key = a.split("=", 1)[0]
    for opt in DEV_DENY_OPTS.get(name, set()):
        if opt.startswith("--") or (opt.startswith("-") and len(opt) > 2):
            if key.startswith(opt):
                return True
        elif key.startswith("--"):
            continue
        elif key == opt or (key.startswith("-") and opt[1:] in key[1:]):
            return True
    return False


def dev_tool_rule(name, args, ctx):
    """Build and test tools (Owner decision, Round A option 4). A protected, outside, parent, home, glob, brace or unexpanded
    word is DENY (the parent directory is ..; a value after = or an attached short value is checked the same way). Options
    that run code or write outside the repository are DENY per tool (dev_tool_opt_denied), and so are the listed subcommands.
    The tools run project code that this hook cannot inspect (documented)."""
    deny_words = DEV_DENY_WORDS.get(name, set())
    for a in args:
        if any(c in a for c in "$`*?[]{}") or SUBST in a or "\n" in a:
            raise Deny("dev-tool", name + " argument with an expansion or glob is not inspected: " + a)
        if ".." in a or "~" in a:
            raise Deny("dev-tool", name + " argument names a parent or home path: " + a)
        if a.startswith("-") and dev_tool_opt_denied(name, a):
            raise Deny("dev-tool", name + " option runs code or writes outside the repository: " + a)
        if a in deny_words:
            raise Deny("dev-tool", name + " subcommand runs code or changes the machine: " + a)
        # A program named by a linker or wrapper option (rustc -C linker=, cargo rustc-wrapper) runs in the build.
        if re.search(r"(?i)linker|wrapper|runner", a):
            raise Deny("dev-tool", name + " option names a program the build runs: " + a)
        # A bare protected name (make -C .git, make -C .claude) is checked as well as a path-shaped word.
        if names_claude(a) or protected_part([a], "dir"):
            raise Deny("dev-tool", name + " argument names a protected component: " + a)
        value = a.split("=", 1)[1] if "=" in a else a
        if "/" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
            if (not tar_path_ok(value, ctx, "member") or names_claude(value)
                    or protected_part(value.replace("\\", "/").split("/"), "dir")):
                raise Deny("dev-tool", name + " argument names a path outside the repository or a protected one: " + a)


def gzip_rule(args, ctx):
    """Every gzip form is DENY (Owner D2). The file gzip reads is checked before the command runs, and the check cannot be
    tied to the file it opens: a symlink or a replacement between the two is not covered. Without that guarantee the form
    is not allowed, and this is fail-closed for gzip -c, gzip -dc and gzip FILE alike."""
    raise Deny("gzip", "gzip is DENY: the checked file cannot be tied to the file it reads (TOCTOU)")


def find_rule(args, ps, ctx, depth, fed=False):
    if "-delete" in args:
        raise Deny("find-delete", "find -delete is denied")
    if any(a in ("-fprint", "-fprint0", "-fprintf", "-fls") for a in args):
        raise Deny("find-write", "find -fprint/-fls writes a named file: DENY (Owner Round 13 audit)")
    for i, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < len(args) and args[j] not in (";", "+"):
                j += 1
            if j == i + 1:
                raise Deny("find", "find -exec without a command")
            # Piece D (#636): -exec runs in a child process, so its cwd
            # never reaches the parent; taint still merges (a child can
            # change the filesystem the parent later judges). Issue #637:
            # the -exec/-execdir child shares this segment's stdin, so fed
            # is passed. A -ok/-okdir child does not: GNU find consumes the
            # pipe itself for the confirmation answer and hands the child
            # /dev/null (verified: `echo | find -ok cat` shows the child
            # stdin closed), so only the argv is validated, untainted.
            _nested_argv(args[i + 1:j], ctx, ps, depth + 1,
                         fed if a in ("-exec", "-execdir") else False)


# Commands on this list must not run another program, whatever their options. sort (--compress-program=PROG) and
# rg (--pre=PROG) do run a named program, so they are NOT here; they are treated as possible wrappers (reviewer
# CRITICAL on 714345c). Keep this list to commands whose options cannot name a program.
NON_EXEC_COMMANDS = frozenset({
    "echo", "printf", "grep", "egrep", "fgrep", "cat", "ls", "pwd", "cd", "head", "tail", "wc",
    "uniq", "cut", "tr", "which", "type", "test", "true", "false", "diff", "file", "stat", "du", "df", "basename",
    "dirname", "realpath", "date", "touch", "mkdir"})


def _cmd_name(word):
    n = posixpath.basename(word.replace("\\", "/")).lower()
    return n[:-4] if n.endswith(".exe") else n


def _call_target(words, ps):
    """Command-position function-call resolution (Issue #637).

    Returns (fkey, saw_coproc, first_idx): the exact name for function lookup
    after KEYWORDS/assignments and same-shell prefixes, whether a `coproc`
    prefix was skipped (the call then forks: no exec leaks back), and the
    first meaningful index (for the NON_EXEC documentation check). Only the
    command position counts (`echo f` is not a call); external wrappers
    (`sudo`, `env`, ...) do not run shell functions. Bash resolves
    case-sensitively and prefers same-name functions over builtins
    (MAJOR-A); PowerShell folds case.
    """
    j = 0
    while j < len(words) and (words[j] in KEYWORDS or ASSIGN.match(words[j])):
        j += 1
    k = j
    saw_coproc = False
    # `time` runs its pipeline in a forked subshell, but still resolves shell
    # functions, so a pipe-fed `time -p f` must inspect f's body. Bash `time`
    # takes only `-p`; anything else option-like is not skipped (fail-closed:
    # the call then does not resolve and the suffix pass judges `f` alone).
    while k < len(words) and words[k] in ("time", "coproc"):
        if words[k] == "time":
            k += 1
            if k < len(words) and words[k] == "-p":
                k += 1
            continue
        saw_coproc = True
        k += 1
    fkey = _func_call_name(words[k], ps) if k < len(words) else None
    return fkey, saw_coproc, j


def _func_key(name, ps):
    """Function-definition/call lookup key: Bash is case-sensitive, PowerShell is not."""
    return name.lower() if ps else name


def _func_call_name(word, ps):
    """Exact quote-removed name for function-call resolution (review 5968136032).

    Bash resolves the exact name (no basename, no .exe strip, case-sensitive);
    `f.exe` (cat) must not resolve to `f` (node). PowerShell folds case.
    Executable-path normalization stays separate (command_name, _cmd_name)."""
    return word.lower() if ps else word


def python_position_args(words):
    """The arguments of the python process this segment runs, or None if python does not run here.

    The command word is the first word after keywords and assignments. A known non-executing command (echo, grep,
    printf, ...) runs no python. Any other command may be a wrapper, known or not (nice, taskset -c 0, uv run,
    busybox, ...): then python is the first later word whose name is python, so no prefix hides it. Words come from
    words_of, so quotes and backslashes are already removed."""
    i = 0
    while i < len(words) and (words[i] in KEYWORDS or ASSIGN.match(words[i])):
        i += 1
    if i >= len(words) or _cmd_name(words[i]) in NON_EXEC_COMMANDS:
        return None
    for j in range(i, len(words)):
        if re.fullmatch(r"python[0-9.]*|py", _cmd_name(words[j])):
            return words[j + 1:]
    return None


# Bash expands or splits on these when they are unquoted. Inside double quotes only $, backtick and backslash are
# special. Single quotes are fully literal. A process or command substitution changes argv (SUBST, below).
# Input redirection (<) and here-strings give python code over stdin, so < is special. Output redirection (>) does
# not change argv or code; its target is checked by the redirect rules, so > is not special.
UNQUOTED_SPECIAL = frozenset("$`{}*?[]~\\()|&;#!<")
DQUOTED_SPECIAL = frozenset("$`\\")


def python_word_certainty(seg):
    """True if the raw segment of a python command has no special character outside single quotes, and no
    $, backtick or backslash inside double quotes. Then the argv bash passes is exactly the quote-removed words,
    so the guard judges the same argv. Otherwise the argv depends on expansion and is not certain."""
    if SUBST in seg:
        # A command or process substitution outside the python argv changes the argv (the substituted text or a
        # /dev/fd path becomes a word). It is not certain.
        return False
    state = None
    word_start = True
    for ch in seg:
        if state == "'":
            if ch == "'":
                state = None
        elif state == '"':
            if ch == '"':
                state = None
            elif ch in DQUOTED_SPECIAL:
                return False
        elif ch == "#" and word_start:
            # A word-initial # starts a comment, exactly as split_segments tokenises it: the rest of the
            # segment is not part of any word, so it cannot change the argv.
            break
        elif ch in "'\"":
            state = ch
        elif ch in UNQUOTED_SPECIAL:
            return False
        word_start = ch in " \t;&|()"
    return state is None


def python_c_codes(args):
    """Every code string of a python -c option. In a short-option bundle, -c takes the rest of the bundle as its
    code ("-copen(...)", "-Icopen(...)"); only an empty rest takes the next argument. -W, -X and -m take the rest of
    the bundle as a value, so a "c" after them is not -c, and a "c" after them is kept as a candidate (fail-closed)."""
    codes = []
    for i, a in enumerate(args):
        if a.startswith("-") and not a.startswith("--"):
            flags = a[1:]
            for j, ch in enumerate(flags):
                if ch == "c":
                    rest = flags[j + 1:]
                    codes.append(rest if rest else (args[i + 1] if i + 1 < len(args) else ""))
                    break
                if ch in "WXm":
                    if "c" in flags[j + 1:]:
                        codes.append(args[i + 1] if i + 1 < len(args) else "")
                    break
    return codes


def _drop_redirect_words(args):
    """argv without redirections: a marked operator and the word after it (its target) select no program."""
    out, skip = [], False
    for w in args:
        if skip:
            skip = False
            continue
        if w.startswith(REDIR_MARK):
            skip = True
            continue
        out.append(w)
    return out


def _python_program(args):
    """How a python argv selects its program: 'code' (-c), 'module' (-m), 'file' (a script path),
    'stdin' (a standalone `-`), or 'none' (flags only: interactive). A single left-to-right scan, so
    `python3 - -c x` is 'stdin' (the `-c` becomes that run's argv, not its program). Only -W/-X take a
    separate value and only --check-hash-based-pycs does among long options; every other -x is a flag.
    Never raises; redirect words are not program words."""
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            i += 2 if (a == '--check-hash-based-pycs' and i + 1 < n) else 1
            continue
        if a.startswith('-') and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch == 'c':
                    return 'code'
                if ch == 'm':
                    return 'module'
                if ch in 'WX':
                    if j + 1 == len(a):
                        i += 1  # the value is the next word
                    break
                j += 1
            i += 1
            continue
        return 'file'
    return 'none'


_NODE_CODE_SHORTS = {"e", "p"}
_NODE_VALUE_SHORTS = {"r", "C"}
_NODE_FLAG_SHORTS = {"c", "i", "h", "v"}
_NODE_CODE_LONGS = {"--eval", "--print"}
_NODE_VALUE_LONGS = {
    "--allow-fs-read",
    "--allow-fs-write",
    "--build-snapshot-config",
    "--conditions",
    "--cpu-prof-dir",
    "--cpu-prof-interval",
    "--cpu-prof-name",
    "--diagnostic-dir",
    "--disable-proto",
    "--disable-warning",
    "--dns-result-order",
    "--env-file",
    "--env-file-if-exists",
    "--eval",
    "--experimental-config-file",
    "--experimental-default-type",
    "--experimental-loader",
    "--experimental-sea-config",
    "--experimental-test-isolation",
    "--heap-prof-dir",
    "--heap-prof-interval",
    "--heap-prof-name",
    "--heapsnapshot-near-heap-limit",
    "--heapsnapshot-signal",
    "--icu-data-dir",
    "--import",
    "--input-type",
    "--inspect-port",
    "--inspect-publish-uid",
    "--localstorage-file",
    "--max-http-header-size",
    "--max-old-space-size-percentage",
    "--network-family-autoselection-attempt-timeout",
    "--openssl-config",
    "--redirect-warnings",
    "--report-dir",
    "--report-filename",
    "--report-signal",
    "--require",
    "--run",
    "--secure-heap",
    "--secure-heap-min",
    "--snapshot-blob",
    "--test-concurrency",
    "--test-coverage-branches",
    "--test-coverage-exclude",
    "--test-coverage-functions",
    "--test-coverage-include",
    "--test-coverage-lines",
    "--test-name-pattern",
    "--test-reporter",
    "--test-reporter-destination",
    "--test-shard",
    "--test-skip-pattern",
    "--test-timeout",
    "--title",
    "--tls-cipher-list",
    "--tls-keylog",
    "--trace-event-categories",
    "--trace-event-file-pattern",
    "--trace-require-module",
    "--unhandled-rejections",
    "--use-largepages",
    "--v8-pool-size",
    "--watch-kill-signal",
    "--watch-path",
    "--loader",
    "--report-directory",
    "--debug-port",
    "--max-old-space-size",
}
_NODE_FLAG_LONGS = {
    "--abort-on-uncaught-exception",
    "--allow-addons",
    "--allow-child-process",
    "--allow-wasi",
    "--allow-worker",
    "--build-snapshot",
    "--check",
    "--completion-bash",
    "--cpu-prof",
    "--debug-port",
    "--disable-sigusr1",
    "--disable-wasm-trap-handler",
    "--disallow-code-generation-from-strings",
    "--enable-etw-stack-walking",
    "--enable-fips",
    "--enable-network-family-autoselection",
    "--enable-source-maps",
    "--entry-url",
    "--experimental-addon-modules",
    "--experimental-async-context-frame",
    "--experimental-default-config-file",
    "--experimental-eventsource",
    "--experimental-import-meta-resolve",
    "--experimental-inspector-network-resource",
    "--experimental-network-inspection",
    "--experimental-permission",
    "--experimental-print-required-tla",
    "--experimental-require-module",
    "--experimental-test-coverage",
    "--experimental-test-module-mocks",
    "--experimental-transform-types",
    "--experimental-vm-modules",
    "--experimental-webstorage",
    "--experimental-worker-inspection",
    "--expose-gc",
    "--force-context-aware",
    "--force-fips",
    "--force-node-api-uncaught-exceptions-policy",
    "--frozen-intrinsics",
    "--heap-prof",
    "--help",
    "--huge-max-old-generation-size",
    "--insecure-http-parser",
    "--inspect",
    "--inspect-brk",
    "--inspect-wait",
    "--interactive",
    "--interpreted-frames-native-stack",
    "--jitless",
    "--loader",
    "--max-old-space-size",
    "--no-addons",
    "--no-deprecation",
    "--no-experimental-detect-module",
    "--no-experimental-fetch",
    "--no-experimental-global-customevent",
    "--no-experimental-global-navigator",
    "--no-experimental-global-webcrypto",
    "--no-experimental-repl-await",
    "--no-experimental-require-module",
    "--no-experimental-sqlite",
    "--no-experimental-strip-types",
    "--no-experimental-websocket",
    "--no-extra-info-on-fatal-exception",
    "--no-force-async-hooks-checks",
    "--no-global-search-paths",
    "--no-network-family-autoselection",
    "--no-warnings",
    "--node-memory-debug",
    "--openssl-legacy-provider",
    "--openssl-shared-config",
    "--pending-deprecation",
    "--permission",
    "--preserve-symlinks",
    "--preserve-symlinks-main",
    "--print",
    "--prof",
    "--prof-process",
    "--report-compact",
    "--report-directory",
    "--report-exclude-env",
    "--report-exclude-network",
    "--report-on-fatalerror",
    "--report-on-signal",
    "--report-uncaught-exception",
    "--test",
    "--test-force-exit",
    "--test-only",
    "--test-update-snapshots",
    "--throw-deprecation",
    "--tls-max-v1.2",
    "--tls-max-v1.3",
    "--tls-min-v1.0",
    "--tls-min-v1.1",
    "--tls-min-v1.2",
    "--tls-min-v1.3",
    "--trace-atomics-wait",
    "--trace-deprecation",
    "--trace-env",
    "--trace-env-js-stack",
    "--trace-env-native-stack",
    "--trace-exit",
    "--trace-promises",
    "--trace-sigint",
    "--trace-sync-io",
    "--trace-tls",
    "--trace-uncaught",
    "--trace-warnings",
    "--track-heap-objects",
    "--use-bundled-ca",
    "--use-env-proxy",
    "--use-openssl-ca",
    "--use-system-ca",
    "--v8-options",
    "--version",
    "--watch",
    "--watch-preserve-output",
    "--zero-fill-buffers",
}
def _node_program(args):
    """How a node argv selects its program (Issue #637, MAJOR fix).

    Returns 'code' (-e/--eval, -p/--print name code in argv), 'file' (a script
    file names the program), 'stdin' (a standalone `-` names stdin), 'none'
    (flags only, no program word), or 'unknown' (an option form the guard does
    not model: fail-closed, treated like 'none' when stdin is piped or
    redirected, but never as a file). Only -r/-C take a separate value and only
    the --xxx=... longs take a value in the same word; every other short/long
    without a value is a flag. `inspect` after the options is a subcommand, not
    a file: `node inspect script.js` still names a file, `node inspect` alone
    names none. Never raises; redirect words are not program words.
    """
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    seen_subcommand = False
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            key, eq, _val = a.partition('=')
            if eq:
                if key in _NODE_CODE_LONGS:
                    return 'code'
                if key in _NODE_VALUE_LONGS:
                    i += 1
                    continue
                return 'unknown'
            if a in _NODE_CODE_LONGS:
                return 'code'
            if a in _NODE_VALUE_LONGS:
                i += 2
                continue
            if a in _NODE_FLAG_LONGS:
                i += 1
                continue
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in _NODE_CODE_SHORTS:
                    return 'code'
                if ch in _NODE_VALUE_SHORTS:
                    if j + 1 == len(a):
                        i += 1
                    break
                if ch in _NODE_FLAG_SHORTS:
                    j += 1  # flag letter: next cluster letter
                    continue
                return 'unknown'
            i += 1
            continue
        if not seen_subcommand and a == 'inspect':
            seen_subcommand = True
            i += 1
            continue
        return 'file'
    return 'none'


def _perl_program(args):
    """How a perl argv selects its program: 'code' (-e/-E), 'file', 'stdin' (-),
    'none' (flags only), or 'unknown' (unmodelled option: fail-closed)."""
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in ('e', 'E'):
                    return 'code'
                if ch in ('I', 'F', 'D', 'C', '0', 'l', 'i', 'm', 'M', 'd', 'x', 'V'):
                    if j + 1 == len(a):
                        i += 1
                    break
                if ch in ('a', 'c', 'f', 'g', 'n', 'p', 's', 'S', 't', 'T', 'u', 'U', 'v', 'w', 'W', 'X'):
                    j += 1  # perl flag: next cluster letter
                    continue
                return 'unknown'
            i += 1
            continue
        return 'file'
    return 'none'


def _ruby_program(args):
    """How a ruby argv selects its program: 'code' (-e), 'file', 'stdin' (-),
    'none', or 'unknown' (fail-closed). Value-taking shorts consume a separate
    word when not attached; unknown shorts/longs are unknown."""
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch == 'e':
                    return 'code'
                if ch in ('r', 'I', 'K', 'C', 'E', 'F', '0', 'T', 'W', 'X', 'x', 'i'):
                    if j + 1 == len(a):
                        i += 1
                    break
                if ch in ('a', 'c', 'd', 'l', 'n', 'p', 's', 'S', 'U', 'v', 'V', 'w', 'y'):
                    j += 1  # ruby flag: next cluster letter
                    continue
                return 'unknown'
            i += 1
            continue
        return 'file'
    return 'none'


def _php_program(args):
    """How a php argv selects its program: 'code' (-r/-B/-R/-E), 'file' (a script
    or -f/-F file), 'stdin' (-), 'none', or 'unknown' (fail-closed)."""
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            # -f/-F name a file: `-f -` reads the program from stdin.
            if len(a) >= 2 and a[1] in ('f', 'F'):
                rest = a[2:]
                if rest:
                    return 'stdin' if rest == '-' else 'file'
                if i + 1 >= n:
                    return 'unknown'
                nxt = args[i + 1]
                if nxt == '-':
                    return 'stdin'
                if nxt.startswith('-') and nxt != '-':
                    return 'unknown'
                return 'file'
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in ('r', 'B', 'R', 'E'):
                    return 'code'
                if ch in ('d', 'c', 'z', 'H', 's'):
                    if j + 1 == len(a):
                        i += 1
                    break
                if ch in ('a', 'n', 'e', 'h', 'i', 'l', 'm', 'v', 'w', 'q'):
                    j += 1  # php flag: next cluster letter
                    continue
                return 'unknown'
            i += 1
            continue
        return 'file'
    return 'none'


def _lua_program(args):
    """How a lua argv selects its program: 'code' (-e), 'file', 'stdin' (-),
    'none', or 'unknown' (fail-closed)."""
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none'
            return 'stdin' if rest[0] == '-' else 'file'
        if a == '-':
            return 'stdin'
        if a.startswith('--'):
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch == 'e':
                    return 'code'
                if ch == 'l':
                    if j + 1 == len(a):
                        i += 1
                    break
                if ch in ('i', 'v', 'E', 'W'):
                    j += 1  # lua flag: next cluster letter
                    continue
                return 'unknown'
            i += 1
            continue
        return 'file'
    return 'none'


def _awk_program(args):
    """How an awk/gawk/mawk argv selects its program.

    Returns 'file' when a program is named in argv (a -f file, a -W exec file,
    or a program-text word), 'stdin' when -f names `-` (program from stdin) or
    a bare `-` selects stdin with no program text, 'none' (flags only), or
    'unknown' (unmodelled -W form: fail-closed). A non-option word is program
    text (stdin is then data, not the program), and a `-` data file after
    program text does not select stdin as the program.
    """
    args = _drop_redirect_words(args)
    i, n = 0, len(args)
    seen_program_text = False
    while i < n:
        a = args[i]
        if a == '--':
            rest = args[i + 1:]
            if not rest:
                return 'none' if not seen_program_text else 'file'
            if rest[0] == '-' and not seen_program_text:
                return 'stdin'
            return 'file'
        if a == '-':
            if seen_program_text:
                i += 1
                continue
            return 'stdin'
        if a.startswith('--'):
            return 'unknown'
        if a.startswith('-') and len(a) > 1:
            # -f file, -F sep, -v var=value, -W opt (mawk/gawk).
            if len(a) >= 2 and a[1] == 'f':
                rest = a[2:]
                if rest:
                    if rest == '-':
                        return 'stdin'
                    return 'file'
                if i + 1 >= n:
                    return 'unknown'
                nxt = args[i + 1]
                if nxt == '-':
                    return 'stdin'
                if nxt.startswith('-') and nxt != '-':
                    return 'unknown'
                return 'file'
            if len(a) >= 2 and a[1] in ('F', 'v'):
                if len(a) == 2:
                    i += 2
                    continue
                i += 1
                continue
            if len(a) >= 2 and a[1] == 'W':
                rest = a[2:]
                if rest:
                    if rest == 'exec':
                        if i + 1 >= n:
                            return 'unknown'
                        nxt = args[i + 1]
                        if nxt == '-':
                            return 'stdin'
                        if nxt.startswith('-') and nxt != '-':
                            return 'unknown'
                        return 'file'
                    i += 1
                    continue
                if i + 1 >= n:
                    return 'unknown'
                nxt = args[i + 1]
                if nxt == 'exec':
                    if i + 2 >= n:
                        return 'unknown'
                    nxt2 = args[i + 2]
                    if nxt2 == '-':
                        return 'stdin'
                    if nxt2.startswith('-') and nxt2 != '-':
                        return 'unknown'
                    return 'file'
                i += 2
                continue
            # Short cluster without f/F/v/W: only flags are expected; any other
            # letter is unknown (fail-closed).
            j = 1
            only_flags = True
            while j < len(a):
                ch = a[j]
                if ch in ('F', 'v', 'W', 'f'):
                    only_flags = False
                    break
                # mawk/gawk have no other single-letter flags that take no value
                # besides the ones above; treat the rest as unknown.
                return 'unknown'
            if only_flags:
                i += 1
                continue
            i += 1
            continue
        seen_program_text = True
        return 'file'
    if seen_program_text:
        return 'file'
    return 'none'


def _interp_program(name, args):
    """Program selection for a non-python interpreter (Issue #637)."""
    if name == 'node':
        return _node_program(args)
    if name == 'perl':
        return _perl_program(args)
    if name == 'ruby':
        return _ruby_program(args)
    if name == 'php':
        return _php_program(args)
    if name in ('lua',):
        return _lua_program(args)
    if name in ('awk', 'gawk'):
        return _awk_program(args)
    return 'unknown'


def _find_matching_brace(text, start):
    """Find matching "}" for "{" at start. Returns (inside_text, end_idx_after_closing).
    Fail-closed: return None on unbalanced/too deep/unparseable (treated as unknown body -> unsafe).
    Handles single/double quotes, backslash escapes, # comments (to newline),
    $(...), backticks, <(...)/>(...) skipping (fail-closed None on failure)."""
    # start must be "{" ?
    if start >= len(text) or text[start] != "{":
        return None
    depth = 1
    quote = None
    i = start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:  # Bash backslash escapes next char (also inside ""? approximate)
            # In PowerShell, backtick escapes, not backslash; treating backslash as escape is safe over-approx (may miss brace, fail-closed via depth? Actually skipping escaped char could miss quote handling? Safe: skip next char (may incorrectly skip, but then brace matching may fail -> None -> unsafe -> DENY, fail-closed safe).
            i += 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            # Skip $(...) via _matched-like depth counting with quotes? Simplify: use _matched logic? To avoid dependency, do manual with quote awareness? For minimal, try to find matching ")" via _matched equivalent? Use simple depth with quotes? Hmm.
            # For fail-closed, on any complexity, return None? Actually $(...) may contain braces (e.g., $(echo "}")). Must skip correctly, else brace depth miscount -> over/under-taint? Under-taint unsafe (leak), over-taint safe. To be safe (fail-closed), on encountering "$(", return None (unknown) -> unsafe -> DENY tainted calls (safe). Good - simple fail-closed for $(...) inside function body braces? But many function bodies contain $(...)? Example f() { echo "$(node)"; }? Unlikely in required tests (bodies are simple node/cat/script). Returning None (unsafe) for any body containing $(...) would cause over-denial for safe bodies with $(...)? Example f() { echo "$(cat)"; }; printf data | f - body contains $(...), our matcher would return None (unknown) -> unsafe -> DENY tainted call (over-denial, safe? Actually body echo "$(cat)" with tainted stdin - inner cat reads...? Hmm, safe? Over-denial safe. Acceptable per fail-closed.
            # Actually task requires subs in function bodies? Not explicitly, but nested subs + functions combo should be considered. Our simple fail-closed (unknown on $() inside {}) would DENY tainted calls with subs in body (safe over-denial). Acceptable.
            return None
        if c == "`":
            # Skip backticks: find closing unescaped backtick (fail-closed None on failure)
            j = text.find("`", i + 1)
            if j < 0:
                return None
            i = j + 1
            continue
        if c in "<>" and quote is None and i + 1 < n and text[i + 1] == "(":
            # Process substitution <(...) / >(...): skip via paren matching (fail-closed None on failure)
            # Find matching ")" for "(" at i+1 with quote awareness? Simplify: return None (unknown) to be fail-closed (safe) since process subst in function body is exotic.
            return None
        if quote == '"':
            if c == '"':
                quote = None
            i += 1
            continue
        if quote is None and c == "#" and (i == 0 or text[i-1] in " \t\n;&|(){}"):
            # Word-initial # starts comment to end of line (approximate split_segments logic, plus {,})
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if c == "{":
            depth += 1
            if depth > 8:
                return None
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (text[start+1:i], i+1)
            if depth < 0:
                return None
        i += 1
    return None

def _extract_function_defs(text, ps):
    """Extract shell function definitions: name -> list of body texts (or None for unknown/unparseable -> unsafe).
    Handles Bash name() { ... }, name() ( ... ), function name [()] { ... } / ( ... ). PowerShell function name { ... }.
    Fail-closed: unparseable/too deep/unknown forms yield None body (treated as unsafe)."""
    defs = {}
    # Review 5968136032: exclude `#` comments from discovery (a commented
    # `f() { node; }` must not taint the real `f() { cat; }`). Replace comment
    # chars with spaces (same length, indices preserved; newline kept).
    # `#` starts a comment only outside quotes at word start (blank or
    # `;|&|(){}`, newline, start); `\#` is literal. Over-stripping is
    # fail-closed safe (may miss a `#`-containing name, then unknown-call
    # handling denies piped calls).
    _chars = list(text)
    _q = None
    _word_start = True
    _i = 0
    _L = len(text)
    while _i < _L:
        _c = text[_i]
        if _q == "'":
            if _c == "'":
                _q = None
            _word_start = False
            _i += 1
            continue
        if _c == "\\" and _i + 1 < _L:
            _word_start = False
            _i += 2
            continue
        if _q == '"':
            if _c == '"':
                _q = None
            _word_start = False
            _i += 1
            continue
        if _c in "'\"":
            _q = _c
            _word_start = False
            _i += 1
            continue
        if _c == "#" and _word_start:
            while _i < _L and text[_i] != "\n":
                _chars[_i] = " "
                _i += 1
            _word_start = False
            continue
        if _c in " \t\n" or _c in ";|&(){}:":
            _word_start = True
        else:
            _word_start = False
        _i += 1
    clean = "".join(_chars)
    # Pattern for name() with { or ( body opener after optional spaces (including newlines)
    # Use regex to find candidates, then brace/paren matching for body (fail-closed None on failure).
    # To avoid missing due to newlines, use \s* (includes \n).
    import re as _re
    # Bash name() { ... } and name() ( ... )
    # MAJOR (review 5967514206): Bash function names are case-sensitive and
    # allow many non-metacharacters (verified: F-X, f+x, f?, f*, f%, f/,
    # f@, f!, f^, f~, f,, 1f). The previous [A-Za-z0-9_.:\-] missed `+`
    # (and digit-leading names), causing fail-open misses. Use a permissive
    # WORD pattern (any non-metacharacter, non-`=`, non-space) and over-match
    # quoted/candidates (fail-closed over-denial, never silent ALLOW).
    # PowerShell names resolve case-insensitively (keyed lower); Bash keys
    # keep original case and are stored/looked up case-sensitively.
    for m in _re.finditer(r'(?<![^\s;|&<>()=])([^\s;|&<>()=]+)\s*\(\s*\)\s*(\{|\()', clean):
        name = m.group(1)
        key = _func_key(name, ps)
        opener = m.group(2)
        # MAJOR (Issue #637): a `(...)` body runs in a subshell, so its
        # `exec` never reaches the caller. The stored body keeps its
        # parentheses, so the stdin scan sees the fork boundary (a brace
        # body stays bare: it runs in the calling shell). Verified on Bash:
        # `f() (exec < f); f; node` leaves the caller stdin alone, while
        # `f() { exec < f; }; f; node` redirects it.
        body_start_idx = m.start(2)  # index of "{" or "("
        # Skip if opener inside quotes/comments? Regex over-matches quoted (fail-closed safe: over-denial). Accept.
        if opener == "{":
            res = _find_matching_brace(clean, body_start_idx)
            if res is None:
                body = None
            else:
                body_inside, _end = res
                body = body_inside
        else:  # "(" subshell body: use paren matching with quote awareness? Simplify: use _matched-like? For minimal, try to find matching ")" via simple depth with quotes (fail-closed None on failure).
            # Find matching ")" for "(" at body_start_idx
            depth = 1
            quote = None
            i = body_start_idx + 1
            n = len(clean)
            found = None
            while i < n:
                c = clean[i]
                if quote == "'":
                    if c == "'":
                        quote = None
                    i += 1
                    continue
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == "$" and i + 1 < n and clean[i+1] == "(":
                    # Nested $(...): fail-closed unknown (return None for whole body to be safe? Actually body contains $(...), analyzing body with subs handling will handle subs separately? For body extraction, $(...) inside (...) body - need to skip correctly, else paren depth miscount. Fail-closed: return None (unknown -> unsafe).
                    found = None
                    break
                if c == "`":
                    j = clean.find("`", i+1)
                    if j < 0:
                        found = None
                        break
                    i = j + 1
                    continue
                if quote == '"':
                    if c == '"':
                        quote = None
                    i += 1
                    continue
                if c in "'\"":
                    quote = c
                    i += 1
                    continue
                if c == "(":
                    depth += 1
                    if depth > 8:
                        found = None
                        break
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        found = (clean[body_start_idx+1:i], i+1)
                        break
                    if depth < 0:
                        found = None
                        break
                i += 1
            if found is None:
                body = None
            else:
                body, _end = found
                body = "(" + body + ")"  # subshell scope marker (see above)
        defs.setdefault(key, []).append(body)
    # function name [()] { ... } / ( ... )  (Bash + PowerShell)
    for m in _re.finditer(r'(?<![^\s;|&<>()=])function\s+([^\s;|&<>()=]+)\s*(\(\s*\))?\s*(\{|\()', clean):
        name = m.group(1)
        key = _func_key(name, ps)
        opener = m.group(3)
        body_start_idx = m.start(3)
        if opener == "{":
            res = _find_matching_brace(clean, body_start_idx)
            body = None if res is None else res[0]
        else:
            # "(" subshell body after function: same paren matching as above (fail-closed None on $(), backticks, etc.)
            depth = 1
            quote = None
            i = body_start_idx + 1
            n = len(clean)
            found = None
            while i < n:
                c = clean[i]
                if quote == "'":
                    if c == "'":
                        quote = None
                    i += 1
                    continue
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == "$" and i + 1 < n and clean[i+1] == "(":
                    found = None
                    break
                if c == "`":
                    j = clean.find("`", i+1)
                    if j < 0:
                        found = None
                        break
                    i = j + 1
                    continue
                if quote == '"':
                    if c == '"':
                        quote = None
                    i += 1
                    continue
                if c in "'\"":
                    quote = c
                    i += 1
                    continue
                if c == "(":
                    depth += 1
                    if depth > 8:
                        found = None
                        break
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        found = (clean[body_start_idx+1:i], i+1)
                        break
                    if depth < 0:
                        found = None
                        break
                i += 1
            body = None if found is None else found[0]
            if body is not None and opener == "(":
                body = "(" + body + ")"  # subshell scope marker (see above)
        defs.setdefault(key, []).append(body)
    return defs


def _stdin_op_effect(op, target):
    """Effect of one `<`-family redirect operator on fd 0 (Issue #637).

    op includes an optional fd-digit prefix (e.g. '3<', '<', '<<', '<<<',
    '<&', '<>'). Returns 'supply' (fd 0 reads a new source), 'close' (fd 0
    is closed: provably delivers no program), or None (another descriptor
    or an unrecognised form: no stdin effect). Only fd 0/default supplies
    stdin; `3<`, `3<&-` and friends touch another descriptor. `<&N` dups
    onto stdin (supply); `<&-` closes it. `<>` opens fd 0 read-write.
    """
    m = re.match(r"^(\d*)(.*)$", op, re.S)
    fd, base = m.group(1), m.group(2)
    if fd not in ("", "0"):
        return None
    if base in ("<", "<<", "<<<", "<<-", "<>"):
        return "supply"
    if base == "<&":
        return "close" if (target or "") == "-" else "supply"
    return None


def _scan_stdin_ops(seg):
    """Yield (op, target) for each unquoted `<`-family operator in a segment.

    Works on lexer-marked segments (operators stand off as `\x01<op>` words,
    fd digits glued inside the marker) and on raw text alike. Quoted `<`
    is literal; `<(` is process substitution, not a redirect. The target is
    the next unquoted word (None when quoted: fail-closed, never `-`).
    """
    ops = []
    quote, i, n = None, 0, len(seg)
    while i < n:
        c = seg[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            i += 2  # an escaped `<` is a literal argument, not a redirect
            continue
        if quote == '"':
            if c == '"':
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            continue
        if c == "<" and not (i + 1 < n and seg[i + 1] == "("):
            # Glued fd digits immediately before `<` (the marked form keeps
            # them inside the `\x01<op>` marker). No digits = default fd 0.
            k = i - 1
            fd = ""
            while k >= 0 and seg[k].isdigit():
                fd = seg[k] + fd
                k -= 1
            j = i + 1
            while j < n and seg[j] in "<&":
                j += 1
            op = fd + seg[i:j]
            t = j
            while t < n and seg[t] in " \t":
                t += 1
            if t < n and seg[t] in "'\"":
                target = None
            else:
                u = t
                while u < n and seg[u] not in " \t" + REDIR_MARK + "'\"":
                    u += 1
                target = seg[t:u]
            ops.append((op, target))
            i = j
            continue
        i += 1
    return ops


def _has_input_redirect(seg):
    """True when a segment contains an unquoted `<` supplying stdin (fd 0/default).

    Only fd 0/default supplies stdin (`3<`, `3<&-` do not); `<&N` dups onto
    stdin (supply) while `<&-` closes it (no program can be delivered).
    `<(` is process substitution, not stdin redirection. Quoted `<` and
    backslash-escaped `<` are literal arguments.
    """
    for op, target in _scan_stdin_ops(seg):
        if _stdin_op_effect(op, target) == "supply":
            return True
    return False


def _exec_shadowed_by_function(words, ps, all_funcs):
    """True when a segment `exec` resolves to a shell function (Issue #637).

    Bash prefers a same-name function over the builtin, and a function
    invocation's redirects are temporary (restored afterwards, verified on
    Bash), so a shadowed `exec <&-` never closes the shell's stdin while
    `command exec`/`builtin exec` bypass functions and do. Only closes
    consult this: a shadowed supply stays fail-closed (still taints), since
    a definition placed after the use would not have shadowed it yet.
    """
    if _func_call_name("exec", ps) not in (all_funcs or {}):
        return False
    i = 0
    for _ in range(4):
        while i < len(words) and (words[i] in KEYWORDS or ASSIGN.match(words[i])):
            i += 1
        if i < len(words) and words[i] == "!":
            i += 1
            continue
        if i < len(words) and words[i] == "time":
            i += 1
            if i < len(words) and words[i] == "-p":
                i += 1
            continue
        if i < len(words) and words[i] in ("builtin", "command"):
            return False
        break
    return i < len(words) and words[i] == "exec"


def _exec_segment_effect(words, seg):
    """Persistent stdin effect of one segment: 'supply', 'close', or None.

    Only a segment whose command is bare `exec` (no command word after its
    options) changes the current shell's descriptors persistently (verified
    on Bash): with a command, `exec` replaces the shell (later segments are
    dead code, analysed as usual) or exits, so only the segment's own
    redirects matter there. `exec [-cl] [-a name]` options are skipped; an
    unknown option form counts as having a command (fail-closed: 'supply'
    still applies when an stdin redirect is present). Output-only and
    non-stdin-fd redirects never change stdin. The last stdin-affecting
    redirect in the segment wins (`< a <&-` closes, `<&- < a` supplies).
    """
    i = 0
    # Same-shell prefixes that still run an `exec` in this shell (verified
    # on Bash): `!`, `time -p`, `builtin exec`, `command [-p|--] exec` all
    # persistently redirect the current shell. `command -v/-V` merely
    # describes and executes nothing. Anything else (wrappers, options)
    # runs elsewhere or is unparseable, so it is not a persistent redirect.
    for _ in range(4):
        while i < len(words) and (words[i] in KEYWORDS or ASSIGN.match(words[i])):
            i += 1
        if i < len(words) and words[i] in ("!", "builtin"):
            i += 1
            continue
        if i < len(words) and words[i] == "time":
            i += 1
            if i < len(words) and words[i] == "-p":
                i += 1
            continue
        if i < len(words) and words[i] == "command":
            i += 1
            if i < len(words) and words[i] in ("-p", "--"):
                i += 1
            elif i < len(words) and words[i] in ("-v", "-V"):
                return None
            continue
        break
    if i >= len(words) or words[i] != "exec":
        return None
    # Skip redirect operators and their targets: they select no program.
    clean, skip = [], False
    for w in words[i + 1:]:
        if skip:
            skip = False
            continue
        if w.startswith(REDIR_MARK):
            skip = True
            continue
        clean.append(w)
    j = 0
    while j < len(clean):
        a = clean[j]
        if a == "--":
            j += 1
            break
        if len(a) >= 2 and a[0] == "-" and a[1] != "-":
            k, valid, need_next = 1, True, False
            while k < len(a):
                ch = a[k]
                if ch in "cl":
                    k += 1
                elif ch == "a":
                    if k + 1 == len(a):
                        need_next = True
                    k = len(a)
                else:
                    valid = False
                    break
            if not valid:
                break  # unknown option: treat the rest as a command
            if need_next:
                if j + 1 >= len(clean):
                    break  # missing -a value: unparseable, treat as command
                j += 2
            else:
                j += 1
            continue
        break
    has_command = j < len(clean)
    last = None
    for op, target in _scan_stdin_ops(seg):
        e = _stdin_op_effect(op, target)
        if e is not None:
            last = e
    if last == "supply":
        return "supply"
    if last == "close" and not has_command:
        return "close"
    return None


def stdin_program_refusal(words, piped, seg):
    """Deny when an interpreter's program comes from stdin and is not visible in argv (Issue #637).

    Refused: `python3 -` (explicit stdin program, piped or not; see python_rule), a piped stdin with no
    program word (`... | python3`, `... | python3 -W ignore`), an explicit `-` stdin program for any
    interpreter (`node -`, `perl -`; piped or not), and a bare or flags-only perl/node/ruby/php/lua/awk
    with piped or `<`-redirected stdin (`... | node --no-warnings`, `node --no-warnings < code.js`;
    these read their program from stdin when no -e/file names one). Each interpreter has its own
    option grammar (_node_program, _perl_program, ...): an option and a script-file operand are told
    apart, an unknown option form is fail-closed (never read as a file), and syntax differences are
    not ignored with one blanket ALLOW. Returns a Deny, or None when no rule fires.

    Kept as they are (documented): a script file operand (`python3 x.py`, `node x.js`, including
    `node --no-warnings script.js` and `node -- script.js`) and `-c`/`-m`/`-e` with stdin as data
    (the code or module is named in argv and judged; file content is the X3d residual, out of #637
    scope), and unpiped bare or flags-only interpreters with no delivered code (no stdin taint).
    Pipes through `(...)`, `{...}`, control keywords (`if/while/until/for/case`), wrappers and
    nested `sh -c`/`bash -c` strings are marked (MAJOR-1/2): the interpreter shares the pipe-fed
    stdin across `;` inside pipe-fed compounds and into inner shell strings (fail-closed OR of outer
    and inner pipe marks). An independent command after the top-level pipeline (`... | cat; node`)
    stays unmarked. Too deep compound nesting is fail-closed DENY (depth limit).
    Known overreach (fail-closed, rare): a file operand literally named like an interpreter
    (`... | sort perl`, `... | tee node`) reads as a bare interpreter and is refused; the established
    X1 scan accepts the same trade-off for wrapper-hidden python (`nice python3`).
    """
    def _check_prog(name, prog, piped_here, seg_here):
        if prog in ('code', 'file'):
            return None
        if prog == 'stdin':
            return Deny("stdin-program", name + " with `-` runs its program from stdin: not inspected")
        if prog in ('none', 'unknown'):
            if piped_here or _has_input_redirect(seg_here):
                return Deny("stdin-program", name + " with piped or redirected stdin and no program word runs stdin: not inspected")
            return None
        return None

    i = 0
    while i < len(words) and (words[i] in KEYWORDS or ASSIGN.match(words[i])):
        i += 1
    if i < len(words) and _cmd_name(words[i]) in NON_EXEC_COMMANDS:
        return None
    for j in range(i, len(words)):
        name = _cmd_name(words[j])
        if PY_NAME.match(name):
            if piped and _python_program(words[j + 1:]) == 'none':
                return Deny("stdin-program", "python with piped stdin and no program word runs stdin: not inspected")
            return None
        if name in INTERP:
            prog = _interp_program(name, words[j + 1:])
            hit = _check_prog(name, prog, piped, seg)
            if hit is not None:
                return hit
            return None
    for j in range(i, len(words)):
        w = words[j]
        if ' ' not in w and '\t' not in w:
            continue
        if '\n' in w or '$' in w or '`' in w or SUBST in w:
            continue
        # A `<` inside a multi-word string is a quoted redirect of nested
        # code (`sh -c 'exec <&-; node'`): the nested analysis judges the
        # inner stdin precisely (including explicit closes), while this
        # text scan cannot tell supply from close. Skip it here instead of
        # guessing (fail-closed: the nested pass still denies when the
        # inner interpreter really reads stdin as its program).
        if '<' in w:
            continue
        parts = w.split()
        if not parts:
            continue
        for k, part in enumerate(parts):
            pname = _cmd_name(part)
            if PY_NAME.match(pname):
                if piped and _python_program(parts[k + 1:]) == 'none':
                    return Deny("stdin-program", "python with piped stdin and no program word runs stdin: not inspected")
                break
            if pname in INTERP:
                prog = _interp_program(pname, parts[k + 1:])
                if prog in ('code', 'file'):
                    break
                if prog == 'stdin':
                    return Deny("stdin-program", pname + " with `-` runs its program from stdin: not inspected")
                if prog in ('none', 'unknown') and piped:
                    return Deny("stdin-program", pname + " with piped or redirected stdin and no program word runs stdin: not inspected")
                break
    return None


def python_rule(args):
    # A shell variable or substitution in a python argument: its value is not visible here.
    for arg in args:
        if "$" in arg or "`" in arg or SUBST in arg:
            raise Deny("unexpanded", "python argument with an unexpanded variable or substitution is not inspected")
    # A standalone `-` in program position names stdin as the program (Issue #637): the code is not in
    # argv, so there is nothing to judge. `python3 -` and `python3 - args` are refused; `x.py -` (a script
    # with `-` in its own argv) and `-m mod -` select their program from argv and stay as they are.
    if _python_program(args) == 'stdin':
        raise Deny("stdin-program", "python with `-` runs its program from stdin: not inspected")
    for code in python_c_codes(args):
        # Python NFKC-normalises identifiers, so ｏｐｅｎ(...) is open(...). Match the raw text (as before) and the
        # normalised text. Normalising alone would let a word with a combining mark (remove + U+0301) through.
        if DANGER_PY.search(code) or DANGER_PY.search(unicodedata.normalize("NFKC", code)):
            raise Deny("python-c", "python -c with a file or process operation")


def shell_rule(args, ctx, depth, fed=False):
    # MAJOR-2: outer pipe-fed stdin is inherited by the inner "sh -c" string.
    # The inner text is analysed with inherited_fed so that "node;" / "(node)"
    # with no inner pipe still sees the outer pipe (fail-closed). A fresh
    # child shell shares no function definitions, so outer_funcs is not
    # passed (an unexported function is not visible inside sh -c).
    for i, a in enumerate(args):
        if short_has(a, "c"):
            if i + 1 >= len(args):
                raise Deny("shell", "shell -c without code")
            _nested_text(args[i + 1], ctx, False, depth + 1, fed=fed)
            return
    raise Deny("shell", "shell without -c is not inspected")


def pwsh_rule(args, ctx, depth, fed=False):
    low = [a.lower() for a in args]
    for a in low:
        if a.startswith("-enc") or a in ("-e", "-ec"):
            raise Deny("powershell", "encoded command is not inspected")
        if len(a) >= 2 and "-file".startswith(a):
            raise Deny("powershell", "-File script is not inspected")
    for i, a in enumerate(low):
        if a == "-c" or (len(a) > 2 and "-command".startswith(a)):
            _nested_text(" ".join(args[i + 1:]), ctx, True, depth + 1, fed=fed)
            return


def check_assign(word):
    # GIT_CONFIG_* variables can define an alias just like "git -c alias.x=..."
    if "alias." in word.casefold():
        raise Deny("git-alias", "git alias set through an environment variable is denied")


def names_claude(value):
    """True if a path (or a word that may expand to one) names .claude/ literally, or through a glob: a path component that
    fnmatch maps onto .claude (.cl?ude, .cl[a]ude, *) counts. Fail closed."""
    v = _unescape(value).replace("\\", "/").casefold()
    if ".claude" in v:
        return True
    return any(part and fnmatch.fnmatchcase(".claude", part.rstrip(". ")) for part in v.split("/"))


def protected_part(parts, role):
    """True if a path component names .git (any role) or .claude (any role but member globs), in a spelling Windows resolves
    to it: trailing dots and spaces are dropped (".git." and ".git " are .git). An 8.3 short name (CLAUDE~1, GIT~1) cannot be
    mapped here, so it is refused in a write role (fail closed)."""
    for p in parts:
        n = p.rstrip(". ").casefold()
        if n == ".git" or (role != "member" and n == ".claude"):
            return True
        if role != "member" and re.fullmatch(r"[^~]{1,8}~\d+(\.[^~]*)?", p):
            return True
    return False


def claude_mention(args):
    """True if an argument names .claude/ (literally or through a glob, raw or unescaped) or carries a substitution."""
    return any(names_claude(a) or SUBST in a for a in args)


# GNU timeout long options. getopt_long accepts any unambiguous prefix, so --kill, --sig and --s are valid.
TIMEOUT_LONG = ("foreground", "kill-after", "preserve-status", "signal", "verbose", "help", "version")
TIMEOUT_LONG_VALUE = {"kill-after", "signal"}


def timeout_candidates(args):
    """The command timeout runs: timeout [OPTION]... DURATION COMMAND [ARG]... GNU timeout stops option parsing at
    the first non-option (the "+" getopt form), so options after DURATION belong to COMMAND. Options before it:
    -k and -s (and --kill-after, --signal) take a value, separate or attached; other options are flags. Anything
    the grammar does not cover is analysed conservatively: a non-numeric duration and an option-like command name
    are also candidates, and so is every suffix of an option-like candidate."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not (a.startswith("-") and len(a) > 1):
            break
        if a.startswith("--"):
            # Long option: an exact name or an unambiguous prefix; only --kill-after and --signal take a value.
            hits = [o for o in TIMEOUT_LONG if o.startswith(a[2:])] if "=" not in a else []
            i += 2 if len(hits) == 1 and hits[0] in TIMEOUT_LONG_VALUE else 1
        else:
            # Short cluster: -k or -s takes the rest of the word as its value, or the next word if it is last.
            takes_next = False
            for j in range(1, len(a)):
                if a[j] in "ks":
                    takes_next = j == len(a) - 1
                    break
            i += 2 if takes_next else 1
    if i >= len(args):
        return []
    cands = [args[i + 1:]]
    if not re.match(r"^[0-9.]+[smhd]?$", args[i]):
        # A non-numeric duration is an error in GNU timeout; the word is kept as a possible command (fail closed).
        cands.append(args[i:])
    out = []
    for c in cands:
        out.append(c)
        if c and c[0].startswith("-"):
            # An option-like word may be read as an option; its suffixes are kept too (fail closed).
            out += [c[k:] for k in range(1, len(c))]
    return out


# Option grammars of the wrappers whose command word can be found exactly: (short options taking a value, short flags,
# long options taking a value, long flags). An option outside the grammar is DENY (fail closed). Other wrappers keep the
# suffix analysis of wrapper_candidates.
WRAPPER_OPTS = {
    "sudo": ({"u", "g", "C", "h", "p", "r", "t", "T", "U"}, {"E", "H", "i", "s", "n", "b", "k", "K", "S", "A", "l", "v", "e"},
             {"user", "group", "close-from", "host", "prompt", "role", "type", "command-timeout", "other-user"},
             {"preserve-env", "login", "shell", "non-interactive", "stdin", "askpass", "list", "validate",
              "reset-timestamp", "edit", "background", "set-home", "help", "version"}),
    "env": ({"u", "C"}, {"i", "0", "v"}, {"unset", "chdir"}, {"ignore-environment", "null", "debug"}),
    "nice": ({"n"}, set(), {"adjustment"}, set()),
    "timeout": ({"s", "k"}, {"v"}, {"signal", "kill-after"}, {"foreground", "preserve-status", "verbose"}),
    "nohup": (set(), set(), set(), set()),
    "time": ({"f", "o"}, {"p", "v", "a", "q"}, set(), set()),
    "flock": ({"w", "E", "c"}, {"s", "x", "u", "n", "o"}, {"timeout", "conflict-exit-code", "command"},
              {"shared", "exclusive", "unlock", "nonblock", "verbose", "close"}),
}


def wrapper_command_index(name, args, ctx):
    """Index in args of the word the wrapper starts: after its options, their values, env's NAME=VALUE pairs, and timeout's
    duration. An option outside the grammar is DENY. A wrapper's own file-writing operand (time -o, flock's lock
    file) is judged here, where the operand is identified."""
    valued_s, flags_s, valued_l, flags_l = WRAPPER_OPTS[name]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        if a.startswith("--"):
            key, eq, _val = a[2:].partition("=")
            if key in valued_l:
                i += 1 if eq else 2
            elif key in flags_l and not eq:
                i += 1
            else:
                raise Deny("wrapper", name + " option is not in the wrapper grammar: " + a)
            continue
        j, step = 1, 1
        while j < len(a):
            ch = a[j]
            if name == "time" and ch == "o":
                # GNU time -o writes the timing report to the named file (truncating it, appending with -a):
                # a wrapper's own write option, denied in every form like the program tools' write options.
                raise Deny("write", "time -o writes a file: DENY in every form")
            if ch in valued_s:
                step = 1 if j < len(a) - 1 else 2  # attached value, or the value is the next word
                break
            if ch not in flags_s:
                raise Deny("wrapper", name + " option letter is not in the wrapper grammar: " + a)
            j += 1
        i += step
    if name == "env":
        while i < len(args) and ASSIGN.match(args[i]):
            i += 1  # NAME=VALUE pairs before the command
    if name == "timeout":
        i += 1  # timeout DURATION COMMAND: the first operand is the duration
    if name == "flock":
        # flock [OPTION]... FILE COMMAND: the first operand is the lock file (-c gives the command instead).
        # flock creates the lock file when it is missing, as touch does. A lock file outside the repository
        # is allowed (Round 6/7), but creating one inside .claude/ or .git is a write into a protected
        # directory. A numeric operand is an open file descriptor and creates nothing.
        if i < len(args) and not args[i].isdigit():
            # Every literal prefix is resolved, as in tar_path_ok: a name that does not exist yet is
            # resolved lexically, so a link in the middle (link/newlock with link -> .claude) would
            # otherwise be missed.
            operand = args[i].replace("\\", "/")
            parts = [p for p in operand.split("/") if p not in ("", ".")]
            hit = names_claude(args[i])
            for j in range(1, len(parts) + 1):
                pre = "/".join(parts[:j])
                if operand.startswith("/"):
                    pre = "/" + pre
                _lex, real = resolve(pre, ctx)
                rk = real.casefold()
                if inside_claude(real, ctx) or rk == ctx.git or inside(rk, ctx.git):
                    hit = True
                    break
            if hit:
                raise Deny("write", "flock lock file is in .claude/ or .git: " + args[i])
        i += 1
    return i


def wrapper_write_context(ctx):
    """Resolve an existing cwd before checking implicit wrapper writes.

    The hook's cwd can itself be a symlink to .claude/ or .git/. resolve() deliberately keeps an absent
    final path lexical, so resolving the nonexistent lock file alone misses that symlink. Keep the
    original context untouched and use the real cwd only for wrappers that implicitly write there.
    An absent/synthetic cwd is unchanged; in a real hook invocation the process cwd already exists.
    """
    if not os.path.isdir(ctx.cwd):
        return ctx
    return ctx.copy(cwd=canon(os.path.realpath(ctx.cwd)))


def wrapper_candidates(name, args, ctx):
    """A wrapper's own options are not parsed here: their arity differs per tool and option (--unset X, nice --adjustment
    5, stdbuf -o L), so a misread option would hide the command it starts. Every suffix of the words is therefore analysed
    as a possible command; the real one is among them. env -S (--split-string) takes one string that is split into
    several words, so it is denied."""
    if name in ("nohup", "flock"):
        ctx = wrapper_write_context(ctx)
    if name == "env":
        # The whole remaining argv is scanned, not only env's own option region (Owner, X1): a -S after
        # the program word is denied too (fail-closed over-denial, pinned by the X1 tests).
        for tok in args:
            if (tok.startswith("--") and tok != "--" and "split-string".startswith(tok[2:].split("=", 1)[0])) or (tok.startswith("-") and not tok.startswith("--") and "S" in tok[1:]):
                raise Deny("wrapper-argv", "env -S (split-string) is not inspected")
    if name == "nohup" and inside_claude(ctx.cwd, ctx):
        # nohup appends the command's output to nohup.out in the current directory: inside .claude/ that
        # writes into the guard's own directory.
        raise Deny("claude-write", "nohup run inside .claude/ writes nohup.out there")
    if name in WRAPPER_OPTS:
        start = wrapper_command_index(name, args, ctx)
        return [args[start:]] if start < len(args) else []
    return [args[k:] for k in range(len(args) + 1)]


def command_name(w):
    """The program name a command word starts: the basename, lower-cased, without a leading = (zsh), trailing dots and
    spaces (Windows drops them from file names), and .exe. The PATH check and the dispatch use this same normalization."""
    name = posixpath.basename(w.replace("\\", "/")).lower()
    name = name.lstrip("=").rstrip(". ") or name
    if name.endswith(".exe"):
        name = name[:-4].rstrip(". ") or name[:-4]
    return name


def analyze_argv(words, ctx, ps, depth, fed=False, outer_funcs=None,
                  via_builtin=False):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    # A program-naming environment variable anywhere in the command (prefix, export, env -S, a shell string) is denied.
    # In a python layer the words after the command word are python's argv, not shell assignments (its -c
    # code is judged by python_rule): only the prefix words before the command can be shell assignments.
    cmd_i = 0
    while cmd_i < len(words) and (words[cmd_i] in KEYWORDS or ASSIGN.match(words[cmd_i])):
        cmd_i += 1
    if cmd_i < len(words) and re.fullmatch(r"python[0-9.]*|py", _cmd_name(words[cmd_i])):
        expansion_words = words[:cmd_i]
    else:
        expansion_words = words
    if any("=" in w and ("{" in w or "}" in w or "$" in w) for w in expansion_words):
        raise Deny("expansion", "an assignment with an expansion is not inspected")
    # A variable set by a builtin from an expansion ($A$B) has its name known only at run time: denied (fail closed).
    # A backslash-escaped builtin (\export, \printf) is the same builtin: compare the unescaped word.
    plain = [w.replace("\\", "") for w in words]
    setter = any(w in SETTER_BUILTINS for w in plain) or ("printf" in plain and any(w.startswith("-v") for w in plain))
    if setter and any("$" in w or "`" in w or SUBST in w for w in words):
        raise Deny("expansion", "a variable set by a builtin with an expansion is not inspected")
    # Any mention of a program-naming variable (assignment, +=, export, read, printf -v) is denied (fail closed).
    # Windows environment names are case-insensitive, so the comparison is made in uppercase.
    if any(v in w.replace("\\", "").replace('"', "").replace("'", "").upper() for w in words for v in PROGRAM_ENV):
        raise Deny("program-option", "a program-naming environment variable is not inspected")
    # A PATH assignment changes which helper a program starts (rsync's remote form starts ssh through PATH). With a
    # program tool in the same command it is denied; the tool name is compared by its basename, without .exe.
    if any(re.match(r"PATH\+?=", w.replace("\\", "").replace('"', "").replace("'", "").upper()) for w in words) and any(
            PROGRAM_ALIAS.get(command_name(w), command_name(w)) in (PROGRAM_TOOLS | DEV_TOOLS)
            for w in words):
        raise Deny("program-option", "PATH is set in a command that starts a program tool: not inspected")
    words = strip_redirections(words)
    while True:
        # PowerShell has no environment prefix: "a=Remove-Item" (a hashtable entry) is name=value, and its value runs.
        while words and (words[0] in KEYWORDS or (ASSIGN.match(words[0]) and not ps)):
            check_assign(words[0])
            words = words[1:]
        if not words:
            return
        w0 = words[0]
        if ps and len(words) > 1 and words[1].startswith("="):
            # PowerShell "name =value": the value after "=" is the command position.
            value = words[1][1:]
            words = ([value] if value else []) + words[2:]
            continue
        if ps and "=" in w0 and not w0.startswith("="):
            # PowerShell name=value with no spaces, for any key ('my key'=, my-key=, a.b=): the value after the first "=" is
            # the command position.
            value = w0.split("=", 1)[1]
            words = ([value] if value else []) + words[1:]
            continue
        if w0.lower() == "function":
            # X3a: function NAME { body }. The body's commands are in command position, so the words after the
            # name are analysed as ordinary words. A name fused with a brace or parenthesis (f{rm, f(), or no name
            # at all) cannot be split reliably: fail closed.
            if len(words) < 2 or any(ch in words[1] for ch in "{}()"):
                raise Deny("function", "function keyword without a plain name is not resolvable")
            words = words[2:]
            continue
        if w0.lower() == "coproc":
            # X3a: coproc CMD ... or coproc NAME CMD ... Both readings are analysed, so the command is checked either way.
            rest = words[1:]
            if not rest:
                raise Deny("coproc", "coproc without a command is not resolvable")
            _nested_argv(rest, ctx, ps, depth + 1, fed, outer_funcs)
            if len(rest) >= 2 and rest[0] not in COPROC_COMPOUND:
                # Simple-command form has no explicit name: the suffix is
                # validated, but taint from the impossible named parse is
                # not exported (Piece D, #636). A recognised unnamed
                # compound was already covered once by the primary parse.
                # Issue #637: the alternative reading shares this stdin.
                analyze_argv(rest[1:], ctx.copy(), ps, depth + 1, fed, outer_funcs)
            return
        if SUBST in w0:
            raise Deny("command-word", "command substitution in command position")
        if ps and len(words) == 1 and w0.startswith("$") and not any(ch in w0 for ch in "*?[]{}`~"):
            # A lone PowerShell variable is an expression, not a command ($_.Name in a scriptblock body). A variable
            # with arguments ($RM -rf dir) stays a command position and is refused below.
            words = words[1:]
            continue
        if w0 not in ("[", "[[") and any(ch in w0 for ch in "$*?[]{}`~"):
            raise Deny("command-word", "unexpanded word in command position")
        # A backslash before a plain character only quotes it: \rm is rm.
        w0 = re.sub(r"^\\+(?=[^\\/])", "", w0)
        # A backslash word that is not a drive path is read as POSIX: \/bin/s\h is /bin/sh. A backslash word with
        # no "/" after unescaping (tools\rm, .\rm.exe, \\server\share) is refused: its name is not known.
        if "\\" in w0 and not re.match(r"^[A-Za-z]:[\\/]", w0):
            unescaped = re.sub(r"\\(.)", r"\1", w0, flags=re.S)
            if "/" not in unescaped:
                raise Deny("command-word", "backslash in command name")
            w0 = unescaped
        # Windows drops trailing dots and spaces from a file name (rm. and rm  are rm); a leading = is zsh's expansion of
        # a command name to its path (=sort). command_name() is the one normalization, shared with the PATH check.
        name = command_name(w0)
        path_word = "/" in w0 or "\\" in w0
        if name == "timeout":
            # timeout's option grammar (long options with a separate value, non-integer durations) is not parsed, so
            # the command it runs is taken from its grammar (timeout_candidates), with conservative extra candidates.
            for cand in timeout_candidates(words[1:]):
                _nested_argv(cand, ctx, ps, depth + 1, fed, outer_funcs)
            return
        if handoff(name):
            # String visits below are alternative parses of this same
            # invocation: they start from the taint inherited here, before
            # the suffix pass merges its own findings (Piece D, #636).
            wrapper_tainted = ctx.tainted
            same_shell_consumed = None
            if name in ("command", "builtin", "time"):
                # Piece D (#636, CRITICAL-2): command/builtin/time run a
                # shell builtin in the calling shell, so its cwd change
                # moves the parent too. Anything else they reach runs in
                # a child process (or is refused), keeping taint-only.
                # The existing suffix and string passes below still run,
                # so no current validation is dropped.
                idx = same_shell_target_index(name, words[1:], ctx)
                if idx is not None and command_name(words[1:][idx]) in SAME_SHELL_NAMES:
                    same_shell_consumed = words[1:][idx:]
                    # Only `command`/`builtin` bypass a shadowing function;
                    # `time` runs functions in the current shell, so the
                    # builtin form is not assumed there.
                    _nested_argv_same_shell(same_shell_consumed, ctx, ps, depth + 1,
                                            fed, outer_funcs,
                                            via_builtin=(name in ("command", "builtin")))
            for cand in wrapper_candidates(name, words[1:], ctx):
                if same_shell_consumed is not None and cand == same_shell_consumed:
                    # Already validated above (its taint/cwd merged once):
                    # re-validating the exact target against its own taint
                    # self-denies a clean same-shell invocation.
                    continue
                # An option-looking word (-n, --command=...) or a number (timeout 5) cannot be the command a wrapper starts;
                # the command is a later suffix. Strings are covered by the text analysis below.
                # For a wrapper with a grammar the command word is exact, so it is analysed even when it looks like an option.
                if name not in WRAPPER_OPTS and cand and (cand[0].startswith("-") or cand[0].isdigit()):
                    continue
                _nested_argv(cand, ctx, ps, depth + 1, fed, outer_funcs)
            if name in STRING_LAUNCHERS:
                # A shell string argument is text, whatever its shape: a quoted string with a space (sudo -s "rm -rf x"),
                # the value after -c or --command (flock -c rm lock), and the value after --name= (flock --command="rm").
                # Plain words and options are commands for the suffix pass, which the grammar narrows to the real command.
                # String visits are alternative parses of one invocation: each
                # starts from the wrapper-entry taint and merges back (Piece D, #636).
                # A plain-word visit exports taint only for the command word
                # itself: other words are the command's data, validated but
                # not exported. Explicit code carriers (-c/--command/--x=y)
                # always export.
                cmd_word = None
                if name in WRAPPER_OPTS:
                    cmd_word = wrapper_command_index(name, words[1:], ctx) + 1
                for k in range(1, len(words)):
                    if not words[k].startswith("-") and any(ch.isspace() for ch in words[k]):
                        if cmd_word is None or k == cmd_word:
                            _nested_text_from(words[k], ctx, wrapper_tainted, ps, depth + 1,
                                              fed=fed)
                        else:
                            sub = ctx.copy()
                            sub.tainted = wrapper_tainted
                            analyze_text(words[k], sub, ps, depth + 1, inherited_fed=fed)
                    if words[k] in ("-c", "--command") and k + 1 < len(words):
                        _nested_text_from(words[k + 1], ctx, wrapper_tainted, ps, depth + 1,
                                          fed=fed)
                    if words[k].startswith("--") and "=" in words[k]:
                        _nested_text_from(words[k].split("=", 1)[1], ctx, wrapper_tainted, ps,
                                          depth + 1, fed=fed)
            return
        break
    args = words[1:]
    program_option_rule(name, args, ctx)
    # MAJOR-B (review 5968211099): enforce stdin-program on the resolved
    # interpreter argv (wrapper-resolved via handoff recursion carries fed),
    # not the text-scan first token (which may be a wrapper operand).
    # Named code/file stays ALLOW (stdin as data); bare/unknown with fed
    # (pipe-fed OR input-redirected seg_taint) is DENY with stdin-program.
    if PY_NAME.match(name):
        _prog = _python_program(args)
        if _prog == 'stdin':
            raise Deny("stdin-program", "python with `-` runs its program from stdin: not inspected")
        if _prog in ('none', 'unknown') and fed:
            raise Deny("stdin-program", "python with piped or redirected stdin and no program word runs stdin: not inspected")
    elif name in INTERP:
        _prog = _interp_program(name, args)
        if _prog == 'stdin':
            raise Deny("stdin-program", name + " with `-` runs its program from stdin: not inspected")
        if _prog in ('none', 'unknown') and fed:
            raise Deny("stdin-program", name + " with piped or redirected stdin and no program word runs stdin: not inspected")
    if ps and name in PS_LAUNCH_NAMES:
        # A PowerShell process launcher runs its arguments as a command line, and its options (-FilePath, -ArgumentList)
        # may come first, so every suffix of the arguments is analysed as a command line. Bounded: a longer argument
        # list is denied.
        # Re-split on whitespace: a quoted argument ('-c Remove-Item ...') is one word but several command words.
        tokens = " ".join(args).split()
        if len(tokens) > LAUNCHER_MAX_WORDS:
            raise Deny("launcher", "launcher with too many arguments is not inspected")
        # No candidate is skipped: a path split at its space ("Files\app.exe") is refused like any other command word,
        # so a destructive command after a separator in the same token ("tools\app.exe;rm -rf /") is still analysed.
        entry_tainted = ctx.tainted
        for k in range(len(tokens)):
            _nested_text_from(" ".join(tokens[k:]), ctx, entry_tainted, True, depth + 1,
                              fed=fed)
    if name in (".", "source") and any(("$" in a or "`" in a or SUBST in a) for a in args):
        # Sourcing a path that names a variable (or a substitution) is a file that is not known here (fail closed).
        raise Deny("unexpanded", "sourcing a variable or substitution is not inspected")
    if name in EVAL_LIKE and any(SUBST in a for a in args):
        raise Deny("substitution", "command substitution passed to a shell, eval or source is not inspected")
    if name in WRITE_NAMES or (name == "sed" and any(
            short_has(a, "i") or a.startswith("--in-place") for a in args)):
        # Target not statically known (variable or backtick): fail closed.
        if any("$" in a or "`" in a for a in args):
            raise Deny("unexpanded", "write with an unexpanded variable or backtick is not inspected")
        if claude_mention(args) or inside_claude(ctx.cwd, ctx):
            raise Deny("claude-write", "write to .claude/ (guard, registry, settings) is denied")
    elif PY_NAME.match(name) or name in INTERP:
        if claude_mention(args):
            raise Deny("claude-write", "interpreter with .claude/ target is denied")
    if name in ("iex", "invoke-expression"):
        raise Deny("powershell", "Invoke-Expression is not inspected")
    if name == "eval":
        # Piece D (#636, CRITICAL-1): eval runs in the calling shell, so
        # a cd inside it moves the parent cwd. The nested text is analysed
        # in a copy and both taint and cwd merge back; a subshell inside
        # the eval text is still scoped by analyze_text. Issue #637: eval
        # runs in the calling shell, so this segment's stdin and visible
        # function definitions are passed through, and a persistent stdin
        # redirect inside the eval text (e.g. `exec < file`) moves the
        # parent's stdin too when this eval runs the builtin (merge_exec;
        # verified on Bash). A `command`/`builtin` prefix bypasses a
        # shadowing `eval` function and always runs the builtin, while a
        # bare `eval` with a shadowing function calls that function instead
        # (its string is an argument, not code). A redirect on the eval
        # command itself does not persist (verified: restored after the
        # command), so it sets no persistent flag.
        _nested_text(" ".join(args), ctx, ps, depth + 1, fed=fed,
                     outer_funcs=outer_funcs, merge_cwd=True,
                     merge_exec=(via_builtin or _func_call_name("eval", ps) not in (outer_funcs or {})))
    elif name in SHELLS:
        shell_rule(args, ctx, depth, fed)
    elif name in PS_HOSTS:
        pwsh_rule(args, ctx, depth, fed)
    elif name in CD_NAMES:
        if args and args[0].startswith("-"):
            raise Deny("cd", "cd with option is not inspected")
        if args and any(c in args[0] for c in "*?[") and names_claude(args[0]):
            raise Deny("cd", "cd into a glob that may name .claude/ is not inspected")
        if args and ("$" in args[0] or "`" in args[0] or SUBST in args[0]):
            # Piece D (#636): the target is known only after the shell
            # expands it. Guessing a cwd would misjudge later relative
            # paths, so it is refused instead of resolved lexically.
            raise Deny("unexpanded", "cd target with a variable or substitution is not inspected")
        ctx.cwd, ctx.cwd_fuzzy = _cd_target(args[0] if args else "~", ctx)
    elif name in ALWAYS_DENY:
        raise Deny("destructive", name + " is denied")
    elif name == "rm" or name in PS_RM:
        rm_rule(args, ps or name in PS_RM, ctx)
    elif name == "git":
        git_rule(args, ctx)
    elif name == "find":
        find_rule(args, ps, ctx, depth, fed)
    elif PY_NAME.match(name):
        python_rule(args)
    elif path_word:
        raise Deny("unknown", "a program named by a path is not inspected: " + w0)
    elif name in WRITE_DENY:
        raise Deny("write", name + " creates a link or copies to another location: DENY (Owner Round 13)")
    elif name in SAFE_WRITE:
        safe_write_rule(name, args, ctx)
    elif name in SAFE_EXTERNAL:
        pass  # read-only: its options cannot write
    elif name == "gzip":
        gzip_rule(args, ctx)
    elif name in DEV_TOOLS:
        dev_tool_rule(name, args, ctx)
    elif PROGRAM_ALIAS.get(name, name) in PROGRAM_TOOLS or not args:
        pass  # a program tool (its own grammar ran above), or no arguments
    else:
        # Owner rule: an external command that is not provably safe is DENY when it takes arguments (fail closed). Listing
        # a name is not enough when its arguments can run code: make, npm, pip, uv, ltrace, numactl, parallel are all here.
        raise Deny("unknown", "external command with arguments that is not in the allowlist: " + name)


MAX_REDIRECTS = 64  # each redirection is a path check; an unbounded number of them is refused (fail closed, time bound)


def _stdin_prepass(segs, ps, inherited_fed, all_funcs=None):
    """Stdin provenance per segment over the sentinel stream (Issue #637).

    split_segments runs fully before analysis, so this pre-pass can compute
    stdin taint for every segment up front, including retroactive group
    taint (`(node) < file`, `fi < file`) that a single forward pass cannot
    see when it analyses the inner segments. Returns (pipe, eff, is_stage,
    subdepth, end_exec, suppressed, in_cond), each parallel to segs
    (sentinels read False/0):
    - pipe[i]: the segment reads a tainted stdin. This joins three
      provenance layers tracked separately (Issue #637 MAJOR follow-up):
      `inh` (inherited from the enclosing invocation: the caller's pipe,
      redirect, or exec-persistent stdin; never cleared by this text's own
      command boundaries), `fed` (this text's own local pipeline stage),
      and the persistent `exec` flag. Its own `<` redirect is NOT included
      (expansion precedes redirection, so a substitution in the segment
      still sees the pre-redirect stdin).
    - eff[i]: pipe[i] or its own input redirect (the taint an interpreter
      in this segment, a function called here, or a nested shell string
      actually observes).
    - is_stage[i]: the pipe taint comes from a real pipeline (a fork: a
      function body called here cannot leak an `exec` back to the caller).
    - subdepth[i]: subshell/coprocess nesting depth (same reason).
    - suppressed[i]: inside a function definition body (never executes there).
    - in_cond[i]: inside a conditional/loop compound (may not execute).
    - end_exec: the persistent exec-stdin flag after the last segment (for
      function-body queries; forked contexts never export it).

    Bash `exec` with an stdin redirect and no command changes the current
    shell's descriptors persistently (verified on Bash): a later bare
    interpreter reads its program from the redirected stdin. Output-only
    and non-stdin-fd `exec` redirects (`> log`, `3< f`) change nothing, a
    bare interpreter with a named file (`node script.js`) or a data reader
    (`cat`) is unaffected, and an explicit stdin close (`exec <&-`) clears
    the flag again (a closed stdin delivers EOF, no program). An `exec`
    inside a pipeline stage, background job, subshell or coprocess is
    local to that fork and never leaks out (over-approximation stays
    fail-closed: pipeline-left and background `exec` still taint). A function
    definition body never executes at definition time, so an `exec` inside
    one (`f() { exec < f; }`, `function f { exec < f; }`) is suppressed there
    and only takes effect through a call (analysed at the call site).
    """
    n = len(segs)
    pipe = [False] * n
    eff = [False] * n
    is_stage = [False] * n
    subdepth = [0] * n
    suppressed = [False] * n
    in_cond = [False] * n
    # inh: stdin inherited from the enclosing invocation (the caller's pipe,
    # redirect, or exec-persistent stdin). A `;`/newline/`&&`/`||`/`&`/`|`
    # boundary ends a command or a local pipeline stage, never the outer
    # fd-0 input stream, so inh is never cleared by boundaries (verified on
    # Bash: function bodies and `sh -c` strings keep reading inherited
    # stdin across `;`). Only an explicit stdin close at this same shell
    # level clears it (fail-closed: a close inside a conditional may never
    # execute, so conditional closes never clear).
    inh = bool(inherited_fed)
    fed = False
    pfed = False
    exec_flag = False
    exec_save = []
    inh_save = []  # inherited-stdin snapshots per open subshell/coprocess
    # (Issue #637 MAJOR: an inner `exec <&-` closes only the fork's fd 0;
    # the enclosing text keeps its inherited stream, restored at the
    # boundary, exactly like the persistent exec flag).
    stack = []  # (kind, entered_fed, entered_pfed, opener_idx)
    # kinds: if/loop/case, function (the `function` keyword), funcbody (an
    # `f() {...}` / `f() (...)` body), paren, coproc, bbrace, pscond, psbrace.
    subshell_depth = 0
    pending_funcbody = False  # an `f()`-style signature awaits its body group

    def _transparent(idx):
        return _seg_is_transparent(segs[idx], ps)

    for i, seg in enumerate(segs):
        if seg in _STDIN_SENTINELS:
            if seg not in (BBRACE_OPEN, SUBSHELL_OPEN):
                # Only a body group directly continues an `f()` signature;
                # any other boundary ends the pending definition.
                pending_funcbody = False
            if seg == PIPE_BOUND:
                fed = True
                pfed = True
            elif seg in (CMD_BOUND, BG_BOUND, PIPE_END):
                if not any(e[1] for e in stack):
                    fed = False
                    pfed = False
            elif seg in (SUBSHELL_OPEN, COPROC_OPEN):
                if len(stack) >= MAX_DEPTH:
                    raise Deny("depth", "command nesting too deep: not inspected")
                if pending_funcbody and seg == SUBSHELL_OPEN:
                    # An `f()` signature's `(...)` body: definitions do not
                    # execute, so their `exec` never leaks (calls are
                    # analysed at the call site instead).
                    stack.append(("funcbody", fed, pfed, i))
                    pending_funcbody = False
                else:
                    stack.append(("paren" if seg == SUBSHELL_OPEN else "coproc",
                                  fed, pfed, i))
                    pending_funcbody = False
                exec_save.append(exec_flag)
                inh_save.append(inh)
                subshell_depth += 1
            elif seg in (BBRACE_OPEN, PSCOND_OPEN, PSBRACE_OPEN):
                if len(stack) >= MAX_DEPTH:
                    raise Deny("depth", "command nesting too deep: not inspected")
                if pending_funcbody and seg == BBRACE_OPEN:
                    # An `f()` signature's `{...}` body: same suppression.
                    stack.append(("funcbody", fed, pfed, i))
                    pending_funcbody = False
                else:
                    stack.append(("bbrace" if seg == BBRACE_OPEN else (
                        "pscond" if seg == PSCOND_OPEN else "psbrace"), fed, pfed, i))
                    pending_funcbody = False
            elif seg in (SUBSHELL_CLOSE, COPROC_CLOSE):
                want = ("paren", "funcbody") if seg == SUBSHELL_CLOSE else ("coproc",)
                if stack and stack[-1][0] in want:
                    _, _efed, _epfed, opener_idx = stack.pop()
                    exec_flag = exec_save.pop()
                    inh = inh_save.pop()
                    if stack and stack[-1][0] == "function":
                        # A non-empty `(...)` group closed directly inside a
                        # `function` body is that body: the definition ends
                        # here (an empty `()` is only the signature, whose
                        # body still follows). A `case`-pattern `)` never
                        # matches (mismatch pops nothing).
                        _inner_empty = True
                        for _kk in range(opener_idx + 1, i):
                            if segs[_kk] not in _STDIN_SENTINELS and not _transparent(_kk):
                                _inner_empty = False
                                break
                        if not _inner_empty:
                            stack.pop()
                    if (seg == SUBSHELL_CLOSE and not ps) or seg == COPROC_CLOSE:
                        # A trailing input redirect applies to the whole
                        # group (`(node) < file`): taint the enclosed range
                        # fail-closed (verified on Bash). A redirect to
                        # another descriptor (`3< f`) or output-only (`>`)
                        # changes no stdin. The trailing segment itself
                        # keeps the restored (outer) stdin: a substitution
                        # in it is expanded by the parent (verified on Bash).
                        j = i + 1
                        while j < n and segs[j] not in _STDIN_SENTINELS and _transparent(j):
                            if _has_input_redirect(segs[j]):
                                for kk in range(opener_idx, i + 1):
                                    pipe[kk] = True
                                    eff[kk] = True
                                break
                            j += 1
                    subshell_depth -= 1
                    if not any(e[1] for e in stack):
                        fed = False
                        pfed = False
                    if not ps and seg == SUBSHELL_CLOSE and opener_idx is not None:
                        # An `f()`-style signature: empty parens directly after
                        # a single-word name, directly followed by a body
                        # group. The body never executes at definition time.
                        _empty = True
                        for _kk in range(opener_idx + 1, i):
                            if segs[_kk] not in _STDIN_SENTINELS and not _transparent(_kk):
                                _empty = False
                                break
                        if _empty:
                            _p = opener_idx - 1
                            while _p >= 0 and segs[_p] not in _STDIN_SENTINELS and _transparent(_p):
                                _p -= 1
                            if _p >= 0 and segs[_p] not in _STDIN_SENTINELS:
                                try:
                                    _pw = words_of(segs[_p], ps)
                                except Deny:
                                    _pw = []
                                if (len(_pw) == 1 and _FUNCDEF_NAME.fullmatch(_pw[0])
                                        and _pw[0] not in KEYWORDS
                                        and _pw[0] not in ("function", "time", "coproc")):
                                    _j2 = i + 1
                                    while _j2 < n and segs[_j2] not in _STDIN_SENTINELS and _transparent(_j2):
                                        _j2 += 1
                                    if _j2 < n and segs[_j2] in (BBRACE_OPEN, SUBSHELL_OPEN):
                                        pending_funcbody = True
                # Mismatch (e.g. a case pattern `)`): keep tainted, pop nothing.
            elif seg in (BBRACE_CLOSE, PSCOND_CLOSE, PSBRACE_CLOSE):
                want = (("bbrace", "funcbody") if seg == BBRACE_CLOSE else
                        (("pscond",) if seg == PSCOND_CLOSE else ("psbrace",)))
                if stack and stack[-1][0] in want:
                    _, _efed, _epfed, opener_idx = stack.pop()
                    if stack and stack[-1][0] == "function":
                        # The `{...}` body of a `function` definition ends
                        # here (a `{` brace group is always a real body).
                        stack.pop()
                    if seg == BBRACE_CLOSE and not ps:
                        j = i + 1
                        while j < n and segs[j] not in _STDIN_SENTINELS and _transparent(j):
                            if _has_input_redirect(segs[j]):
                                for kk in range(opener_idx, i + 1):
                                    pipe[kk] = True
                                    eff[kk] = True
                                break
                            j += 1
                    if not any(e[1] for e in stack):
                        fed = False
                        pfed = False
            continue
        try:
            words = words_of(seg, ps)
        except Deny:
            raise
        if strip_redirections(words) == []:
            # Empty or redirect-only: transparent for pipe/exec tracking
            # (a redirect-only segment runs no command). Its own redirect
            # still counts for its substitutions and for eff.
            pipe[i] = fed or inh
            own = _has_input_redirect(seg)
            eff[i] = pipe[i] or exec_flag or own
            is_stage[i] = pfed
            subdepth[i] = subshell_depth
            suppressed[i] = any(e[0] in ("function", "funcbody") for e in stack)
            in_cond[i] = any(e[0] in ("if", "loop", "case") for e in stack)
            continue
        pipe[i] = fed or inh
        exec_before = exec_flag
        own = _has_input_redirect(seg)
        eff[i] = pipe[i] or exec_before or own
        is_stage[i] = pfed
        subdepth[i] = subshell_depth
        suppressed[i] = any(e[0] in ("function", "funcbody") for e in stack)
        in_cond[i] = any(e[0] in ("if", "loop", "case") for e in stack)
        if not ps:
            k = 0
            while k < len(words) and words[k] in ("!", "time", "coproc"):
                k += 1
            first = words[k] if k < len(words) else ""
            opener = None
            if first == "if":
                opener = "if"
            elif first in ("while", "until", "for", "select"):
                opener = "loop"
            elif first == "case":
                opener = "case"
            elif first == "function":
                opener = "function"
            if opener is not None:
                if len(stack) >= MAX_DEPTH:
                    raise Deny("depth", "command nesting too deep: not inspected")
                stack.append((opener, fed, pfed, i))
            elif first in ("fi", "done", "esac"):
                want = {"fi": "if", "done": "loop", "esac": "case"}[first]
                if stack and stack[-1][0] == want:
                    _, _efed, _epfed, opener_idx = stack.pop()
                    if own:
                        # A closer carrying an input redirect (`fi < file`)
                        # redirects the whole compound (verified on Bash).
                        for kk in range(opener_idx, i + 1):
                            pipe[kk] = True
                            eff[kk] = True
            elif first == "}":
                if stack and stack[-1][0] == "function":
                    _, _efed, _epfed, opener_idx = stack.pop()
                    if own:
                        for kk in range(opener_idx, i + 1):
                            pipe[kk] = True
                            eff[kk] = True
            effect = _exec_segment_effect(words, seg)
            _suppressed = any(e[0] in ("function", "funcbody") for e in stack)
            _in_cond_here = any(e[0] in ("if", "loop", "case") for e in stack)
            if effect == "supply":
                # MAJOR (Issue #637): a persistent redirect applies to the
                # active shell scope, including a subshell/coprocess body
                # (`(exec < f; node)` reads f there, verified on Bash). The
                # matching close restores the saved outer state, so a
                # completed fork never leaks out (fail-closed).
                if (not pfed and not _suppressed
                        and not _followed_by_fork_boundary(segs, i, ps)):
                    exec_flag = True
            elif effect == "close":
                # An explicit stdin close at this same shell level clears
                # both the persistent flag and the inherited provenance
                # (a closed stdin delivers EOF, no program). A close inside
                # a conditional may never execute, so it never clears
                # (fail-open otherwise). Forked positions never leak: the
                # saved state is restored at the fork's closing boundary.
                if (not pfed and not _suppressed
                        and not _in_cond_here
                        and not _followed_by_fork_boundary(segs, i, ps)
                        and not _exec_shadowed_by_function(words, ps, all_funcs)):
                    exec_flag = False
                    inh = False
    return pipe, eff, is_stage, subdepth, exec_flag, suppressed, in_cond


def _eval_command_text(words, ps, all_funcs):
    """Eval string of a same-shell `eval` invocation, or None (Issue #637).

    Resolves `eval` through same-shell prefixes (`!`, `time -p`, `builtin`,
    `command [-p|--]`; `command -v/-V` merely describes) but never through
    forking wrappers (`coproc`, `sudo`, ...) or a shadowing shell function
    named `eval` (Bash prefers the function, like any other command).
    Output-only redirections on the eval command are skipped; the code
    words keep their lexer marks so the nested scan classifies them.
    """
    if ps:
        return None
    j = 0
    while j < len(words) and (words[j] in KEYWORDS or ASSIGN.match(words[j])):
        j += 1
    for _ in range(4):
        if j < len(words) and words[j] in ("!", "builtin"):
            j += 1
            continue
        if j < len(words) and words[j] == "time":
            j += 1
            if j < len(words) and words[j] == "-p":
                j += 1
            continue
        if j < len(words) and words[j] == "command":
            j += 1
            if j < len(words) and words[j] in ("-p", "--"):
                j += 1
            elif j < len(words) and words[j] in ("-v", "-V"):
                return None
            continue
        break
    if j >= len(words) or words[j] != "eval":
        return None
    if _func_call_name("eval", ps) in (all_funcs or {}):
        # A shell function shadows the builtin: the words are its
        # arguments, not code (handled by the function-call path).
        return None
    rest, skip, out = words[j + 1:], False, []
    for w in rest:
        if skip:
            skip = False
            continue
        if w.startswith(REDIR_MARK):
            skip = True
            continue
        out.append(w)
    return " ".join(out)


def _body_sets_exec_stdin(body, ps, funcs=None, _seen=None, strict=True):
    """True when a function body persistently redirects the caller's stdin.

    A function called in the current shell runs in it, so a bare
    `exec < file` in its body changes the caller's stdin (verified on
    Bash). Calls to other known functions are followed transitively (a
    call cycle never returns, so a repeated name stops the search: sound).
    The scan is syntactic (split + pre-pass only): conditionals inside the
    body are over-approximated fail-closed, while forked positions
    (subshells, coprocesses, pipeline stages, `coproc` calls) never leak.
    strict=True (function bodies): an unparseable body is fail-closed True.
    strict=False (eval-text pre-scan): unparseable text returns False
    because the precise nested analysis denies it anyway (fail-closed there).
    """
    if ps:
        return False
    try:
        segs = split_segments(body, ps)[0]
    except Deny:
        return strict
    try:
        _pipe, _eff, _stage, _subdepth, end_exec, _supp, _cond = _stdin_prepass(segs, ps, False, funcs)
    except Deny:
        return strict
    if end_exec:
        return True
    if not funcs:
        return False
    if _seen is None:
        _seen = set()
    for idx_b, seg_b in enumerate(segs):
        if seg_b in _STDIN_SENTINELS:
            continue
        try:
            words_b = words_of(seg_b, ps)
        except Deny:
            return strict
        if strip_redirections(words_b) == []:
            continue
        fkey_b, saw_coproc_b, _first_b = _call_target(words_b, ps)
        if _is_funcdef_name(segs, idx_b, ps):
            continue  # a nested definition installs without running
        if not saw_coproc_b and not _stage[idx_b] and _subdepth[idx_b] == 0 and not _supp[idx_b]:
            # A same-shell eval in the body persistently redirects it, too
            # (resolved before the function-call check: `eval` itself is
            # not a function name, so it would otherwise be skipped).
            _ev_b = _eval_command_text(words_b, ps, funcs)
            if _ev_b is not None and _body_sets_exec_stdin(_ev_b, ps, funcs, _seen, strict):
                return True
        if fkey_b is None or fkey_b not in funcs or fkey_b in _seen:
            continue
        if saw_coproc_b or _stage[idx_b] or _subdepth[idx_b] > 0 or _supp[idx_b]:
            continue  # forked call or nested definition: no leak back
        _seen.add(fkey_b)
        for _b in funcs[fkey_b]:
            if _b is None:
                return strict
            if _body_sets_exec_stdin(_b, ps, funcs, _seen, strict):
                return True
    return False



def _followed_by_fork_boundary(segs, idx, ps):
    """True when the command in segment idx runs in a forked child.

    A Bash `|`-pipeline stage or `&`-background job forks, so a persistent
    stdin change there never reaches the parent shell (verified on Bash:
    `exec < f | cat` and `exec < f &` leave the parent stdin alone).
    `&&`/`||` (PIPE_END) and `;`/newlines are sequential in the same shell.
    Only empty segments are skipped (a trailing redirect-only segment
    belongs to the preceding command, not to a following boundary).
    PowerShell forks no pipeline/background children this way, so only
    Bash `|`/`&` count (ps keeps its prior behavior exactly).
    """
    if ps:
        return False
    n = len(segs)
    j = idx + 1
    while j < n:
        if segs[j] in _STDIN_SENTINELS:
            return segs[j] in (PIPE_BOUND, BG_BOUND)
        try:
            _w = words_of(segs[j], ps)
        except Deny:
            return False
        if strip_redirections(_w) != []:
            return False
        j += 1
    return False

def _seg_is_transparent(seg, ps):
    """True when a segment is empty or redirect-only (runs no command)."""
    if seg in _STDIN_SENTINELS:
        return False
    return strip_redirections(words_of(seg, ps)) == []


def _is_funcdef_name(segs, idx, ps):
    """True when segment idx is a function-definition name (`f` in `f() {...}`).

    A definition installs the body without running it, so the name is not
    a call (no stdin-DENY analysis, no exec-out). Only the `f()`-style form
    needs this: `function f` puts `function` in command position, which
    never resolves to a definition. `(...)` with a non-empty body is not
    a signature (`f (node)` is not a definition).
    """
    n = len(segs)
    j = idx + 1
    while j < n:
        s = segs[j]
        if s in _STDIN_SENTINELS:
            if s == SUBSHELL_OPEN:
                break
            return False
        try:
            if not _seg_is_transparent(s, ps):
                return False
        except Deny:
            return False
        j += 1
    if j >= n or segs[j] != SUBSHELL_OPEN:
        return False
    k = j + 1
    while k < n:
        s = segs[k]
        if s in _STDIN_SENTINELS:
            return s == SUBSHELL_CLOSE
        try:
            if not _seg_is_transparent(s, ps):
                return False
        except Deny:
            return False
        k += 1
    return False


_STDIN_SENTINELS = frozenset([
    SUBSHELL_OPEN, SUBSHELL_CLOSE, PIPE_BOUND, BG_BOUND, CMD_BOUND,
    PIPE_END, COPROC_OPEN, COPROC_CLOSE, PSCOND_OPEN, PSCOND_CLOSE,
    BBRACE_OPEN, BBRACE_CLOSE, PSBRACE_OPEN, PSBRACE_CLOSE,
])

# A plausible `f()`-style function name for pending-definition detection:
# any single non-metacharacter word (the lexer already removed quotes),
# excluding keywords and reserved words. Over-matching only suppresses an
# `exec` inside what follows (fail-closed over-denial); under-matching
# (e.g. a `-`-leading name) only over-taints (fail-closed too).
_FUNCDEF_NAME = re.compile(r"[^\s;|&<>()=]+")


def analyze_text(text, ctx, ps, depth, inherited_fed=False, outer_funcs=None):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    if ps and METHOD_CALL_RE.search(text):
        raise Deny("powershell", "PowerShell method call is not inspected")
    if ps and PS_PROC_LAUNCH_RE.search(text) and EXPR_SYNTAX_RE.search(text):
        raise Deny("launcher", "PowerShell process launcher with expression syntax is not inspected")
    segs, subs, redirs = split_segments(text, ps)
    # MAJOR function: collect shell function definitions in this text and merge
    # with outer (caller) definitions for lexical sibling resolution. ANY unsafe
    # body makes tainted calls fail-closed DENY (redefinition safe over-denial).
    # Built before the stdin prepass: a function named `exec` shadows the
    # builtin (its redirects are temporary), so closes must see it.
    try:
        func_defs = _extract_function_defs(text, ps)
    except Deny:
        # Unparseable function syntax: fail-closed taint (never ALLOW guessed).
        # Mark as unknown body for a synthetic name that no call will match?
        # Simpler: treat whole text as tainted unknown -> rely on per-seg checks.
        func_defs = {}
    if outer_funcs:
        # Review 5968136032: copy each inherited body list (dict() alone shares
        # lists; a child `eval` define via extend would pollute the parent map
        # and deny a later sibling for a body Bash never installed there).
        all_funcs = {k: list(v) for k, v in outer_funcs.items()}
        for _k, _v in func_defs.items():
            all_funcs.setdefault(_k, []).extend(_v)
    else:
        all_funcs = func_defs
    # Issue #637: stdin provenance per segment (pipe-fed, exec-persistent,
    # group-redirect). Nested shells (sh -c, wrappers, eval, subs) inherit
    # the outer taint fail-closed (OR of outer and inner marks).
    _stdin_pipe, _stdin_eff, _stdin_stage, _stdin_subdepth, _prepass_end, _stdin_suppressed, _stdin_in_cond = _stdin_prepass(
        segs, ps, inherited_fed, all_funcs)
    # A python command whose argv is not certain (unquoted or double-quoted expansion characters) is refused.
    # dyn_at records the function-exec stdin flag before each segment, so the
    # scope machine below observes the same taint the checks here saw.
    dyn_exec = False
    dyn_at = [False] * len(segs)
    dyn_save = []  # dynamic stdin snapshots per open subshell/coprocess
    # (Issue #637 MAJOR: function/eval persistent effects apply inside the
    # active fork as well; the saved outer state is restored at the fork's
    # close so a completed fork never leaks back. A brace group runs in the
    # calling shell, so it shares the dyn flag with no save/restore.)
    for idx, seg in enumerate(segs):
        if seg == SUBSHELL_OPEN or seg == COPROC_OPEN:
            dyn_save.append(dyn_exec)
            continue
        if seg == SUBSHELL_CLOSE or seg == COPROC_CLOSE:
            if dyn_save:
                dyn_exec = dyn_save.pop()
            continue
        if seg == PIPE_BOUND or seg == BG_BOUND or seg == CMD_BOUND:
            continue
        if seg == PIPE_END:
            continue
        if seg == PSCOND_OPEN or seg == PSCOND_CLOSE:
            continue
        if seg == BBRACE_OPEN or seg == BBRACE_CLOSE:
            continue
        if seg == PSBRACE_OPEN or seg == PSBRACE_CLOSE:
            continue
        dyn_at[idx] = dyn_exec
        here_pipe = _stdin_pipe[idx] or dyn_exec
        here_eff = _stdin_eff[idx] or dyn_exec
        seg_words = words_of(seg, ps)
        pargs = python_position_args(seg_words)
        if pargs is not None:
            if not python_word_certainty(seg):
                raise Deny("unexpanded", "python command whose argv depends on shell expansion is not inspected")
            # The python arguments are judged here too, so a wrapper that dispatch does not know cannot hide -c code.
            python_rule(pargs)
        # Issue #637: an interpreter whose program comes from stdin and is not named in argv is refused.
        refusal = stdin_program_refusal(seg_words, here_pipe, seg)
        if refusal is not None:
            raise refusal
        # MAJOR function: pipe-fed or redirected calls to locally defined functions
        # whose bodies may run stdin-as-program interpreters are fail-closed DENY.
        # Only the command position is considered (not args like "echo f"), and
        # only same-shell prefixes ("!", "time", "coproc") are skipped -- external
        # wrappers ("sudo", "env", ...) do NOT run shell functions.
        # A same-shell call whose body persistently redirects stdin (`exec < f`
        # at its top level) changes the caller's stdin for later segments
        # (verified on Bash); forked calls (pipeline stages, subshells,
        # coprocesses) never leak back.
        call_taint = here_eff
        if all_funcs:
            _fkey, _saw_coproc, _first_idx = _call_target(seg_words, ps)
            if _fkey is not None and _fkey in all_funcs and not _is_funcdef_name(segs, idx, ps):
                if call_taint:
                    for _body in all_funcs[_fkey]:
                        if _body is None:
                            raise Deny("function", "function '" + _fkey + "' with uninspectable body called with stdin: not inspected")
                        try:
                            analyze_text(_body, ctx.copy(), ps, depth + 1, inherited_fed=True, outer_funcs=all_funcs)
                        except Deny as _d:
                            raise _d
                # MAJOR (review 5967514206): Bash is case-sensitive, PowerShell is
                # not — _func_call_name resolves per shell, never lowercasing
                # Bash `F-X` to `f-x` and never missing `+` names.
                # MAJOR-A (review 5968211099): Bash prefers same-name functions
                # over builtins/NON_EXEC, so the local function resolves before
                # the NON_EXEC shortcut (fail-closed for pipe-fed
                # `cat() { node; }; ... | cat`).
                if (not _stdin_stage[idx]
                        and not _saw_coproc and not _stdin_suppressed[idx]
                        and not _followed_by_fork_boundary(segs, idx, ps)):
                    for _body in all_funcs[_fkey]:
                        if _body is None or _body_sets_exec_stdin(_body, ps, all_funcs):
                            dyn_exec = True
                            break
            elif _first_idx < len(seg_words) and _func_call_name(seg_words[_first_idx], ps) in NON_EXEC_COMMANDS:
                pass
        # MAJOR (review 5971146586): a same-shell `eval` whose text
        # persistently redirects stdin (`eval 'exec < f'`, `eval 'f'`) changes
        # the caller's stdin for later segments (verified on Bash). The
        # syntactic pre-scan here is certain-only (strict=False): forked
        # positions never leak, and unparseable text is left to the precise
        # nested analysis (fail-closed DENY there). The scope-machine loop
        # below additionally merges the precise nested result.
        if (not ps and not _stdin_stage[idx]
                and not _stdin_suppressed[idx]
                and not _followed_by_fork_boundary(segs, idx, ps)):
            _ev_text = _eval_command_text(seg_words, ps, all_funcs)
            if _ev_text is not None and _body_sets_exec_stdin(
                    _ev_text, ps, all_funcs, strict=False):
                dyn_exec = True
        # A bare `exec` supply/close at a same-shell position updates the
        # dynamic flag too (mirrors the prepass): pipe-fed, suppressed, or
        # forked (pipeline/background) positions never leak to the parent.
        # A close clears only when its execution is certain (unconditional
        # and unforked); a conditional/loop close may never run, so
        # clearing there would under-taint (fail-open).
        if not ps and not _stdin_stage[idx] and not _stdin_suppressed[idx]:
            _fx = _exec_segment_effect(seg_words, seg)
            if _fx == "supply" and not _followed_by_fork_boundary(segs, idx, ps):
                dyn_exec = True
            elif (_fx == "close" and not _stdin_in_cond[idx]
                    and not _followed_by_fork_boundary(segs, idx, ps)
                    and not _exec_shadowed_by_function(seg_words, ps, all_funcs)):
                dyn_exec = False
    if sum(len(ops) for ops in redirs) > MAX_REDIRECTS:
        raise Deny("redirect", "more than %d redirections are not inspected" % MAX_REDIRECTS)
    rest_subs = list(subs)
    # Piece D (#636): execution-scope cwd handling. A Bash parenthesised
    # group and every stage of a Bash pipeline (or background job) run in
    # a subshell: segments inside share the parent taint (a filesystem
    # change inside outlives the subshell) but their cwd is restored at
    # the boundary. `||`/`&&` are sequential and never scope. Brace
    # groups, eval and plain sequences run in the calling shell and keep
    # sharing cwd. A stray close with no open group is ignored (lenient,
    # as before); an unclosed Bash group is a shell syntax error, so it is
    # refused instead of guessed. PowerShell `{ ... }` groups run in the
    # calling scope for cwd purposes (like Bash brace groups): `if`/`while`
    # /`foreach` bodies execute in the current scope. Only `& { ... }`
    # (child scope, uncertain) stays refused by the call-operator rule. A
    # conditional branch taken or not taken cannot be told apart statically
    # (same as Bash `if`). run-12: the body is still analysed as taken
    # (DENY/taint detection unchanged), but a cwd it leaves behind is
    # marked uncertain (cwd_fuzzy): a later relative-path judgment is
    # refused instead of trusting one branch outcome. An absolute `cd`
    # restores certainty. A failed `cd` into an absent directory keeps the
    # old cwd when nothing earlier could have created it (Gu1P).
    # run-12 OQwj: every cwd snapshot carries its certainty, so a child
    # scope exit restores both and a relative judgment never outlives the
    # scope that made it uncertain. Taint stays a single shared flag.
    scope_stack = []  # (cwd, fuzzy) per open subshell/coprocess
    bbrace_stack = []
    pipe_entry = None  # (cwd, fuzzy) or None
    run_start = (ctx.cwd, ctx.cwd_fuzzy)
    bg_start = (ctx.cwd, ctx.cwd_fuzzy)
    scope_entry = (ctx.cwd, ctx.cwd_fuzzy)
    scope_just_closed = False
    prev_was_pipe = False
    # Saved (pipe_entry, prev_was_pipe) per open Bash scope, plus the
    # brace-group stage cwd for calling-shell groups used as a stage.
    pipe_scope = []
    bstage = []  # (is_stage, cwd, fuzzy)
    bg_scope = []  # (cwd, fuzzy) per open scope
    ps_arm_stack = []  # run-13: pre-arm snapshots of closed PS conditionals
    ps_arm_moved = False  # run-13: then-arm move pending for the next PS arm entry
    ps_arm_taint = False  # run-13: then-arm taint pending for the next PS arm entry
    run_scope = []  # (run_start, bg_start) per open brace group (OQwm)
    cond_stack = []  # run-12: entry dict per open conditional/loop scope
    # Scope-local background-list start per open group: a `&` backgrounds
    # the whole in-scope list, so the parent continues from the cwd the
    # in-scope list started with (after the last in-scope `;`), not from
    # the outer bg_start.
    # Issue #637: eval-discovered persistent stdin (same-shell nested eval)
    # is found during this loop, after the first loop snapshotted dyn_at.
    live_exec = False
    live_save = []  # resolved-effect snapshots per open subshell/coprocess
    # (Issue #637 MAJOR: same scoping as dyn_exec in the first loop; a
    # fork-local discovery never leaks past the fork's close. Kept in this
    # separate block so the scope-machine anchors below stay untouched.)
    for idx, (seg, ops) in enumerate(zip(segs, redirs)):
        if seg == SUBSHELL_OPEN or seg == COPROC_OPEN:
            live_save.append(live_exec)
        elif seg == SUBSHELL_CLOSE or seg == COPROC_CLOSE:
            if live_save:
                live_exec = live_save.pop()
        if seg == SUBSHELL_OPEN:
            if not ps:
                if prev_was_pipe and pipe_entry is not None:
                    # A group right after `|` is the next stage: it forks
                    # from the entry, and inner commands must not see or
                    # move the outer pipeline state.
                    ctx.cwd, ctx.cwd_fuzzy = pipe_entry
                pipe_scope.append((pipe_entry, prev_was_pipe))
                pipe_entry = None
                prev_was_pipe = False
                if not scope_stack and not bbrace_stack:
                    # A top-level `( ... )` may be the whole first pipeline
                    # stage: remember the pre-group cwd for the entry.
                    scope_entry = (ctx.cwd, ctx.cwd_fuzzy)
                    scope_just_closed = False
                # run-13 (M1): the subshell owns its pipeline/list start:
                # an inner `cd` moves the subshell's run, never the outer one.
                run_scope.append((run_start, bg_start))
                run_start = (ctx.cwd, ctx.cwd_fuzzy)
                bg_start = (ctx.cwd, ctx.cwd_fuzzy)
                scope_stack.append((ctx.cwd, ctx.cwd_fuzzy))
                bg_scope.append((ctx.cwd, ctx.cwd_fuzzy))
            continue
        if seg == SUBSHELL_CLOSE:
            if not ps and scope_stack:
                ctx.cwd, ctx.cwd_fuzzy = scope_stack.pop()
                if run_scope:
                    run_start, bg_start = run_scope.pop()
                if pipe_scope:
                    pipe_entry, prev_was_pipe = pipe_scope.pop()
                if bg_scope:
                    bg_scope.pop()
                if not scope_stack and not bbrace_stack:
                    scope_just_closed = True
            continue
        if seg == BBRACE_OPEN:
            # Calling-shell group: cwd is shared, so nothing is restored
            # at the close -- unless the group itself is a pipeline stage,
            # which forks like any other stage. The pre-group cwd is still
            # the pipeline entry when the whole group is the first stage.
            is_stage = bool(prev_was_pipe and pipe_entry is not None)
            if is_stage:
                ctx.cwd, ctx.cwd_fuzzy = pipe_entry
            pipe_scope.append((pipe_entry, prev_was_pipe))
            pipe_entry = None
            prev_was_pipe = False
            bstage.append((is_stage, ctx.cwd, ctx.cwd_fuzzy))
            if not scope_stack and not bbrace_stack:
                scope_entry = (ctx.cwd, ctx.cwd_fuzzy)
                scope_just_closed = False
            bbrace_stack.append(ctx.cwd)
            bg_scope.append((ctx.cwd, ctx.cwd_fuzzy))
            # run-12 OQwm: the group runs the calling shell, so its `;`
            # advances a group-local pipeline/list start; the outer one
            # is restored at the close.
            run_scope.append((run_start, bg_start))
            continue
        if seg == BBRACE_CLOSE:
            if bbrace_stack:
                bbrace_stack.pop()
                if bg_scope:
                    bg_scope.pop()
                if run_scope:
                    run_start, bg_start = run_scope.pop()
                was_stage, stage_cwd, stage_fuzzy = bstage.pop() if bstage else (False, ctx.cwd, ctx.cwd_fuzzy)
                if was_stage:
                    # A brace group used as a pipeline stage forked: drop
                    # the body-local cwd instead of leaking it.
                    ctx.cwd, ctx.cwd_fuzzy = stage_cwd, stage_fuzzy
                if pipe_scope:
                    pipe_entry, prev_was_pipe = pipe_scope.pop()
                if not scope_stack and not bbrace_stack:
                    scope_just_closed = True
            continue
        if seg == COPROC_OPEN:
            # A `coproc { ... }` compound body runs in a subshell: same
            # scope handling as `( ... )`. The `coproc ...` segment itself
            # is still dispatched (in a taint-only copy) by analyze_argv.
            if prev_was_pipe and pipe_entry is not None:
                ctx.cwd, ctx.cwd_fuzzy = pipe_entry
            pipe_scope.append((pipe_entry, prev_was_pipe))
            pipe_entry = None
            prev_was_pipe = False
            if not scope_stack and not bbrace_stack:
                scope_entry = (ctx.cwd, ctx.cwd_fuzzy)
                scope_just_closed = False
            # run-13 (M1): the coprocess body runs in a subshell, so it
            # owns its pipeline/list start like `( ... )`.
            run_scope.append((run_start, bg_start))
            run_start = (ctx.cwd, ctx.cwd_fuzzy)
            bg_start = (ctx.cwd, ctx.cwd_fuzzy)
            scope_stack.append((ctx.cwd, ctx.cwd_fuzzy))
            bg_scope.append((ctx.cwd, ctx.cwd_fuzzy))
            continue
        if seg == COPROC_CLOSE:
            if scope_stack:
                ctx.cwd, ctx.cwd_fuzzy = scope_stack.pop()
                if run_scope:
                    run_start, bg_start = run_scope.pop()
                if bg_scope:
                    bg_scope.pop()
                if pipe_scope:
                    pipe_entry, prev_was_pipe = pipe_scope.pop()
                if not scope_stack and not bbrace_stack:
                    scope_just_closed = True
            continue
        if seg == PSCOND_OPEN:
            # run-12 Gu1n: a PowerShell conditional/loop body in braces
            # runs in the calling scope when taken, but the outcome is
            # uncertain: remember the entry and mark the cwd uncertain
            # on close when the body moved it.
            cond_stack.append(_cond_entry(ctx))
            if ps_arm_moved or ps_arm_taint:
                # run-13 (M3): this is an else/elseif body: inherit the
                # then-arm outcome so the close joins every arm.
                cond_stack[-1]["moved"] = cond_stack[-1]["moved"] or ps_arm_moved
                cond_stack[-1]["then_taint"] = cond_stack[-1]["then_taint"] or ps_arm_taint
                ps_arm_moved = False
                ps_arm_taint = False
            # run-12 P7k3/RZ4V: every block open snapshots the live arm
            # state, so an `elseif` condition's own writes are kept for
            # a later arm instead of being replaced by stale state.
            cond_stack[-1]["arm"] = (ctx.cwd, ctx.cwd_fuzzy, ctx.tainted)
            continue
        if seg == PSCOND_CLOSE:
            if cond_stack:
                # run-12 OQww: join the arm outcomes conservatively: any
                # move inside the scope leaves the cwd uncertain, and
                # taint from either arm reaches later segments.
                entry = cond_stack.pop()
                # run-12 OQww/P7kz: join all outcomes including the
                # skipped body: entry uncertainty, any move, or any end
                # difference leaves the cwd uncertain.
                if entry["fuzzy"] or entry["moved"] or ctx.cwd != entry["cwd"]:
                    ctx.cwd_fuzzy = True
                ctx.tainted = ctx.tainted or entry["then_taint"]
                if cond_stack and (entry["moved"] or ctx.cwd != cond_stack[-1]["cwd"]):
                    cond_stack[-1]["moved"] = True
                # run-13 (M3): keep the pre-arm snapshot for a following
                # else/elseif arm, whose entry was already popped. Each
                # arm restarts from this base and joins at its own close.
                ps_arm_stack.append(entry)
            continue
        if seg == CMD_BOUND:
            # A `;`/newline ends a pipeline and a background list: later
            # foreground segments are judged from the pipeline-entry cwd,
            # never from a finished stage's cwd. Entry snapshots restart
            # after the boundary for the next list.
            if not ps and pipe_entry is not None:
                ctx.cwd, ctx.cwd_fuzzy = pipe_entry
                pipe_entry = None
            prev_was_pipe = False
            # run-13 (M1): the snapshots are scope-local, so an in-scope
            # `;` moves the scope's list start. Conditionals still gate
            # the update (Gu1Y): an arm-end cwd must not become the run.
            if not cond_stack:
                run_start = (ctx.cwd, ctx.cwd_fuzzy)
                bg_start = (ctx.cwd, ctx.cwd_fuzzy)
            if bg_scope and (scope_stack or bbrace_stack):
                # A `;`/newline inside a scope group starts a new list
                # there: a later `&` restores this scope-local start,
                # never the stale outer bg_start.
                bg_scope[-1] = (ctx.cwd, ctx.cwd_fuzzy)
            scope_just_closed = False
            continue
        if seg == PIPE_END:
            # run-12 OQwd: `&&`/`||` terminate the pipeline like `;` (the
            # next command runs in the enclosing cwd), but they do not
            # start a new background list: bg_start/bg_scope are kept.
            if not ps and pipe_entry is not None:
                ctx.cwd, ctx.cwd_fuzzy = pipe_entry
                pipe_entry = None
            prev_was_pipe = False
            # run-13 (M1): scope-local like `;` above (bg_start is the
            # list start and stays untouched by `&&`/`||` by design).
            if not cond_stack:
                run_start = (ctx.cwd, ctx.cwd_fuzzy)
            scope_just_closed = False
            continue
        if seg == PIPE_BOUND:
            # Piece D (#636, CRITICAL-1): the next stage starts from the
            # pipeline-entry cwd, never from a sibling stage's cwd. The
            # entry is the pre-execution cwd of the first stage's run
            # (run_start tracks sequential `cd`, but never a stage body or
            # a keyword/empty segment; a scope that is itself the first
            # stage contributes its pre-group cwd via scope_entry).
            # run-13 (M2): the entry keeps the snapshot's own certainty:
            # cwd and fuzzy are always the same-instant pair, so a first
            # stage that clears the certainty cannot launder a stale cwd
            # into certain. The snapshot is refreshed when a conditional
            # closes (below), so uncertainty set just before the pipe
            # still reaches the stages (OQwj).
            if not ps:
                if pipe_entry is None:
                    if scope_just_closed:
                        pipe_entry = scope_entry
                    elif cond_stack:
                        # run-12 RZ4Y: inside a conditional the saved run
                        # start is stale (updates are gated); the active
                        # arm's current pair is the pipeline entry.
                        pipe_entry = (ctx.cwd, ctx.cwd_fuzzy)
                    else:
                        pipe_entry = run_start
                prev_was_pipe = True
                scope_just_closed = False
            continue
        if seg == BG_BOUND:
            # A `&` backgrounds the whole list in a subshell: the parent
            # (following foreground segments) continues at once from the
            # cwd the list started with, and the list's pipeline state ends
            # here. A `cd` left of `&` stays in the background job. Inside
            # a scope group the list started at the scope-local `;`
            # boundary (bg_scope), never at the outer bg_start: without
            # this a `cd` left of `&` leaks into the in-scope foreground.
            if not ps:
                if (scope_stack or bbrace_stack) and bg_scope:
                    ctx.cwd, ctx.cwd_fuzzy = bg_scope[-1]
                else:
                    ctx.cwd, ctx.cwd_fuzzy = bg_start
                pipe_entry = None
                prev_was_pipe = False
                scope_just_closed = False
            continue
        if seg == PSBRACE_OPEN or seg == PSBRACE_CLOSE:
            # Issue #637: stdin-provenance marker for ps `{ ... }`; the cwd
            # machine ignores it (calling scope, like PSCOND bodies).
            continue
        seg_words = words_of(seg, ps)
        first_cmd = next((w for w in seg_words if not ASSIGN.match(w)), None)
        if not ps and cond_stack and first_cmd == "then":
            # run-12 OQww/P7k3/RZ4R: the condition just ended; snapshot the
            # arm entry (cwd, certainty, taint). Every `else`/`elif` arm
            # restarts from here instead of the previous arm's end, so a
            # condition that moves the cwd (or taints) is honored while a
            # previous arm's effects stay joined only at the close.
            cond_stack[-1]["arm"] = (ctx.cwd, ctx.cwd_fuzzy, ctx.tainted)
        if not ps and first_cmd in COND_OPENERS:
            # run-12 Gu1Y: a Bash conditional/loop body is analysed (DENY
            # and taint detection unchanged), but the cwd it leaves is
            # one branch outcome of several: later segments must not
            # trust it via run_start/pipe_entry either.
            cond_stack.append(_cond_entry(ctx))
        elif not ps and cond_stack and first_cmd in ("then", "else", "do", "elif"):
            # run-12 OQwq: a nested opener after a branch/loop keyword
            # (`then if true`) starts its own scope; the inner `fi`
            # must not pop the outer entry. Only command position after
            # the keyword counts, never an argument.
            rest = [w for w in seg_words if not ASSIGN.match(w)]
            if len(rest) > 1 and rest[1] in COND_OPENERS:
                cond_stack.append(_cond_entry(ctx))
        if ps and first_cmd in ("else", "elseif") and ps_arm_stack:
            # run-13 (M3): a PowerShell then-body close pops its entry
            # before the else/elseif arm starts, so the arm restarts from
            # the stashed pre-conditional snapshot (cwd, certainty,
            # taint), never from the previous arm's end or an outer
            # conditional's entry. The then-arm outcome (move/taint) is
            # carried into the next arm entry and joins at its close, so
            # the post-chain state covers every arm taken or not.
            # P7k3: condition taint wins over the pre-conditional taint.
            entry = ps_arm_stack[-1]
            entry["then_taint"] = entry["then_taint"] or ctx.tainted
            arm = entry["arm"]
            if arm is None:
                arm = (entry["cwd"], entry["fuzzy"], entry["tainted"])
            ctx.cwd, ctx.cwd_fuzzy, ctx.tainted = arm
            ps_arm_moved = entry["moved"]
            ps_arm_taint = entry["then_taint"]
        elif cond_stack and first_cmd in ("else", "elif"):
            # run-12 OQww/P7k3: each arm runs from the conditional entry, not
            # from the previous arm's end. Condition taint (captured at
            # `then`, at the PowerShell block open, or lazily here) is kept;
            # the previous arm's taint is dropped here and joined at close.
            # run-13 (M3): the certainty is restored too, so a previous
            # arm's absolute cd cannot clear an uncertain entry for the
            # else arm.
            entry = cond_stack[-1]
            entry["then_taint"] = entry["then_taint"] or ctx.tainted
            if entry["arm"] is None:
                entry["arm"] = (ctx.cwd, ctx.cwd_fuzzy, ctx.tainted)
            ctx.cwd, ctx.cwd_fuzzy, ctx.tainted = entry["arm"]
        if not seg_words and not ops:
            # A boundary flush with no words and no redirects (e.g. the
            # empty segment before `{` in `| { ...`): consumes no pipeline
            # state at all -- not even the stage reset. The next real
            # segment (or scope open) is still the stage.
            continue
        if prev_was_pipe and not ps and pipe_entry is not None:
            ctx.cwd, ctx.cwd_fuzzy = pipe_entry
        # Refresh the pipeline-entry candidate only at a top-level segment
        # with a real command word, so a compound body or an empty/keyword
        # segment never moves it (Piece D, #636 CRITICAL-1). A scope that
        # just closed stays the entry source until a real segment (or a
        # `;` boundary) starts a new run: empty segments never clear it.
        # A scope group owns its inner pipeline state on pipe_scope, so an
        # inner command never ends the enclosing entry lifetime.
        if not scope_stack and not bbrace_stack and seg_words:
            scope_just_closed = False
        if (not prev_was_pipe
                and not cond_stack
                and any(
                    w not in KEYWORDS and not ASSIGN.match(w)
                    for w in seg_words)):
            # run-13 (M1): the refresh is scope-local, so `cd` inside a
            # subshell moves the subshell's run without touching the
            # outer run. Conditionals still gate it (Gu1Y).
            run_start = (ctx.cwd, ctx.cwd_fuzzy)
        if (not prev_was_pipe and not scope_stack
                and not cond_stack):
            # A top-level non-stage segment ends the pipeline entry
            # lifetime. Segments inside a scope group must not end the
            # enclosing entry: the group owns a saved copy on pipe_scope.
            pipe_entry = None
        prev_was_pipe = False
        # A substitution runs when its containing segment runs: analyse it
        # with this segment's incoming stdin taint (Issue #637: pipe-fed or
        # exec-redirected stdin reaches the substitution, but not the
        # segment's own `<` redirect, which applies after expansion), so it
        # taints only this and later segments (Piece D, #636).
        # live_exec (same-shell eval discovery in this loop) joins here too.
        n = seg.count(SUBST)
        bodies, rest_subs = rest_subs[:n], rest_subs[n:]
        for body in bodies:
            _nested_text(body, ctx, ps, depth + 1,
                         fed=(_stdin_pipe[idx] or dyn_at[idx] or live_exec),
                         outer_funcs=all_funcs)
        if ops and inside_claude(ctx.cwd, ctx):
            raise Deny("claude-write", "redirect while cwd is inside .claude/ is denied")
        # Redirects of one segment share its incoming taint for their checks;
        # a write they perform is still recorded for later segments.
        incoming, wrote = ctx.tainted, False
        for op, target in ops:
            ctx.tainted = incoming
            check_redirect(target, op, ctx, ps)
            wrote = wrote or ctx.tainted
        if wrote:
            ctx.tainted = True
        analyze_argv(words_of(seg, ps), ctx, ps, depth,
                     fed=(_stdin_eff[idx] or dyn_at[idx] or live_exec),
                     outer_funcs=all_funcs)
        if ctx.exec_out:
            # A same-shell nested eval persistently redirected stdin during
            # this segment (MAJOR review 5971146586). Forked positions never
            # leak: a pipeline stage, subshell, or coprocess eval runs in a
            # child, and a definition body never executes, so only unforked
            # positions outside definitions propagate to later segments.
            # PowerShell has no eval/exec persistent-redirect semantics.
            ctx.exec_out = False
            if (not ps and not _stdin_stage[idx]
                    and not _stdin_suppressed[idx]
                    and not _followed_by_fork_boundary(segs, idx, ps)):
                live_exec = True
        # A certain bare-exec close clears the live flag too (mirrors the
        # first-loop dyn handling): a conditional/loop close may never run,
        # so only unconditional closes clear (fail-open otherwise). The
        # save/restore above scopes the clear to the active fork.
        if (live_exec and not ps and not _stdin_stage[idx]
                and not _stdin_suppressed[idx] and not _stdin_in_cond[idx]
                and not _followed_by_fork_boundary(segs, idx, ps)
                and not _exec_shadowed_by_function(seg_words, ps, all_funcs)
                and _exec_segment_effect(seg_words, seg) == "close"):
            live_exec = False
        if cond_stack and ctx.cwd != cond_stack[-1]["cwd"]:
            # run-12 OQww: any move inside the scope makes the outcome
            # uncertain, even when a later arm moves it back.
            cond_stack[-1]["moved"] = True
        if not ps and cond_stack and first_cmd in COND_CLOSERS:
            # run-12: the body ran above (still analysed as taken); a
            # cwd it moved is uncertain, since the branch may not run.
            # A stray closer with no open scope is ignored, as before.
            entry = cond_stack.pop()
            # run-12 OQww/P7kz: join all outcomes including the skipped
            # body (same rule as the PowerShell close).
            if entry["fuzzy"] or entry["moved"] or ctx.cwd != entry["cwd"]:
                ctx.cwd_fuzzy = True
            ctx.tainted = ctx.tainted or entry["then_taint"]
            if cond_stack and (entry["moved"] or ctx.cwd != cond_stack[-1]["cwd"]):
                cond_stack[-1]["moved"] = True
            if not cond_stack:
                # run-13 (M2): the run start is the same-instant pair
                # again, so a later pipeline entry keeps its own
                # certainty instead of borrowing the live one. The
                # refresh is scope-local (M1) inside a subshell.
                run_start = (ctx.cwd, ctx.cwd_fuzzy)
    for body in rest_subs:
        # Defensive: a body no segment claimed is still analysed, never skipped.
        # Fail-closed tainted: an unclaimed body may belong to any segment.
        _nested_text(body, ctx, ps, depth + 1, fed=True, outer_funcs=all_funcs)
    if scope_stack:
        # An unclosed subshell never runs in a real shell: refuse instead
        # of judging later segments against a guessed cwd (fail closed).
        raise Deny("parse", "unbalanced subshell grouping is not inspected")
    if bbrace_stack:
        # An unclosed Bash brace group never runs either (fail closed).
        raise Deny("parse", "unbalanced brace grouping is not inspected")
    # Issue #637: report the persistent stdin state for same-shell callers
    # (eval/function merging). Static pre-pass taint or any later dynamic
    # (function/eval) propagation both count as persistent.
    # MAJOR (Issue #637): a persistent stdin effect found by resolved
    # dispatch (live_exec) counts like the static findings: an enclosing
    # same-shell eval inherits it through merge_exec. Static and resolved
    # findings are scoped identically, so no fork-local effect leaks here.
    return bool(_prepass_end or dyn_exec or live_exec)


def load_registry(root):
    """Worktree paths with a recorded owner. Missing or broken file: empty set."""
    path = os.path.join(root, ".claude", "worktree-owners.json")
    keys = set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for ent in data.get("worktrees", []):
            if (isinstance(ent, dict) and isinstance(ent.get("path"), str)
                    and isinstance(ent.get("owner"), str) and ent["owner"].strip()):
                keys.add(key(ent["path"]))
    except FileNotFoundError:
        pass
    except Exception:
        return set()
    return keys


def _decide(raw, repo_root):
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise Deny("input", "invalid JSON")
    if not isinstance(payload, dict):
        raise Deny("input", "payload is not an object")
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise Deny("input", "missing tool_name")
    if tool not in SHELL_TOOLS:
        return ALLOW, "not a shell tool"
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise Deny("input", "missing tool_input")
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        raise Deny("input", "missing command")
    if not cmd.strip():
        raise Deny("input", "empty command")
    if len(cmd.encode("utf-8", "replace")) > MAX_CMD_BYTES:
        raise Deny("size", "too large to inspect")
    # NUL and other control characters (CR, BEL...) are not inspectable as shell text.
    if any((ord(ch) < 32 and ch not in "\t\n") or ch == "\x7f" for ch in cmd):
        raise Deny("input", "NUL or control character in command")
    root = os.path.realpath(repo_root or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = root
    if not (cwd.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", cwd)):
        raise Deny("input", "cwd is not absolute")
    homes = home_roots()
    if not homes:
        # Fail closed: the protected set cannot be determined without a home directory.
        raise Deny("home", "home directory cannot be determined; protected set unknown")
    protected = [key(p) for p in PROTECTED_BASE + homes + local_roots(root)]
    protected += [key(root), key(os.path.join(root, ".git"))]
    ctx = Ctx(canon(cwd), key(root), key(os.path.join(root, ".git")),
              load_registry(root), protected)
    # sftp reads its commands from stdin when no batch file is given, and a "!" line runs a local command. Piped or
    # redirected input cannot be inspected here, so an sftp command with a pipe or an input redirection is DENY.
    # Quotes and backslashes are removed first (s''ftp, \sftp name sftp); coproc feeds its command a pipe too.
    squashed = re.sub(r"[\"'\\]", "", cmd)
    if re.search(r"sftp", squashed, re.IGNORECASE) and (
            re.search(r"[|<]", cmd) or re.search(r"\bcoproc\b", cmd, re.IGNORECASE)):
        raise Deny("program-option", "sftp with piped or redirected input or a coprocess: its commands are not inspected")
    # A program-naming variable can be built at run time in PowerShell (Set-Item env:, concatenation, $env:). With a
    # program tool in the same command the environment is not inspected: denied (fail closed).
    if tool == "PowerShell" and re.search(r"env:|\$env|environment", cmd, re.IGNORECASE) and re.search(
            r"\b(" + "|".join(sorted(PROGRAM_TOOLS)) + r")\b", cmd, re.IGNORECASE):
        raise Deny("program-option", "a PowerShell environment change with a program tool is not inspected")
    # A variable that changes the search path or names a program (PATH, PATH+=, LD_*, RSYNC_*, ...), set in one segment or
    # in a string, changes which program a tool in another segment starts. That cannot be shown safe statically, so a
    # mention of such a variable with a program tool anywhere in the same command is DENY (fail closed).
    # PATH is a variable here, not the tail of an option (--rsync-path, find -path): it must not follow - / or .
    if re.search(r"(?i)((?<![A-Za-z0-9_\-/.])PATH|(?<![A-Za-z0-9_])(" + "|".join(re.escape(v) for v in PROGRAM_ENV) + r"))"
                 r"(?![A-Za-z0-9_])",
                 squashed) and re.search(
            r"(?i)(?<![A-Za-z0-9_])(" + "|".join(sorted(PROGRAM_TOOLS | set(PROGRAM_ALIAS) | DEV_TOOLS)) + r")(?![A-Za-z0-9_])",
            squashed):
        raise Deny("program-option", "PATH or a program-naming variable with a program tool in one command: not inspected")
    analyze_text(cmd, ctx, tool == "PowerShell", 0)
    return ALLOW, "no destructive operation found"


def decide(raw, repo_root=None):
    """Return (ALLOW|DENY, reason). Never includes the command text."""
    try:
        return _decide(raw, repo_root)
    except Deny as d:
        return DENY, d.kind + ": " + d.reason
    except Exception:
        return DENY, "guard internal error"


def run_hook(raw, timeout=HOOK_TIMEOUT):
    """Return hook stdout: deny JSON, or "" to allow. Timeouts and errors deny."""
    box = {}

    def work():
        try:
            r = decide(raw)
            box["verdict"] = (r[0], r[1])
        except BaseException:
            box["verdict"] = (DENY, "guard exception")

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        verdict = (DENY, "timeout")
    else:
        verdict = box.get("verdict", (DENY, "no verdict"))
    if verdict[0] == ALLOW:
        return ""
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": ("[destructive-guard] blocked (fail-closed): "
                                     + verdict[1]
                                     + ". Command not run. See docs/claude-destructive-guard.md"),
    }})


def main():
    # Read bytes and decode UTF-8 ourselves: the locale codec (cp932 on Windows) must not
    # decide. A decode error yields "" which json rejects, so it is denied.
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception:
        raw = ""
    out = run_hook(raw)
    if out:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
