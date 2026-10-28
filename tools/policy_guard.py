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
import hashlib
import importlib.util
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
    """A job is privileged when it can write, sign, deploy or run on pull_request_target (secrets are in its reach).
    A composite action has no job of its own: its steps are always treated as privileged here (conservative)."""
    permissions = body.get("permissions", doc.get("permissions")) if isinstance(body, dict) else None
    if permissions == "write-all" or "environment" in (body or {}):
        return True
    if isinstance(permissions, dict) and any(value == "write" for value in permissions.values()):
        return True
    triggers = doc.get("on", doc.get(True))
    return isinstance(triggers, (str, list, dict)) and "pull_request_target" in triggers


def _trusted_steps(trusted):
    """(run text or None, local action path or None) for every step of every PRIVILEGED trusted job and composite action.
    Unprivileged jobs (no write permission, no environment, no pull_request_target) are not in the closure: they hold no
    secret and no signing right, and their output reaches the publish path only through the trusted guard (see the docstring)."""
    import yaml
    steps = []
    for path in sorted((Path(trusted) / ".github").rglob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            continue
        groups = []
        if isinstance(doc.get("runs"), dict):
            groups.append(doc["runs"])   # composite action: always privileged here
        for body in (doc.get("jobs") or {}).values():
            if isinstance(body, dict) and privileged_job(doc, body):
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


# Closure analysis (Issue #544 review). Scope: the files that a trusted run: text can execute, followed through Makefile
# prerequisites and recipes, shell commands, Python imports and literal paths. Static matching is a ceiling, not a proof:
# a path assembled at runtime from non-literal parts, or a program that runs an external tree, is not followed. Such a
# construct is refused unless it is listed in REVIEWED_EXTERNAL with its reason (that table is itself a policy file).
PATH_TOKEN_RE = re.compile(r"""[^\s"'`;&|()<>=]+""")
SAFE_SUBST_RE = re.compile(r"\$\((?:command -v \S+|git rev-parse [\w-]+)\)")
GH_EXPR_RE = re.compile(r"\$\{\{.*?\}\}")
DYNAMIC_SHELL_RE = re.compile(r"`|\$\(")   # command substitution; eval/source/exec/. are checked as command words
DYNAMIC_HEADS = ("eval", "source", "exec", "xargs", ".")
MAKE_DYNAMIC_RE = re.compile(r"\$\((?:shell|eval|call|file)\b")
MAKE_CMDS = ("make", "$(MAKE)", "${MAKE}")
EXEC_EXT = (".py", ".sh", ".mk")
# External trees executed by a trusted file, with the reason. The external tree is pinned by a checksum in the named file.
REVIEWED_EXTERNAL = {
    "tools/setup.py": "make -C on the checksum-verified Geargrafx source tree (pin: tools/environment.py DEPENDENCIES); "
                      "the external Makefile is not in this repository",
}


def _strip_substitutions(text):
    return SAFE_SUBST_RE.sub(" ", GH_EXPR_RE.sub(" ", text))


def _words(segment):
    import shlex
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def _discover_files(trusted, args):
    base = Path(trusted)
    start = args[args.index("-s") + 1] if "-s" in args else "."
    pattern = args[args.index("-p") + 1] if "-p" in args else "test*.py"
    found = set()
    top = base / start
    if top.is_dir():
        for path in top.rglob(pattern):
            if path.is_file():
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
    problems, files, targets = [], set(), []
    text = _strip_substitutions(text.replace("$(MAKE)", "make").replace("${MAKE}", "make"))
    if DYNAMIC_SHELL_RE.search(text):
        problems.append(f"{where}: command substitution cannot be analyzed; refused (Issue #544)")
    for segment in re.split(r"[\n;&|]+", text):
        words = _words(segment)
        if not words:
            continue
        while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):
            words = words[1:]
        if not words:
            continue
        head, args = words[0], words[1:]
        if head in DYNAMIC_HEADS:
            problems.append(f"{where}: command word {head!r} runs text the analysis cannot follow; refused (Issue #544)")
            continue
        if head in ("python", "python3", "bash", "sh", "zsh") and "-c" in args:
            problems.append(f"{where}: {head} -c runs inline code that cannot be analyzed; refused (Issue #544)")
            continue
        if head.startswith("$") and head not in MAKE_CMDS:
            problems.append(f"{where}: command word {head!r} is a variable; the executed program cannot be analyzed "
                            "(refused, Issue #544)")
            continue
        if head in MAKE_CMDS:
            targets += [w for w in args if re.fullmatch(r"[\w.\-/]+", w) and not w.startswith("-")]
            continue
        if head in ("python", "python3", "bash", "sh", "zsh"):
            if "-m" in args:
                index = args.index("-m")
                module = args[index + 1] if index + 1 < len(args) else ""
                if "unittest" in args and "discover" in args:
                    files |= _discover_files(trusted, args)
                elif _module_file(trusted, module):
                    files.add(_module_file(trusted, module))
                continue
            positional = [a for a in args if not a.startswith("-")]
            if positional:
                files.add(positional[0])
        elif head.startswith("./"):
            files.add(head[2:])
        for word in args:   # a repository script named in the arguments (for example: python3 "$x" tools/y.py)
            if word.endswith(EXEC_EXT) and PATH_TOKEN_RE.fullmatch(word):
                files.add(word)
    return files, targets, problems


def _makefile(trusted):
    """(rules, variables): rules[target] = (deps, recipe lines); variables[name] = value (simple one-level)."""
    text = (Path(trusted) / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    rules, variables, current = {}, {}, None
    for raw in text.splitlines():
        if raw.startswith("\t") and current:
            rules[current][1].append(raw[1:])
            continue
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        assign = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:\?=|:=|::=|\+=|=)\s*(.*)$", line)
        if assign:
            variables[assign.group(1)] = assign.group(2).strip()
            current = None
            continue
        include = re.match(r"^-?include\s+(.+)$", line)
        if include:
            rules.setdefault("__include__", ([], []))[0].extend(include.group(1).split())
            current = None
            continue
        rule = re.match(r"^([^\t:=]+?)\s*:(?!=)\s*(.*)$", line)
        if rule:
            current = None
            for name in rule.group(1).split():
                if name == ".PHONY":
                    continue
                entry = rules.setdefault(name, ([], []))
                entry[0].extend(rule.group(2).split())
                current = name
    return rules, variables


def _analyze_makefile(trusted, requested, problems):
    """Targets requested by make -> (files, problems), following prerequisites and recipe commands."""
    base = Path(trusted)
    rules, variables = _makefile(trusted)
    files = {"Makefile"}
    for dep in rules.get("__include__", ([], []))[0]:
        if (base / dep).is_file():
            files.add(dep)
        elif "$" in dep:
            problems.append(f"Makefile include {dep!r} is a variable path; cannot be analyzed (refused, Issue #544)")
    pending, seen = list(requested), set()
    while pending:
        target = pending.pop()
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
            if MAKE_DYNAMIC_RE.search(line):
                problems.append(f"Makefile recipe for {target!r} uses $(shell/eval/call); cannot be analyzed (refused)")
                continue
            expanded = re.sub(r"\$\((\w+)\)", lambda m: variables.get(m.group(1), m.group(0)), line)
            if re.search(r"\$\([^)]*\)\S*/", expanded):
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
    names, dynamic, literals = set(), [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Call):
            callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if callee in ("exec", "eval", "__import__", "import_module", "runpy"):
                dynamic.append(str(callee))
            for keyword in node.keywords:
                if keyword.arg == "shell" and getattr(keyword.value, "value", None) is True:
                    dynamic.append("shell=True")
            if callee in ("run", "Popen", "call", "check_call", "check_output") and node.args:
                first = node.args[0]   # a program built at runtime (anything but a literal list of literal strings)
                literal = isinstance(first, ast.List) and all(isinstance(e, ast.Constant) for e in first.elts)
                if not literal:
                    dynamic.append("a runtime-built program (subprocess)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return names, dynamic, literals


def _python_closure(trusted, rel, problems):
    base = Path(trusted)
    files = {rel}
    source = (base / rel).read_text(encoding="utf-8")
    try:
        names, dynamic, literals = _python_facts(source)
    except SyntaxError as error:
        problems.append(f"{rel} does not parse ({error}); cannot be analyzed (refused, Issue #544)")
        return files
    if rel in REVIEWED_EXTERNAL:
        dynamic = [d for d in dynamic if d != "a runtime-built program (subprocess)"]   # reviewed: the pin is in the entry
    for construct in dynamic:
        problems.append(f"{rel} uses {construct}; cannot be analyzed (refused unless REVIEWED_EXTERNAL, Issue #544)")
    if rel not in REVIEWED_EXTERNAL and re.search(r"subprocess\.run\(\s*command\b", source):
        problems.append(f"{rel} runs a program built at runtime; needs a REVIEWED_EXTERNAL entry with its pin (Issue #544)")
    # sys.path[0] is the script directory, and unittest adds the top directory: follow imports from these directories.
    search = [rel.rsplit("/", 1)[0] if "/" in rel else "", "tools", "tests/host", "tests/integration", "tests", ""]
    for name in names:
        dotted = name.replace(".", "/")
        for directory in search:
            prefix = f"{directory}/" if directory else ""
            for candidate in (f"{prefix}{dotted}.py", f"{prefix}{dotted}/__init__.py"):
                if (base / candidate).is_file():
                    files.add(candidate)
    for lit in literals:
        if lit.endswith(EXEC_EXT) and PATH_TOKEN_RE.fullmatch(lit) and (base / lit).is_file():
            files.add(lit)
    return files


def closure_of(trusted):
    """(files, directories, problems): what the trusted run: texts and local actions can execute."""
    base = Path(trusted)
    problems, files, requested, makes = [], set(), [], False
    for run, local in _trusted_steps(trusted):
        if local:
            action = base / local
            if action.is_dir():
                files |= {path.relative_to(base).as_posix() for path in action.rglob("*") if path.is_file()}
            else:
                problems.append(f"local action {local!r} is missing in the trusted tree (refused, Issue #544)")
        if run is None:
            continue
        step_files, targets, step_problems = _analyze_shell(run, trusted, "run:")
        problems += step_problems
        requested += targets
        makes = makes or bool(targets) or bool(re.search(r"(?:^|[\s;&|(])(?:make|\$\(MAKE\))\s", run))
        for f in step_files:
            if (base / f).is_file():
                files.add(f)
            elif f.endswith(EXEC_EXT) and not f.startswith((".trusted", ".intake", "build/", "$")):
                problems.append(f"executed file {f!r} is not in the trusted tree (refused, Issue #544)")
    if makes:
        make_files, problems_make = _analyze_makefile(trusted, requested, problems)
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


def closure_problems(candidate, trusted):
    """Every file the trusted closure can execute must equal the trusted copy; no unanalyzable construct; no module added to
    a directory that a closure module is run or imported from (sys.path[0] is the script directory)."""
    files, directories, problems = closure_of(trusted)
    for rel in sorted(files):
        mine, theirs = Path(candidate) / rel, Path(trusted) / rel
        if not mine.is_file() or not theirs.is_file() or _sha(mine) != _sha(theirs):
            problems.append(f"{rel} is executed by an approved run: and differs from the trusted copy or is missing "
                            "(refused; Issue #544)")
    for directory in sorted(directories):
        trusted_names = {p.name for p in (Path(trusted) / directory).glob("*.py")}
        for path in (Path(candidate) / directory).glob("*.py"):
            if path.name not in trusted_names:
                problems.append(f"{directory}/{path.name} is added beside an executed module and can shadow an import "
                                "(refused; Issue #544)")
    return problems


def script_hash_problems(candidate, trusted):
    """Compatibility name: the approved closure (see closure_problems)."""
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


def check_candidate(candidate, trusted):
    """All candidate checks against the trusted copy. Returns the list of problems (empty when the candidate passes)."""
    return policy_files_match(candidate, trusted) + approval_problems(candidate, trusted) + closure_problems(candidate, trusted)


def main(argv):
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
