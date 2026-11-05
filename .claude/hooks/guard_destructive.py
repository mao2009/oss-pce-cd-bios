#!/usr/bin/env python3
"""Claude Code PreToolUse guard: deny destructive shell commands, fail-closed.

Reads hook JSON on stdin. A DENY prints hookSpecificOutput JSON with
permissionDecision "deny" (exit 0). An ALLOW prints nothing (exit 0), so the
normal permission flow still applies. The command is only inspected, never run.
Rules: docs/claude-destructive-guard.md.
"""
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
CD_NAMES = {"cd", "chdir", "set-location", "sl"}
# Always denied: destructive by nature, or cannot be inspected.
ALWAYS_DENY = {"unlink", "shred", "rmdir", "rd", "dd", "mkfs", "truncate", "wipefs",
               "srm", "cmd", "cmd.exe"}
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
                       r"shutil|spawn|exec|eval|__import__|open\s*\(|write|truncate|"
                       r"ctypes|socket", re.I)
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
    real = canon(os.path.realpath(raw_form)) if os.path.exists(raw_form) else lex
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


def check_redirect(raw):
    """Deny a redirect target that names .claude/ or cannot be known statically."""
    if not raw:
        return
    if any(ch in raw for ch in "$`") or SUBST in raw:
        raise Deny("unexpanded", "redirect target with a variable or substitution is not inspected")
    if any(ch in raw for ch in "*?[]{}"):
        raise Deny("glob", "redirect target with a glob or brace is not statically known")
    target = _unescape(raw)
    if not target:
        raise Deny("path", "empty redirect target")
    # Check the raw spelling (Windows C:\...\.claude) and the unescaped one (.cl\aude).
    for v in (raw, target):
        if ".claude" in v.replace("\\", "/").casefold():
            raise Deny("claude-write", "redirect into .claude/ is denied")


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
        if c == "\\" and i + 1 < n:
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
                rd.append(_redir_target(text, j))
            cur.append(" " + REDIR_MARK + text[start:j] + " ")
            i = j
            continue
        if quote is None and c == "&" and i + 1 < n and text[i + 1] == ">":
            j = i + 1
            while j < n and text[j] == ">":
                j += 1
            rd.append(_redir_target(text, j))
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
                if k < n and text[k] in "\"'$":
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
    if any(a in ("-f", "--force") for a in args):
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


def git_rule(args, ctx):
    i = 0
    while i < len(args) and args[i].startswith("-"):
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
    if sub == "clean" and any(short_has(a, "f") or a == "--force" for a in rest):
        raise Deny("git-clean", "git clean with force is denied")
    if sub == "reset" and any(a in ("--hard", "--merge") for a in rest):
        raise Deny("git-reset", "git reset --hard/--merge is denied")
    if sub == "checkout" and (any(a in ("-f", "--force", "--", ".") for a in rest)):
        raise Deny("git-checkout", "forced or path checkout can discard changes")
    if sub == "restore":
        if "--staged" in rest and not any(a in ("--worktree", "-W") for a in rest):
            return
        raise Deny("git-restore", "git restore can discard working-tree changes")
    if sub == "switch" and any(a in ("-f", "--force", "--discard-changes") for a in rest):
        raise Deny("git-switch", "forced switch can discard changes")
    if sub == "branch" and any(a in ("-D", "-f", "--force") for a in rest):
        raise Deny("git-branch", "forced branch delete or move is denied")
    if sub == "stash" and rest[:1] in (["clear"], ["drop"]):
        raise Deny("git-stash", "stash clear/drop is denied")
    if sub == "push" and any(
            a in ("--force", "--force-with-lease", "--mirror", "--delete", "--prune")
            or a.startswith(("--force-with-lease=", "+", ":")) or short_has(a, "fd")
            for a in rest):
        raise Deny("git-push", "force or delete push is denied")
    if sub == "worktree" and rest:
        if rest[0] == "prune":
            raise Deny("git-worktree-prune", "git worktree prune is denied")
        if rest[0] == "remove":
            worktree_remove(rest[1:], ctx)


# X3b (Issue #640): program-naming options. An option whose value names a program that the tool starts is an
# execution form the guard does not analyse, so it is denied (UNKNOWN_EXECUTION). Matching is conservative: a long
# option may be abbreviated (getopt, clap), so any token that is a prefix of a program-naming long option is denied.
PROGRAM_LONG = {"sort": ("compress-program",), "rg": ("pre", "pre-glob", "hostname-bin"),
                "rsync": ("rsh", "rsync-path", "remote-option")}
# GNU and BSD variants of the same tools take the same program-naming options.
PROGRAM_ALIAS = {"gsort": "sort", "gtar": "tar", "bsdtar": "tar"}
PROGRAM_TOOLS = {"sort", "rg", "tar", "rsync", "ssh", "scp", "sftp"}
# Builtins that set a variable from their arguments (export $A$B, declare, read, ...).
SETTER_BUILTINS = ("export", "declare", "typeset", "local", "readonly", "read")
# Environment variables that name a program the tool starts (prefix assignments: VAR=x tool, env VAR=x tool).
PROGRAM_ENV = ("RSYNC_RSH", "SSH_ASKPASS", "TAR_OPTIONS", "SSH_SK_HELPER", "RIPGREP_CONFIG_PATH", "RSYNC_CONNECT_PROG")
# A short letter that names a program in its cluster: rsync -e PROG, scp/sftp -S PROG (tar is in ALLOWLIST).
PROGRAM_SHORT = {"rsync": "eM", "ssh": "F", "scp": "SF", "sftp": "SDFb"}
# ssh-family -o KEY=VALUE: a key that runs a local command.
SSH_O_TOOLS = ("ssh", "scp", "sftp")
SSH_LAUNCH_KEYS = ("include", "proxycommand", "localcommand", "permitlocalcommand", "knownhostscommand", "xauthlocation",
                   "pkcs11provider", "securitykeyprovider", "match")


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
        "full_long": ("pre", "pre-glob", "hostname-bin", "pretty", "hyperlink-format", "ignore-case",
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
        "short_flags": "FisvxUSawzLubnNHIclopqVPh",
        "short_args": "egfrtTABCmMEjd",
        "long_flags": ("pretty", "ignore-case", "invert-match", "line-regexp", "word-regexp", "case-sensitive",
                       "smart-case", "fixed-strings", "multiline", "multiline-dotall", "null-data", "pcre2", "text",
                       "search-zip", "count", "count-matches", "files-with-matches", "files-without-match", "json",
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
    # GNU tar 1.35 (tar --help). Program-naming options (--to-command, --use-compress-program, --info-script,
    # --new-volume-script, --rsh-command, --rmt-command, -I, -F) are in full_long but not in any allow list, so they
    # are DENY. --checkpoint-action names a program when its value starts with exec.
    "tar": {
        "full_long": ("absolute-names", "acls", "add-file", "after-date", "anchored", "append", "atime-preserve",
                      "auto-compress", "backup", "block-number", "blocking-factor", "bzip2", "catenate",
                      "check-device", "check-links", "checkpoint", "checkpoint-action", "clamp-mtime", "compare",
                      "compress", "concatenate", "confirmation", "create", "delay-directory-restore", "delete",
                      "dereference", "diff", "directory", "exclude", "exclude-backups", "exclude-caches",
                      "exclude-caches-all", "exclude-caches-under", "exclude-from", "exclude-ignore",
                      "exclude-ignore-recursive", "exclude-tag", "exclude-tag-all", "exclude-tag-under", "exclude-vcs",
                      "exclude-vcs-ignores", "extract", "file", "files-from", "force-local", "format", "full-time",
                      "get", "group", "group-map", "gunzip", "gzip", "hard-dereference", "help", "hole-detection",
                      "ignore-case", "ignore-command-error", "ignore-failed-read", "ignore-zeros", "incremental",
                      "index-file", "info-script", "interactive", "keep-directory-symlink", "keep-newer-files",
                      "keep-old-files", "label", "level", "list", "listed-incremental", "lzip", "lzma", "lzop", "mode",
                      "mtime", "multi-volume", "new-volume-script", "newer", "newer-mtime", "no-acls", "no-anchored",
                      "no-auto-compress", "no-check-device", "no-delay-directory-restore", "no-ignore-case",
                      "no-ignore-command-error", "no-null", "no-overwrite-dir", "no-quote-chars", "no-recursion",
                      "no-same-owner", "no-same-permissions", "no-seek", "no-selinux", "no-unquote",
                      "no-verbatim-files-from", "no-wildcards", "no-wildcards-match-slash", "no-xattrs", "null",
                      "numeric-owner", "occurrence", "old-archive", "one-file-system", "one-top-level", "overwrite",
                      "overwrite-dir", "owner", "owner-map", "pax-option", "portability", "posix", "preserve-order",
                      "preserve-permissions", "quote-chars", "quoting-style", "read-full-records", "record-size",
                      "recursion", "recursive-unlink", "remove-files", "restrict", "rmt-command", "rsh-command",
                      "same-order", "same-owner", "same-permissions", "seek", "selinux", "show-defaults",
                      "show-omitted-dirs", "show-snapshot-field-ranges", "show-stored-names",
                      "show-transformed-names", "skip-old-files", "sort", "sparse", "sparse-version",
                      "starting-file", "strip-components", "suffix", "tape-length", "test-label", "to-command",
                      "to-stdout", "totals", "touch", "transform", "unlink-first", "unquote", "update",
                      "use-compress-program", "usage", "utc", "verbatim-files-from", "verbose", "verify", "version",
                      "volno-file", "warning", "wildcards", "wildcards-match-slash", "xattrs", "xattrs-exclude",
                      "xattrs-include", "xform", "xz", "zstd", "uncompress", "ungzip"),
        "short_flags": "AcdrtuxnSGOmpskUWBiajJzZhlRvwoPM",
        "short_args": "bfCTXLHVKNg",
        "long_flags": ("absolute-names", "acls", "anchored", "append", "auto-compress", "block-number", "bzip2",
                       "catenate", "check-device", "check-links", "clamp-mtime", "compare", "compress", "concatenate",
                       "confirmation", "create", "delay-directory-restore", "delete", "dereference", "diff",
                       "exclude-backups", "exclude-caches", "exclude-caches-all", "exclude-caches-under", "exclude-vcs",
                       "exclude-vcs-ignores", "extract", "force-local", "full-time", "get", "gunzip", "gzip",
                       "hard-dereference", "help", "ignore-case", "ignore-command-error", "ignore-failed-read",
                       "ignore-zeros", "incremental", "interactive", "keep-directory-symlink", "keep-newer-files",
                       "keep-old-files", "list", "lzip", "lzma", "lzop", "multi-volume", "no-acls", "no-anchored",
                       "no-auto-compress", "no-check-device", "no-delay-directory-restore", "no-ignore-case",
                       "no-ignore-command-error", "no-null", "no-overwrite-dir", "no-recursion", "no-same-owner",
                       "no-same-permissions", "no-seek", "no-selinux", "no-unquote", "no-verbatim-files-from",
                       "no-wildcards", "no-wildcards-match-slash", "no-xattrs", "null", "numeric-owner", "old-archive",
                       "one-file-system", "overwrite", "overwrite-dir", "portability", "posix", "preserve-order",
                       "preserve-permissions", "read-full-records", "recursion", "recursive-unlink", "remove-files",
                       "restrict", "same-order", "same-owner", "same-permissions", "seek", "selinux", "show-defaults",
                       "show-omitted-dirs", "show-snapshot-field-ranges", "show-stored-names",
                       "show-transformed-names", "skip-old-files", "sparse", "test-label", "to-stdout", "touch",
                       "unlink-first", "unquote", "update", "usage", "utc", "verbatim-files-from", "verbose", "verify",
                       "version", "wildcards", "wildcards-match-slash", "xattrs", "xz", "zstd", "uncompress", "ungzip"),
        "long_args": ("add-file", "after-date", "blocking-factor", "directory", "exclude", "exclude-from",
                      "exclude-ignore", "exclude-ignore-recursive", "exclude-tag", "exclude-tag-all", "exclude-tag-under",
                      "file", "files-from", "format", "group", "group-map", "hole-detection", "label", "level",
                      "listed-incremental", "mode", "mtime", "newer", "newer-mtime", "owner", "owner-map", "pax-option",
                      "quote-chars", "no-quote-chars", "quoting-style", "record-size", "sort", "sparse-version",
                      "starting-file", "strip-components", "tape-length", "transform", "xform", "xattrs-exclude",
                      "xattrs-include", "warning", "checkpoint-action"),
        "long_optional_any": ("backup", "atime-preserve", "checkpoint", "one-top-level", "occurrence", "suffix", "totals"),
        "long_arg_deny_prefix": {"checkpoint-action": ("exec",)},
    },
}


def allowlist_rule(name, args):
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
                    if j == len(a) - 1:
                        i += 1  # the value is the next word
                    break  # the rest of the cluster is the value
                raise Deny("program-option", name + " short option is not in the allowlist: " + a)
            continue
        # a file name, or - for stdin: not an option


def program_option_rule(name, args):
    name = PROGRAM_ALIAS.get(name, name)
    if name not in PROGRAM_TOOLS:
        return
    longs = PROGRAM_LONG.get(name, ())
    shorts = PROGRAM_SHORT.get(name, "")
    ssh_o = name in SSH_O_TOOLS
    # The guard reads the words the shell passes on, not the shell's own expansion or escape. For these tools an
    # argument that contains an expansion or a backslash cannot be checked here, so it is denied (fail closed).
    # Windows backslash paths in these tools are denied too; this is a documented over-denial.
    # Brace expansion and globs are expanded by the shell before the tool runs, so they can make an option name or
    # value that is not in the word the guard reads. A glob in a file argument does not form an option, so only
    # dashed arguments (and tar's first cluster word) are checked for globs.
    for a in args:
        if "$" in a or "`" in a or SUBST in a or "\\" in a or "{" in a or "}" in a:
            raise Deny("program-option", name + " argument with an expansion or escape is not inspected")
        # A glob that starts the word (*-compress-program, [-]-to-command) can expand to a dashed file name, so it is an
        # option; a glob after a letter (names*.txt) cannot start with a dash.
        if any(c in a for c in "*?[") and (a.startswith("-") or a[:1] in "*?["):
            raise Deny("program-option", name + " option with a glob is not inspected")
    if name == "tar" and args and any(c in args[0] for c in "*?["):
        raise Deny("program-option", "tar first word with a glob is not inspected")
    if name == "tar" and args and re.fullmatch(r"[A-Za-z]+", args[0]):
        # Old-style tar: the first word is an option cluster (tar xf a.tar, tar Ixf sh a.tar), read as its dashed form.
        args = ["-" + args[0]] + list(args[1:])
    if name in ALLOWLIST:
        # The structural allowlist decides for this tool; the denylist below applies only to tools not yet migrated.
        allowlist_rule(name, args)
        return
    for i, a in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        # A backslash in an option name is a shell escape (--compress\-program is --compress-program).
        a = a.replace("\\", "")
        if a.startswith("--"):
            key, eq, val = a[2:].partition("=")
            if key and any(o.startswith(key) for o in longs):
                raise Deny("program-option", name + " option names a program: " + a)
            continue
        if a.startswith("-") and len(a) > 1:
            for j in range(1, len(a)):
                ch = a[j]
                if ch in shorts:
                    raise Deny("program-option", name + " option names a program: " + a)
                if ssh_o and ch == "o":
                    value = (a[j + 1:] or nxt).lower()
                    if any(c in value for c in "*?["):
                        raise Deny("program-option", name + " -o value with a glob is not inspected")
                    if any(k in value for k in SSH_LAUNCH_KEYS):
                        raise Deny("program-option", name + " -o names a local command: " + a)
                    break  # the rest of the cluster is the -o value


def find_rule(args, ps, ctx, depth):
    if "-delete" in args:
        raise Deny("find-delete", "find -delete is denied")
    for i, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < len(args) and args[j] not in (";", "+"):
                j += 1
            if j == i + 1:
                raise Deny("find", "find -exec without a command")
            analyze_argv(args[i + 1:j], ctx, ps, depth + 1)


def python_rule(args):
    for i, a in enumerate(args):
        if short_has(a, "c"):
            code = args[i + 1] if i + 1 < len(args) else ""
            if DANGER_PY.search(code):
                raise Deny("python-c", "python -c with a file or process operation")
            return


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


def claude_mention(args):
    """True if an argument names .claude/ (raw or unescaped) or carries a substitution."""
    return any(".claude" in a.replace("\\", "/").casefold()
               or ".claude" in _unescape(a).replace("\\", "/").casefold()
               or SUBST in a for a in args)


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


def unwrap(name, args):
    withargs = WRAPPER_ARGS.get(name, set())
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if name == "env" and ASSIGN.match(a):
            check_assign(a)
            i += 1
        elif a.startswith("-") and len(a) > 1:
            i += 2 if a in withargs else 1
        else:
            break
    return args[i:]


def analyze_argv(words, ctx, ps, depth):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    # A program-naming environment variable anywhere in the command (prefix, export, env -S, a shell string) is denied.
    if any("=" in w and ("{" in w or "}" in w or "$" in w) for w in words):
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
        name = posixpath.basename(w0.replace("\\", "/")).lower()
        # Windows drops trailing dots and spaces from a file name (rm. and rm  are rm), so they are dropped here too.
        # A leading = is zsh's expansion of a command name to its path (=sort); the same program is named (fail closed).
        name = name.lstrip("=").rstrip(". ") or name
        if name.endswith(".exe"):
            name = name[:-4].rstrip(". ") or name[:-4]
        if name == "timeout":
            # timeout's option grammar (long options with a separate value, non-integer durations) is not parsed, so
            # the command it runs is taken from its grammar (timeout_candidates), with conservative extra candidates.
            for cand in timeout_candidates(words[1:]):
                analyze_argv(cand, ctx.copy(), ps, depth + 1)
            return
        if handoff(name):
            words = unwrap(name, words[1:])
            continue
        break
    args = words[1:]
    program_option_rule(name, args)
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


def analyze_text(text, ctx, ps, depth):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    if ps and METHOD_CALL_RE.search(text):
        raise Deny("powershell", "PowerShell method call is not inspected")
    if ps and PS_PROC_LAUNCH_RE.search(text) and EXPR_SYNTAX_RE.search(text):
        raise Deny("launcher", "PowerShell process launcher with expression syntax is not inspected")
    segs, subs, redirs = split_segments(text, ps)
    for body in subs:
        analyze_text(body, ctx.copy(), ps, depth + 1)
    for seg, ops in zip(segs, redirs):
        if ops and inside_claude(ctx.cwd, ctx):
            raise Deny("claude-write", "redirect while cwd is inside .claude/ is denied")
        for target in ops:
            check_redirect(target)
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
