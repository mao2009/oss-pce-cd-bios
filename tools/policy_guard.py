"""Issue #544 (child of #511): the trusted-main policy check for run: approvals.

Run from the MAIN copy (--trusted), reading the pull-request copy (--candidate) as data only. Nothing from the candidate is
executed. The policy job (.github/workflows/policy-guard.yml) runs this file from the base SHA of the pull request.

Checks, all against the trusted copy:
  1. policy_files_match: the guard, the run allowlist, this file, the workflows, the actions and the policy workflow are
     byte-identical to the trusted copy. A pull request cannot loosen them.
  2. approval_problems: the trusted guard runs its enforced placement check on the candidate workflows with the TRUSTED
     allowlist. Any run: text that is not approved on main is refused, including a new send command.
  3. closure_problems: every file that a PRIVILEGED trusted job can execute (its run: texts, the Makefile targets they
     request, the Python imports and shell paths they reach, local actions) must be byte-identical to the trusted copy,
     and no module may be added beside an executed one. Unprivileged jobs are outside this closure (see privileged_job).
     Ceilings: runtime-built paths and external trees are not followed; a subprocess with a runtime-built program is refused
     unless listed in REVIEWED_EXTERNAL with its pin.
  4. check_record_problems: the GitHub check result for the head SHA. Used by the ruleset verification (see the child issue):
     a missing, renamed, skipped, reused (other head) or foreign-app result is refused.

Static matching cannot prevent network sends from scripts that approved steps call. That is out of guaranteed scope and
rests on least privilege, secret separation and trusted-publishing job separation.
"""
from pathlib import Path
import fnmatch
import hashlib
import importlib.util
import json
import re
import sys

POLICY_PATHS = (".github/workflows", ".github/actions", "tools/artifact_guard.py", "tools/artifact_guard_run_allowlist.json",
                "tools/policy_guard.py")
POLICY_WORKFLOW = ".github/workflows/policy-guard.yml"
REQUIRED_CHECK = "policy-guard"
SCRIPT_RE = re.compile(r"(?<![\w./-])((?:tools|tests|\.github)/[\w./-]+)")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(base, rel):
    """{relative path: sha256} of one file or of every file under one directory of base."""
    top = Path(base) / rel
    if top.is_file():
        return {rel: _sha(top)}
    if not top.is_dir():
        return {}
    return {path.relative_to(base).as_posix(): _sha(path) for path in sorted(top.rglob("*")) if path.is_file()}


def policy_files_match(candidate, trusted):
    """Problems for every policy path whose files differ from the trusted copy (added, removed or changed)."""
    problems = []
    for rel in POLICY_PATHS:
        mine, theirs = _tree(candidate, rel), _tree(trusted, rel)
        for name in sorted(set(mine) | set(theirs)):
            if mine.get(name) != theirs.get(name):
                problems.append(f"{name} differs from the trusted main copy: a policy change needs trusted-main approval "
                                "(Issue #544)")
    if _tree(trusted, POLICY_WORKFLOW) and not _tree(candidate, POLICY_WORKFLOW):
        problems.append(f"{POLICY_WORKFLOW} is missing: the policy check cannot be removed (Issue #544)")
    return problems


def _trusted_guard(trusted):
    spec = importlib.util.spec_from_file_location("trusted_artifact_guard", Path(trusted) / "tools" / "artifact_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approval_problems(candidate, trusted):
    """The trusted guard's enforced placement check on the candidate, with the TRUSTED approval table."""
    guard = _trusted_guard(trusted)
    try:
        guard._RUN_ALLOWLIST = guard.load_run_allowlist(trusted)
        guard.check_guard_placement(candidate)
    except ValueError as error:
        return [str(error)]
    finally:
        guard._RUN_ALLOWLIST = None
    return []


def privileged_job(doc, body):
    """A job is privileged when it can write, sign, deploy, reach a secret, or run on pull_request_target. A job with no
    permissions at either level is privileged (the repository default token may be write). A composite action has no job of
    its own: its steps are always privileged here (conservative)."""
    if not isinstance(body, dict):
        return True
    permissions = body.get("permissions", doc.get("permissions"))
    if permissions is None or permissions == "write-all" or "environment" in body or "secrets" in repr(body):
        return True
    if isinstance(permissions, dict) and any(value == "write" for value in permissions.values()):
        return True
    triggers = doc.get("on", doc.get(True))
    return isinstance(triggers, (str, list, dict)) and "pull_request_target" in triggers


def _trusted_steps(trusted):
    """(run text or None, local action path or None) for every step of every PRIVILEGED trusted job and composite action.
    Unprivileged jobs are outside the closure (see the docstring and docs/policy-bootstrap-owner-procedure.md)."""
    import yaml
    steps = []
    files = sorted((Path(trusted) / ".github").rglob("*.yml")) + sorted((Path(trusted) / ".github").rglob("*.yaml"))
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            continue
        groups = []
        if isinstance(doc.get("runs"), dict):
            groups.append(doc["runs"])   # composite action: always privileged here
        for body in (doc.get("jobs") or {}).values():
            if privileged_job(doc, body):
                groups.append(body)
        for body in groups:
            for step in (body.get("steps") or []) if isinstance(body, dict) else []:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                local = uses[2:] if isinstance(uses, str) and uses.startswith("./") else None
                run = step.get("run") if isinstance(step.get("run"), str) else None
                steps.append((run, local))
    return steps


# Closure analysis (Issue #544 review). Scope: the files that a PRIVILEGED trusted run: text can execute, followed through
# Makefile prerequisites and recipes, shell commands, Python imports and literal paths. Static matching is a ceiling, not a
# proof. Anything it cannot follow is refused rather than skipped: command substitution, eval/source/exec/., interpreter -c,
# a variable in an executed path, an expression ${{ }} in the run text, a runtime-built program (subprocess) unless listed
# in REVIEWED_EXTERNAL with its expected count, and dynamic loading. The table is itself a policy file.
PATH_TOKEN_RE = re.compile(r"""[^\s"'`;&|()<>=]+""")
SAFE_SUBST_RE = re.compile(r"\$\((?:command -v \S+|git rev-parse [\w-]+)\)")
GH_EXPR_RE = re.compile(r"\$\{\{.*?\}\}")
DYNAMIC_SHELL_RE = re.compile(r"`|\$\(")   # command substitution; eval/source/exec/. are checked as command words
DYNAMIC_HEADS = ("eval", "source", "exec", "xargs", ".", "builtin", "command", "env", "nohup", "time", "sudo", "stdbuf")
SHELL_KEYWORDS = ("if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}")
MAKE_CMDS = ("make", "$(MAKE)", "${MAKE}")
EXEC_EXT = (".py", ".sh", ".mk")
DYNAMIC_PY_CALLS = ("exec", "eval", "__import__", "import_module", "runpy", "run_path",
                    "spec_from_file_location", "exec_module", "load_module", "load_source")
RUNTIME_PROGRAM = "a runtime-built program (subprocess)"
# Files that run external trees, with the reason and the exact number of runtime-built subprocess calls reviewed.
REVIEWED_EXTERNAL = {
    "tools/setup.py": ("4 calls (curl download at line 77; make -C at lines 142 and 197; the build command at line 159) "
                       "on checksum-verified archives and source trees; the pins are in tools/environment.py DEPENDENCIES, "
                       "and the external Makefiles are not in this repository", 4),
}


def _strip_substitutions(text):
    return SAFE_SUBST_RE.sub(" ", GH_EXPR_RE.sub(" ", text))


def _words(segment):
    import shlex
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def _discover_options(args):
    """(start directory, pattern, top-level directory) of unittest discover, in the short and the long forms."""
    values = {"start": ".", "pattern": "test*.py", "top": None}
    names = {"-s": "start", "--start-directory": "start", "-p": "pattern", "--pattern": "pattern",
             "-t": "top", "--top-level-directory": "top"}
    index = 0
    while index < len(args):
        key, _, inline = args[index].partition("=")
        if key in names:
            if inline:
                values[names[key]] = inline
            elif index + 1 < len(args):
                values[names[key]] = args[index + 1]
                index += 1
            else:
                raise ValueError(f"unittest discover option {key} has no value (refused, Issue #544)")
        index += 1
    return values["start"], values["pattern"], values["top"]


def _discover_files(trusted, args):
    """Every test module and package __init__.py that discover imports under its start and top-level directories."""
    base = Path(trusted)
    start, pattern, top = _discover_options(args)
    found = set()
    for root in [start] + ([top] if top else []):
        directory = base / root
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file() and (fnmatch.fnmatch(path.name, pattern) or path.name == "__init__.py"):
                    found.add(path.relative_to(base).as_posix())
    return found


def _module_file(trusted, module):
    base = Path(trusted)
    rel = module.replace(".", "/")
    for candidate in (rel + ".py", rel + "/__init__.py"):
        if (base / candidate).is_file():
            return candidate
    return None


def _analyze_shell(text, trusted, where):
    """Shell-like text -> (files, make_targets, problems). Only executed positions are followed; other paths are data."""
    base = Path(trusted)
    problems, files, targets = [], set(), []
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))   # shebang and comments
    text = _strip_substitutions(text.replace("$(MAKE)", "make").replace("${MAKE}", "make"))
    if DYNAMIC_SHELL_RE.search(text):
        problems.append(f"{where}: command substitution cannot be analyzed; refused (Issue #544)")
    for segment in re.split(r"[\n;&|]+", text):
        words = _words(segment)
        if not words:
            continue
        while words and (re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]) or words[0] in SHELL_KEYWORDS):
            words = words[1:]
        if not words:
            continue
        head, args = words[0], words[1:]
        if head in DYNAMIC_HEADS:
            problems.append(f"{where}: command word {head!r} runs text the analysis cannot follow; refused (Issue #544)")
            continue
        if head.startswith("$") and head not in MAKE_CMDS:
            problems.append(f"{where}: command word {head!r} is a variable; the executed program cannot be analyzed (refused)")
            continue
        if head in MAKE_CMDS:
            if any(a.startswith(("-C", "-f", "-I", "--directory", "--file", "--makefile", "--include-dir")) for a in args):
                problems.append(f"{where}: make with -C, -f or -I selects another Makefile; refused (Issue #544)")
                continue
            if any("=" in a and not a.startswith("-") for a in args):
                problems.append(f"{where}: make with a command-line variable (for example PYTHON= or SHELL=) changes what "
                                "the recipes run; refused (Issue #544)")
                continue
            positional = [a for a in args if not a.startswith("-") and "=" not in a]
            targets += positional if positional else ["__default__"]
            continue
        interpreter = head in ("python", "python3", "bash", "sh", "zsh")
        if interpreter and any(a.startswith("-") and not a.startswith("--") and "c" in a[1:] for a in args):
            problems.append(f"{where}: {head} with -c runs inline code that cannot be analyzed; refused (Issue #544)")
            continue
        if head.startswith("./"):
            files.add(head[2:])
        elif "/" in head or (base / head).is_file():
            files.add(head)   # an executed path, with or without an extension
        if interpreter:
            if "-m" in args:
                index = args.index("-m")
                module = args[index + 1] if index + 1 < len(args) else ""
                if "unittest" in args and "discover" in args:
                    files |= _discover_files(trusted, args)
                elif _module_file(trusted, module):
                    files.add(_module_file(trusted, module))
                for word in args:   # the arguments of a -m command can still name an executed repository file
                    if "$" in word and "/" in word:
                        problems.append(f"{where}: argument {word!r} is a variable path; cannot be resolved (refused)")
                    elif PATH_TOKEN_RE.fullmatch(word) and (base / word).is_file():
                        files.add(word)
                continue
            positional = [a for a in args if not a.startswith("-")]
            if not positional:
                problems.append(f"{where}: {head} reads its program from stdin or an option; cannot be analyzed (refused)")
            else:
                if "$" in positional[0]:
                    problems.append(f"{where}: {head} runs a path with a variable ({positional[0]!r}); refused (Issue #544)")
                else:
                    files.add(positional[0])
        for word in args:   # any argument naming a repository file may be executed or sourced by this command: follow it
            if "$" in word and "/" in word:
                problems.append(f"{where}: argument {word!r} is a variable path; cannot be resolved (refused, Issue #544)")
            elif PATH_TOKEN_RE.fullmatch(word) and (base / word).is_file():
                files.add(word)
    return files, targets, problems


def _makefile(trusted):
    """(rules, variables, first target). rules[target] = (deps, recipe lines); variables[name] = value (simple, one level)."""
    text = (Path(trusted) / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    rules, variables, current, first = {}, {}, None, None
    for raw in text.splitlines():
        if raw.startswith("\t") and current:
            rules[current][1].append(raw[1:])
            continue
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        include = re.match(r"^-?s?include\s+(.+)$", line)
        if include:
            rules.setdefault("__include__", ([], []))[0].extend(include.group(1).split())
            current = None
            continue
        assign = re.match(r"^\s*([A-Za-z_]\w*)\s*(\?=|:=|::=|\+=|=)\s*(.*)$", line)
        if assign:
            name, op, value = assign.group(1), assign.group(2), assign.group(3).strip()
            variables[name] = (variables.get(name, "") + " " + value).strip() if op == "+=" else value
            current = None
            continue
        rule = re.match(r"^([^\t:=]+?)\s*:(?!=)\s*(.*)$", line)
        if rule:
            current = None
            deps_part, _, inline = rule.group(2).partition(";")
            for name in rule.group(1).split():
                if name == ".PHONY":
                    continue
                entry = rules.setdefault(name, ([], []))
                entry[0].extend(deps_part.split())
                if inline.strip():
                    entry[1].append(inline.strip())   # an inline recipe after ';'
                if first is None:
                    first = name
                current = name
    return rules, variables, first


def _analyze_makefile(trusted, requested, problems):
    """Targets requested by make -> (files, problems), following prerequisites, includes and recipe commands."""
    base = Path(trusted)
    text = (base / "Makefile").read_text(encoding="utf-8")
    rules, variables, first = _makefile(trusted)
    if re.search(r"^\s*(?:export\b|override\b|SHELL\b|\.SHELLFLAGS\b|MAKEFILES\b|MAKEFLAGS\b|\.DEFAULT_GOAL\b)",
                 text, re.M):
        problems.append("Makefile sets SHELL, MAKEFILES, MAKEFLAGS, export, override or .DEFAULT_GOAL; cannot be analyzed "
                        "(refused, Issue #544)")
        return {"Makefile"}, problems
    if re.search(r"!=|\$\((?:shell|eval|call|file|foreach)\b", text):
        problems.append("Makefile uses !=, $(shell), $(eval), $(call), $(file) or $(foreach); cannot be analyzed (refused)")
        return {"Makefile"}, problems
    files = {"Makefile"}
    for dep in rules.get("__include__", ([], []))[0]:
        if (base / dep).is_file():
            files.add(dep)
        elif "$" in dep:
            problems.append(f"Makefile include {dep!r} is a variable path; cannot be analyzed (refused, Issue #544)")
    pending, seen = [first if t == "__default__" else t for t in requested], set()
    if "__default__" in requested and first is None:
        problems.append("make with no target and no default goal in the trusted Makefile (refused, Issue #544)")
    while pending:
        target = pending.pop()
        if target == "__default__":
            target = first
        if target in seen:
            continue
        seen.add(target)
        if target not in rules:
            if (base / target).is_file():
                files.add(target)
            else:
                problems.append(f"make target {target!r} is not in the trusted Makefile (refused, Issue #544)")
            continue
        deps, recipe = rules[target]
        for dep in deps:
            if dep in rules:
                pending.append(dep)
            elif (base / dep).is_file() and dep.endswith(EXEC_EXT + ("Makefile",)):
                files.add(dep)
        for line in recipe:
            expanded = re.sub(r"\$\((\w+)\)", lambda m: variables.get(m.group(1), m.group(0)), line).lstrip("@-+ ")
            if re.search(r"\$\(\w+\)\S*/|/\S*\$\(\w+\)", expanded):
                problems.append(f"Makefile recipe for {target!r} builds a path from a variable (refused, Issue #544)")
            sub_files, sub_targets, sub_problems = _analyze_shell(expanded, trusted, f"Makefile:{target}")
            problems += sub_problems
            files |= sub_files
            pending += sub_targets
    return files, problems


def _python_facts(source):
    """(imported module names, dynamic-loading constructs, string literals) of one Python file."""
    import ast
    tree = ast.parse(source)
    names, dynamic, literals, relatives = set(), [], set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names if a.name != "*")
            if node.module == "os" and any(a.name in ("system", "popen") or a.name.startswith(("exec", "spawn"))
                                           for a in node.names):
                dynamic.append("from os import a program launcher")
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            relatives.append((node.level, node.module or "", [a.name for a in node.names if a.name != "*"]))
        elif isinstance(node, ast.Call):
            callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None) or ""
            owner = getattr(getattr(node.func, "value", None), "id", "")
            if callee in DYNAMIC_PY_CALLS and callee not in ("system", "popen"):
                dynamic.append(str(callee))
            if callee in ("system", "popen") and owner == "os":
                dynamic.append(f"os.{callee}")
            if callee.startswith(("execv", "execl", "spawn")) and owner == "os":
                dynamic.append(f"os.{callee}")
            if callee == "getattr" and any(isinstance(a, ast.Constant) and a.value in DYNAMIC_PY_CALLS for a in node.args):
                dynamic.append("getattr of a dynamic loader")
            for keyword in node.keywords:
                if keyword.arg == "shell" and getattr(keyword.value, "value", None) is True:
                    dynamic.append("shell=True")
            if callee in ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
                program = node.args[0] if node.args else next(
                    (k.value for k in node.keywords if k.arg == "args"), None)
                literal = isinstance(program, ast.List) and all(isinstance(e, ast.Constant) for e in program.elts)
                if program is None or not literal:
                    dynamic.append(RUNTIME_PROGRAM)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return names, dynamic, literals, relatives


def _add_module_files(base, files, directory, dotted):
    """Add the file each dotted name resolves to under directory, and the __init__.py of every parent package."""
    parts = [p for p in dotted.split(".") if p]
    prefix = f"{directory}/" if directory else ""
    for depth in range(1, len(parts) + 1):
        stem = "/".join(parts[:depth])
        for candidate in (f"{prefix}{stem}.py", f"{prefix}{stem}/__init__.py"):
            if (base / candidate).is_file():
                files.add(candidate)


def _python_closure(trusted, rel, problems):
    base = Path(trusted)
    files = {rel}
    source = (base / rel).read_text(encoding="utf-8")
    try:
        names, dynamic, literals, relatives = _python_facts(source)
    except SyntaxError as error:
        problems.append(f"{rel} does not parse ({error}); cannot be analyzed (refused, Issue #544)")
        return files
    runtime = dynamic.count(RUNTIME_PROGRAM)
    dynamic = [d for d in dynamic if d != RUNTIME_PROGRAM]
    for construct in dynamic:
        problems.append(f"{rel} uses {construct}; cannot be analyzed (refused, Issue #544)")
    reviewed = REVIEWED_EXTERNAL.get(rel)
    if reviewed is None and runtime:
        problems.append(f"{rel} runs {runtime} program(s) built at runtime; needs a REVIEWED_EXTERNAL entry with its pin")
    elif reviewed is not None and runtime != reviewed[1]:
        problems.append(f"{rel} has {runtime} runtime-built program call(s), the reviewed count is {reviewed[1]} "
                        "(refused until reviewed again, Issue #544)")
    # sys.path[0] is the script directory, and unittest adds the top directory: follow imports from these directories.
    search = [rel.rsplit("/", 1)[0] if "/" in rel else "", "tools", "tests/host", "tests/integration", "tests", ""]
    for name in names:
        for directory in search:
            _add_module_files(base, files, directory, name)
    package = rel.rsplit("/", 1)[0] if "/" in rel else ""
    for level, module, aliases in relatives:
        parts = package.split("/") if package else []
        if level - 1 > len(parts):
            problems.append(f"{rel} imports above its package; refused (Issue #544)")
            continue
        anchor = "/".join(parts[: len(parts) - (level - 1)])
        for dotted in ([module] if module else []) + [f"{module}.{a}" if module else a for a in aliases]:
            _add_module_files(base, files, anchor, dotted)
    for lit in literals:
        if lit.endswith(EXEC_EXT) and PATH_TOKEN_RE.fullmatch(lit) and (base / lit).is_file():
            files.add(lit)
    return files


# Owner decision (Issue #544): a privileged job runs ONLY trusted code. The candidate is data. The commands it may run are
# listed here; everything else is refused, so an indirect or unknown executor fails closed.
PRIVILEGED_HEADS = frozenset({"test", "echo", "exit", "true", "false", "set", "cp", "mkdir", "cmp", "gh", "tee"})
PRIVILEGED_INTERPRETERS = frozenset({"python3", "python", "bash", "sh"})
TRUSTED_CHECKOUTS = (".trusted-main/", ".trusted/")
PRIVILEGED_WRITERS = frozenset({"cp", "tee", "mkdir"})


def _is_trusted_path(path):
    """A path under a trusted checkout, with no parent-directory step and no unresolved variable."""
    return path.startswith(TRUSTED_CHECKOUTS) and ".." not in path.split("/") and "$" not in path


REPO_TOP_DIRS = ("tools", "tests", "scripts", ".github", "src", "platforms")


def _touches_trusted(arg):
    return arg.startswith(TRUSTED_CHECKOUTS) or ".trusted" in arg or ".." in arg.split("/") or "$" in arg


def _names_repository_code(arg):
    """A repository source or script, whether or not it exists: a writer may not copy it (a missing source is refused too)."""
    return arg.split("/")[0] in REPO_TOP_DIRS or arg.endswith(EXEC_EXT)


def _privileged_command_problems(run, where):
    """A privileged run: text may run only PRIVILEGED_HEADS, and an interpreter only on a trusted checkout path. Writers
    (cp, tee, mkdir) and gh may not name a trusted checkout, a parent step or an unresolved variable. Process substitution,
    redirections, leading assignments, -c and any -m form (attached or not) are refused. Anything unknown is refused."""
    problems = []
    if re.search(r"<\(|>\(", run):
        problems.append(f"{where}: process substitution in a privileged run is refused (Issue #544)")
    text = "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))
    text = _strip_substitutions(text)
    if DYNAMIC_SHELL_RE.search(text):
        problems.append(f"{where}: command substitution in a privileged run is refused (Issue #544)")
    for segment in re.split(r"[\n;&|]+", text):
        words = _words(segment)
        if words is None:
            problems.append(f"{where}: a privileged command cannot be parsed; refused (Issue #544)")
            continue
        while words and words[0] in SHELL_KEYWORDS:
            words = words[1:]
        if not words:
            continue
        if re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
            problems.append(f"{where}: a leading assignment ({words[0].split('=')[0]}=) in a privileged run is refused "
                            "(it can change the search path, Issue #544)")
            continue
        if any(">" in word or "<" in word for word in words):
            problems.append(f"{where}: a redirection in a privileged run is refused (it can write a trusted path, Issue #544)")
            continue
        head, args = words[0], words[1:]
        if head in PRIVILEGED_INTERPRETERS:
            positional = [a for a in args if not a.startswith("-")]
            inline = any(a == "-c" or a.startswith("-m") or (a.startswith("-") and not a.startswith("--") and "c" in a[1:])
                         for a in args)
            if inline or not positional or not _is_trusted_path(positional[0]):
                problems.append(f"{where}: {head} may run only a trusted checkout script in a privileged job; "
                                f"{' '.join(words)[:60]!r} refused (Issue #544)")
        elif head in PRIVILEGED_WRITERS:
            if any(_touches_trusted(a) for a in args):
                problems.append(f"{where}: {head} names a trusted checkout, a parent step or a variable; refused (Issue #544)")
            elif any(_names_repository_code(a) for a in args):
                problems.append(f"{where}: {head} names repository code (existing or missing); refused (Issue #544)")
        elif head == "gh":
            if not (args[:2] == ["attestation", "verify"] or args[:1] == ["api"]) or any(_touches_trusted(a) for a in args):
                problems.append(f"{where}: this gh command may write or is not listed; refused (Issue #544)")
        elif head not in PRIVILEGED_HEADS:
            problems.append(f"{where}: command {head!r} is not allowed in a privileged job; refused (Issue #544)")
    return problems


def closure_of(trusted):
    """(files, directories, problems): what a privileged run can execute. Owner decision: the privileged set must run only
    trusted checkout code, so any repository file, make target or local action here is itself a problem."""
    base = Path(trusted)
    problems, files, requested = [], set(), []
    for run, local in _trusted_steps(trusted):
        if local:
            problems.append(f"local action {local!r} in a privileged job runs repository code; refused (Issue #544)")
        if run is None:
            continue
        problems += _privileged_command_problems(run, "run:")
        if "${{" in run:
            problems.append("a ${{ }} expression in a privileged run: text is refused (its value is runtime data, Issue #544)")
            continue
        step_files, targets, step_problems = _analyze_shell(run, trusted, "run:")
        problems += step_problems
        requested += targets
        for f in step_files:
            if (base / f).is_file():
                files.add(f)
            elif _is_trusted_path(f):
                continue   # the trusted checkout of the guard and the policy job (pinned by the checkout ref, main)
            else:
                problems.append(f"executed file {f!r} is not in the trusted tree or cannot be resolved (refused, Issue #544)")
    if requested:
        make_files, problems = _analyze_makefile(trusted, requested, problems)
        files |= make_files
    # Follow Python and shell files reached from the closure: their imports, literal paths and dynamic constructs.
    pending, done = sorted(files), set()
    while pending:
        rel = pending.pop()
        if rel in done or not (base / rel).is_file():
            continue
        done.add(rel)
        if rel.endswith(".py"):
            new = _python_closure(trusted, rel, problems)
        elif rel.endswith(".sh"):
            new, _, sub = _analyze_shell((base / rel).read_text(encoding="utf-8"), trusted, rel)
            problems += sub
        else:
            new = set()
        files |= new
        pending += sorted(new - done)
    directories = {Path(f).parent.as_posix() for f in files if f.endswith(".py")}
    return files, directories, problems


MODULE_SUFFIXES = (".py", ".pyc", ".pyo", ".so", ".pyd")


def _module_entries(root):
    """Names a Python import from this directory could resolve to: sources, bytecode, extension modules and packages."""
    if not root.is_dir():
        return set()
    entries = set()
    for p in root.iterdir():
        if p.is_file() and p.suffix in MODULE_SUFFIXES:
            entries.add(p.name)
        elif p.is_dir() and (p.name == "__pycache__" or any(f.suffix in MODULE_SUFFIXES for f in p.rglob("*"))):
            entries.add(p.name)
    return entries


def closure_problems(candidate, trusted):
    """Every file the privileged closure can execute must equal the trusted copy; no unanalyzable construct; no module or
    package added beside an executed one (sys.path[0] is the script directory; '.' is checked for python -m)."""
    files, directories, problems = closure_of(trusted)
    for name in ("GNUmakefile", "makefile"):   # exact names: a case-insensitive file system must not match Makefile
        if name in {p.name for p in Path(candidate).iterdir()} or name in {p.name for p in Path(trusted).iterdir()}:
            problems.append(f"{name} would be read by make before Makefile; refused (Issue #544)")
    for rel in sorted(files):
        mine, theirs = Path(candidate) / rel, Path(trusted) / rel
        if not mine.is_file() or not theirs.is_file() or _sha(mine) != _sha(theirs):
            problems.append(f"{rel} is executed by a privileged run: and differs from the trusted copy or is missing "
                            "(refused; Issue #544)")
    for directory in sorted(directories | {"."}):
        for name in sorted(_module_entries(Path(candidate) / directory) - _module_entries(Path(trusted) / directory)):
            problems.append(f"{directory}/{name} is added beside an executed module and can shadow an import "
                            "(refused; Issue #544)")
    return problems


def script_hash_problems(candidate, trusted):
    """Compatibility name: the privileged closure (see closure_problems)."""
    return closure_problems(candidate, trusted)


def check_record_problems(record, head_sha, expected_app_id):
    """The required check result, as the ruleset must see it: the same name, the same head SHA, the expected app, success."""
    if not record:
        return ["no policy-guard result for this head: missing, removed or skipped (refused; Issue #544)"]
    if record.get("name") != REQUIRED_CHECK:
        return [f"check {record.get('name')!r} is not {REQUIRED_CHECK!r} (refused; Issue #544)"]
    if record.get("head_sha") != head_sha:
        return ["the result is for another head SHA (reused result, refused; Issue #544)"]
    if record.get("app_id") != expected_app_id:
        return ["the result comes from another app (fake job, refused; Issue #544)"]
    if record.get("conclusion") != "success":
        return [f"conclusion is {record.get('conclusion')!r}, not success (refused; Issue #544)"]
    return []


def check_run_records(payload, head_sha, expected_app_id):
    """The check-runs listing for the head SHA (GitHub REST: commits/{sha}/check-runs). Every run named policy-guard must be
    for this head, from the expected app, completed with success; at least one must exist (Issue #544)."""
    runs = [run for run in (payload or {}).get("check_runs", []) if run.get("name") == REQUIRED_CHECK]
    if not runs:
        return ["no policy-guard check run for this head: missing, removed or skipped (refused; Issue #544)"]
    problems = []
    for run in runs:
        record = {"name": run.get("name"), "head_sha": run.get("head_sha"), "app_id": (run.get("app") or {}).get("id"),
                  "conclusion": run.get("conclusion") if run.get("status") == "completed" else None}
        problems += check_record_problems(record, head_sha, expected_app_id)
    return problems


def check_candidate(candidate, trusted):
    """All candidate checks against the trusted copy. Returns the list of problems (empty when the candidate passes)."""
    return policy_files_match(candidate, trusted) + approval_problems(candidate, trusted) + closure_problems(candidate, trusted)


def main(argv):
    if argv[:1] == ["--check-record"]:
        if len(argv) != 6 or argv[2] != "--head" or argv[4] != "--app-id":
            raise ValueError("usage: policy_guard.py --check-record RUNS.json --head SHA --app-id ID")
        payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        problems = check_run_records(payload, argv[3], int(argv[5]))
        if problems:
            raise ValueError("policy guard record: " + "; ".join(problems))
        print("PASS policy guard record: policy-guard completed with success for this head and app")
        return
    if len(argv) != 4 or argv[0] != "--candidate" or argv[2] != "--trusted":
        raise ValueError("usage: policy_guard.py --candidate DIR --trusted DIR")
    problems = check_candidate(Path(argv[1]), Path(argv[3]))
    if problems:
        raise ValueError("policy guard: " + "; ".join(problems))
    print("PASS policy guard: candidate matches the trusted main policy, approvals and called scripts")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as error:
        raise SystemExit(str(error)) from error
