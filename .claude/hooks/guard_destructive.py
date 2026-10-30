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
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
MNT = re.compile(r"^/mnt/([A-Za-z])(/.*)?$")
MSYS = re.compile(r"^/([A-Za-z])(/.*)?$")  # Git Bash drive form: /c/Users/... is C:/Users/...
# Generic roots only. The home directory is derived at run time (home_roots), never written into the public tree.
PROTECTED_BASE = ["/", "/home", "/mnt", "/mnt/c", "/mnt/c/users"]


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


def canon(path):
    """Canonical absolute form: forward slashes; /mnt/c, /c (MSYS) and c:/ all -> C:/."""
    p = path.replace("\\", "/")
    m = MNT.match(p) or MSYS.match(p)
    if m:
        p = m.group(1).upper() + ":" + (m.group(2) or "/")
    drive = ""
    m = re.match(r"^([A-Za-z]):", p)
    if m:
        drive = m.group(1).upper() + ":"
        p = p[2:] or "/"
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
    """Home directories to protect, derived from the environment at run time.

    Sources: the process home (expanduser), HOME and USERPROFILE. A value that is unset or still
    unexpanded ("~") is ignored. A relative value is refused. Each value is realpath'd and canonicalised, so
    symlinked, Windows, /mnt/<drive> and MSYS forms of one home compare equal. An empty result makes the
    caller fail closed.
    """
    raw = [os.path.expanduser("~"), os.environ.get("HOME", ""), os.environ.get("USERPROFILE", "")]
    roots = []
    for value in raw:
        if value and value != "~":
            roots.append(_absolute_root(value))
    return sorted(set(roots))


def local_roots(root):
    """Extra protected roots kept outside the public tree.

    Sources: the environment variable OSS_PCE_GUARD_PROTECTED (os.pathsep separated) and the untracked file
    .claude/guard-local.json with {"protected": [absolute paths]}. A missing file adds nothing; an unreadable,
    malformed or relative entry is refused (fail closed).
    """
    values = []
    env = os.environ.get("OSS_PCE_GUARD_PROTECTED", "")
    values += [item.strip() for item in env.split(os.pathsep) if item.strip()]
    path = os.path.join(root, ".claude", "guard-local.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except Exception:
        raise Deny("config", "local guard configuration is unreadable")
    if not isinstance(data, dict):
        raise Deny("config", "local guard configuration is malformed")
    listed = data.get("protected", [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise Deny("config", "local guard configuration is malformed")
    values += listed
    return [_absolute_root(item) for item in values if item]


def inside(k, base):
    return k.startswith(base.rstrip("/") + "/")


def is_protected(k, ctx):
    """True if k is a protected path, or an ancestor of one."""
    for p in ctx.protected:
        if p == k or p.startswith(k.rstrip("/") + "/"):
            return True
    return False


def resolve(raw, ctx):
    """Return (lexical canonical, real canonical) for a path argument."""
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
    real = canon(os.path.realpath(lex)) if os.path.exists(lex) else lex
    return lex, real


def strip_quotes(tok):
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        return tok[1:-1]
    return tok


def words_of(seg, ps):
    try:
        if ps:
            return [strip_quotes(t) for t in shlex.split(seg, posix=False)]
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
        if c == ">":
            rd.append(_redir_target(text, i + 1))
            cur.append(c)
            i += 1
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
        if c in ";&|()\n":
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
    lex, real = resolve(targets[0], ctx)
    rk = real.casefold()
    if os.path.islink(lex):
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
    if name == "timeout" and i < len(args):
        i += 1
    return args[i:]


def analyze_argv(words, ctx, ps, depth):
    if depth > MAX_DEPTH:
        raise Deny("depth", "command nesting too deep")
    while True:
        while words and (words[0] in KEYWORDS or ASSIGN.match(words[0])):
            check_assign(words[0])
            words = words[1:]
        if not words:
            return
        w0 = words[0]
        if SUBST in w0:
            raise Deny("command-word", "command substitution in command position")
        if w0 not in ("[", "[[") and any(ch in w0 for ch in "$*?[]{}`~"):
            raise Deny("command-word", "unexpanded word in command position")
        # A backslash before a plain character only quotes it: \rm is rm.
        w0 = re.sub(r"^\\+(?=[^\\/])", "", w0)
        if "\\" in w0 and not re.match(r"^(?:[A-Za-z]:)?[\\/]", w0):
            raise Deny("command-word", "backslash in command name")
        name = posixpath.basename(w0.replace("\\", "/")).lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name in WRAPPERS:
            words = unwrap(name, words[1:])
            continue
        break
    args = words[1:]
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
