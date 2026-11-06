#!/usr/bin/env python3
"""Claude Code PreToolUse guard: deny destructive shell commands, fail-closed.

Reads hook JSON on stdin. A DENY prints hookSpecificOutput JSON with
permissionDecision "deny" (exit 0). An ALLOW prints nothing (exit 0), so the
normal permission flow still applies. The command is only inspected, never run.
Rules: docs/claude-destructive-guard.md.
"""
import ast
import fnmatch
import json
import os
import posixpath
import re
import shlex
import sys
import threading

HOOK_TIMEOUT = 5.0
# ponytail: hard size cap bounds the work; the thread timeout cannot stop C-level code.
MAX_CMD_BYTES = 64 * 1024
MAX_DEPTH = 8
ALLOW, DENY = "ALLOW", "DENY"
SHELL_TOOLS = ("Bash", "PowerShell")
KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done",
            "while", "until", "(", ")"}
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
                       r"shutil|spawn|exec|eval|__import__|write|truncate|"
                       r"ctypes|socket|replace|rename|chmod|chown|symlink|copy|move|dump\b|link|"
                       r"fileio|touch|mkdir|makedirs|mkfifo|mknod|utime|"
                       r"getattr|globals|locals|vars|builtins|__dict__", re.I)
# python -c: open() is judged on parsed AST (see _open_is_read_only), never by regex.
READ_MODES = frozenset({"r", "rb", "br", "rt", "tr"})
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


class Ctx:
    def __init__(self, cwd, root, git, registry, protected):
        self.cwd = cwd            # canonical absolute path
        self.root = root          # case-folded canonical repo root
        self.git = git            # case-folded canonical .git directory
        self.registry = registry  # case-folded worktree paths with a recorded owner
        self.protected = protected

    def copy(self, **kw):
        c = Ctx(self.cwd, self.root, self.git, self.registry, self.protected)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


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


def resolve_full(raw, ctx, deny_unresolvable_dotdot=False):
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


def resolve(raw, ctx):
    """Return (lexical canonical, real canonical) for a path argument."""
    lex, real, _ = resolve_full(raw, ctx)
    return lex, real


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
        return
    raise Deny("redirect", "redirect writes outside the repository or to a protected path: " + raw)


def split_segments(text, ps=False):
    """Split on unquoted ; & | ( ) and newlines.

    Returns (segments, substitution bodies, redirect targets per segment). A redirect
    target is the raw word after each unquoted '>' ("" when there is none).
    """
    segs, subs, cur, redirs, rd = [], [], [], [], []
    quote, i, n = None, 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n and not ps:
            if text[i + 1] == "\n":          # line continuation
                i += 2
                continue
            cur.append(text[i:i + 2])
            i += 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            body, i = _matched(text, i + 2)
            subs.append(body)
            cur.append(SUBST)
            continue
        if c in "<>" and quote is None and i + 1 < n and text[i + 1] == "(":
            body, i = _matched(text, i + 2)  # process substitution
            subs.append(body)
            cur.append(SUBST)
            continue
        if c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise Deny("parse", "unterminated backtick")
            subs.append(text[i + 1:j])
            cur.append(SUBST)
            i = j + 1
            continue
        if c == "$" and quote is None and i + 1 < n and text[i + 1] in "'\"":
            raise Deny("parse", "ANSI-C or locale quoting not inspected")
        if quote == '"':
            if c == '"':
                quote = None
            cur.append(c)
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
                start = i - k
            if c == ">":
                rd.append((text[i:j], _redir_target(text, j)))
            cur.append(" " + REDIR_MARK + text[start:j] + " ")
            i = j
            continue
        if quote is None and c == "&" and i + 1 < n and text[i + 1] == ">":
            j = i + 1
            while j < n and text[j] == ">":
                j += 1
            rd.append((text[i:j], _redir_target(text, j)))
            cur.append(" " + REDIR_MARK + text[i:j] + " ")
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
            segs.append("".join(cur))
            redirs.append(rd)
            cur, rd = [], []
            i += 1
            continue
        cur.append(c)
        i += 1
    if quote:
        raise Deny("parse", "unterminated quote")
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


def worktree_remove(args, ctx):
    if any(a == "-f" or git_long_is(a, ("--force",)) for a in args):
        raise Deny("git-worktree-remove", "forced worktree removal is denied")
    targets = [a for a in args if not a.startswith("-")]
    if len(targets) != 1:
        raise Deny("git-worktree-remove", "expected exactly one worktree path")
    _, real = resolve(targets[0], ctx)
    k = real.casefold()
    if k not in ctx.registry:
        raise Deny("git-worktree-remove", "worktree owner not recorded; treated as protected")
    if is_protected(k, ctx) or k == ctx.root:
        raise Deny("git-worktree-remove", "protected worktree path")


def git_long_is(a, names):
    """True if a is one of the git long options, or an unambiguous prefix of one: git accepts --forc for --force, so a prefix
    of a denied option is denied (fail closed; a prefix that also names an allowed option is over-denied)."""
    if not a.startswith("--") or len(a) < 3:
        return False
    key = a[2:].split("=", 1)[0]
    return any(n[2:].startswith(key) for n in names)


def git_rule(args, ctx):
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


def find_rule(args, ps, ctx, depth):
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
            analyze_argv(args[i + 1:j], ctx, ps, depth + 1)


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


def _open_is_read_only(code):
    """True only if the code parses and every use of `open` is builtin open(path) or open(path, <literal read mode>).

    Judged on the AST, so string literals cannot hide a call's real arguments. Any other reference to
    `open` (attribute such as io.open or x.open, a bare name used as a value, keyword or starred
    arguments, a non-literal or write mode) makes the code not read-only.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return False
    call_funcs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            if node.keywords or len(node.args) not in (1, 2) or any(isinstance(a, ast.Starred) for a in node.args):
                return False
            if len(node.args) == 2:
                mode = node.args[1]
                if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                        and mode.value in READ_MODES):
                    return False
            call_funcs.add(id(node.func))
        elif isinstance(node, ast.Name) and node.id in ("builtins", "__builtins__"):
            return False
        elif isinstance(node, ast.Name) and node.id == "open" and id(node) not in call_funcs:
            return False
        elif isinstance(node, ast.Attribute) and node.attr == "open":
            return False
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == "open":
            # A definition shadows the builtin even though its name is stored as a string in the AST.
            return False
        elif isinstance(node, ast.arg) and node.arg == "open":
            # An argument can shadow the builtin without creating an ast.Name Store node.
            return False
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # `from io import open as o` and `import builtins` reach open without a Name "open".
            for alias in node.names:
                if alias.name in ("open", "builtins") or alias.asname in ("open", "builtins"):
                    return False
    return True


def python_rule(args):
    # A shell variable or substitution in a python argument: its value is not visible here.
    for arg in args:
        if "$" in arg or "`" in arg or SUBST in arg:
            raise Deny("unexpanded", "python argument with an unexpanded variable or substitution is not inspected")
    for code in python_c_codes(args):
        if DANGER_PY.search(code) or not _open_is_read_only(code):
            raise Deny("python-c", "python -c with a file or process operation")


def shell_rule(args, ctx, depth):
    for i, a in enumerate(args):
        if short_has(a, "c"):
            if i + 1 >= len(args):
                raise Deny("shell", "shell -c without code")
            analyze_text(args[i + 1], ctx.copy(), False, depth + 1)
            return
    raise Deny("shell", "shell without -c is not inspected")


def pwsh_rule(args, ctx, depth):
    low = [a.lower() for a in args]
    for a in low:
        if a.startswith("-enc") or a in ("-e", "-ec"):
            raise Deny("powershell", "encoded command is not inspected")
        if len(a) >= 2 and "-file".startswith(a):
            raise Deny("powershell", "-File script is not inspected")
    for i, a in enumerate(low):
        if a == "-c" or (len(a) > 2 and "-command".startswith(a)):
            analyze_text(" ".join(args[i + 1:]), ctx.copy(), True, depth + 1)
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


def analyze_argv(words, ctx, ps, depth):
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
            analyze_argv(rest, ctx.copy(), ps, depth + 1)
            if len(rest) >= 2:
                analyze_argv(rest[1:], ctx.copy(), ps, depth + 1)
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
                analyze_argv(cand, ctx.copy(), ps, depth + 1)
            return
        if handoff(name):
            for cand in wrapper_candidates(name, words[1:], ctx):
                # An option-looking word (-n, --command=...) or a number (timeout 5) cannot be the command a wrapper starts;
                # the command is a later suffix. Strings are covered by the text analysis below.
                # For a wrapper with a grammar the command word is exact, so it is analysed even when it looks like an option.
                if name not in WRAPPER_OPTS and cand and (cand[0].startswith("-") or cand[0].isdigit()):
                    continue
                analyze_argv(cand, ctx.copy(), ps, depth + 1)
            if name in STRING_LAUNCHERS:
                # A shell string argument is text, whatever its shape: a quoted string with a space (sudo -s "rm -rf x"),
                # the value after -c or --command (flock -c rm lock), and the value after --name= (flock --command="rm").
                # Plain words and options are commands for the suffix pass, which the grammar narrows to the real command.
                for k in range(1, len(words)):
                    if not words[k].startswith("-") and any(ch.isspace() for ch in words[k]):
                        analyze_text(words[k], ctx.copy(), ps, depth + 1)
                    if words[k] in ("-c", "--command") and k + 1 < len(words):
                        analyze_text(words[k + 1], ctx.copy(), ps, depth + 1)
                    if words[k].startswith("--") and "=" in words[k]:
                        analyze_text(words[k].split("=", 1)[1], ctx.copy(), ps, depth + 1)
            return
        break
    args = words[1:]
    program_option_rule(name, args, ctx)
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
        for k in range(len(tokens)):
            analyze_text(" ".join(tokens[k:]), ctx.copy(), True, depth + 1)
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
        analyze_text(" ".join(args), ctx.copy(), ps, depth + 1)
    elif name in SHELLS:
        shell_rule(args, ctx, depth)
    elif name in PS_HOSTS:
        pwsh_rule(args, ctx, depth)
    elif name in CD_NAMES:
        if args and args[0].startswith("-"):
            raise Deny("cd", "cd with option is not inspected")
        if args and any(c in args[0] for c in "*?[") and names_claude(args[0]):
            raise Deny("cd", "cd into a glob that may name .claude/ is not inspected")
        ctx.cwd = resolve(args[0] if args else "~", ctx)[1]
    elif name in ALWAYS_DENY:
        raise Deny("destructive", name + " is denied")
    elif name == "rm" or name in PS_RM:
        rm_rule(args, ps or name in PS_RM, ctx)
    elif name == "git":
        git_rule(args, ctx)
    elif name == "find":
        find_rule(args, ps, ctx, depth)
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


def analyze_text(text, ctx, ps, depth):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    if ps and METHOD_CALL_RE.search(text):
        raise Deny("powershell", "PowerShell method call is not inspected")
    if ps and PS_PROC_LAUNCH_RE.search(text) and EXPR_SYNTAX_RE.search(text):
        raise Deny("launcher", "PowerShell process launcher with expression syntax is not inspected")
    segs, subs, redirs = split_segments(text, ps)
    # A python command whose argv is not certain (unquoted or double-quoted expansion characters) is refused.
    for seg in segs:
        pargs = python_position_args(words_of(seg, ps))
        if pargs is not None:
            if not python_word_certainty(seg):
                raise Deny("unexpanded", "python command whose argv depends on shell expansion is not inspected")
            # The python arguments are judged here too, so a wrapper that dispatch does not know cannot hide -c code.
            python_rule(pargs)
    if sum(len(ops) for ops in redirs) > MAX_REDIRECTS:
        raise Deny("redirect", "more than %d redirections are not inspected" % MAX_REDIRECTS)
    for body in subs:
        analyze_text(body, ctx.copy(), ps, depth + 1)
    for seg, ops in zip(segs, redirs):
        if ops and inside_claude(ctx.cwd, ctx):
            raise Deny("claude-write", "redirect while cwd is inside .claude/ is denied")
        for op, target in ops:
            check_redirect(target, op, ctx, ps)
        analyze_argv(words_of(seg, ps), ctx, ps, depth)


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
