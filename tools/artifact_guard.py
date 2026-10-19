"""Fail-closed artifact guard (Issue #227): no linked executable or object reaches upload-artifact.

Every upload-artifact / upload-pages-artifact step in .github/workflows/*.yml|*.yaml and composite actions
(.github/actions/**/action.yml|yaml) is parsed as a whole block (key order does not matter),
its `path:` patterns are expanded against the checkout, and each matched entry is checked by content:
directories are walked, symlinks are refused, and ELF/Mach-O/PE/ar signatures and object/executable suffixes
are rejected. Unrecognised `path:` forms are refused rather than guessed. Names are not used, because legitimate
evidence JSON may contain "geargrafx". This is a content/signature tripwire only; it does not cover uploads made
from run: (a `gh release` mention is refused outright), and the verdict-upload exemption is unchanged. Distribution conditions
stay with docs/provenance.md and Issue #105. It does not detect BIOS-like content (.pce, .rom, .bin, .iso by content).

Two layers: `check_upload_artifacts()` is a static pre-build check of the checkout (run by make check); the binding
layer is `--post-build FILE STEP`, run in each workflow after the build and before upload-artifact, which expands the
named step's patterns against the real build output and fails when a pattern matches nothing, is unsafe or is refused.
"""
from pathlib import Path
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ".github/workflows"
ACTIONS = ".github/actions"
# Upload patterns that resolve outside the checkout (runner temp). Exact match only; anything else with
# a GitHub expression is refused so that a new expression pattern gets reviewed.
OUTSIDE_CHECKOUT = {"${{ runner.temp }}/verdict/cdread-verdict.json"}
OBJECT_SUFFIXES = {".o", ".obj", ".a", ".lib", ".so", ".dll", ".dylib", ".exe", ".elf", ".out"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".lz4", ".cab", ".jar", ".whl", ".z"}
ARCHIVE_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"BZh", b"\xfd7zXZ\x00",
                  b"Rar!", b"\x28\xb5\x2f\xfd")
SIGNATURES = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
              b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"!<arch>\n")
BLOCK_SCALARS = ("|", "|-", "|+")   # line-based; folded '>' forms and anchors/aliases are refused as unparseable
# Step key: optional quotes, optional spacing before the colon, any letter case. Only the upload-artifact action matches.
USES_RE = re.compile(r"""["']?uses["']?\s*:\s*["']?actions/upload-(?:pages-)?artifact@""", re.IGNORECASE)
# Any other publishing route (release assets, third-party upload actions, local actions and reusable workflows
# whose upload cannot be seen from here) is refused rather than guessed.
UPLOAD_RE = re.compile(r"""upload-(?:pages-)?artifact|\bgh\s+release\b|release-action|action-gh-release|release-asset"""
                       r"""|create-release|["']?uses["']?\s*:\s*["']?(?:\./|[^#\s]*\.github/workflows/)""", re.IGNORECASE)
# pathlib glob has no brace, extglob or alternation syntax: such a pattern would match nothing and hide its files.
UNSUPPORTED_PATTERN_CHARS = "{}()|"


def _indent(line):
    return len(line) - len(line.lstrip())


PATH_KEY_RE = re.compile(r"""["']?path["']?\s*:""")
NAME_RE = re.compile(r"""name:\s*["']?(.*?)["']?\s*$""")


def _step_patterns(lines, index, column):
    """Return (step name, path patterns) of the upload-artifact step whose `uses` key starts at lines[index][column]."""
    start = index
    while not (lines[start].lstrip().startswith("- ") and _indent(lines[start]) < column):
        start -= 1
        if start < 0:
            raise ValueError("upload-artifact step has no list item start")
    dash = _indent(lines[start])
    end = index + 1
    while end < len(lines) and not (lines[end].strip() and _indent(lines[end]) <= dash):
        end += 1
    step = lines[start:end]
    patterns = []
    path_keys = 0
    step_name = ""
    for offset, line in enumerate(step):
        body = line.lstrip()
        if body.startswith("- "):
            body = body[2:].lstrip()
        if body.startswith("name:") and not step_name:
            step_name = NAME_RE.match(body).group(1)
        if PATH_KEY_RE.match(body):   # quoted, spaced or duplicated path keys could hide a pattern from this parser
            path_keys += 1
            if not body.startswith("path:") or path_keys > 1:
                raise ValueError(f"ambiguous path key in upload-artifact step: {line.strip()!r}")
        if not body.startswith("path:"):
            continue
        key_column = len(line) - len(body)
        nested = []
        for rest in step[offset + 1:]:
            if rest.strip() and _indent(rest) <= key_column:
                break
            if rest.strip():
                nested.append(rest.strip())
        value = body[len("path:"):].strip().split(" #")[0].strip()
        if value in BLOCK_SCALARS:
            if not nested:
                raise ValueError(f"empty upload-artifact path block: {line.strip()!r}")
            patterns.extend(nested)
        elif value.startswith("["):
            if not value.endswith("]") or nested or '"' in value or "'" in value:
                raise ValueError(f"unparseable upload-artifact flow path list: {value!r}")
            items = [item.strip() for item in value[1:-1].split(",")]
            if not all(items):
                raise ValueError(f"empty item in upload-artifact flow path list: {value!r}")
            patterns.extend(items)
        elif not value or value[0] in "|>&*!{" or nested:
            raise ValueError(f"unparseable upload-artifact path form: {line.strip()!r}")
        else:
            patterns.append(_unquote(value))
    if not patterns:
        raise ValueError("upload-artifact step without a parseable path")
    return step_name, patterns


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def upload_patterns(text):
    """Return the path patterns of every upload-artifact step in one workflow file."""
    return [p for _, patterns in upload_steps(text) for p in patterns]


def upload_steps(text):
    """Return (step name, path patterns) for every upload-artifact step in one workflow file."""
    lines = text.splitlines()
    patterns = []
    for index, line in enumerate(lines):
        match = USES_RE.search(line)
        if match:
            patterns.append(_step_patterns(lines, index, match.start()))
        elif UPLOAD_RE.search(line):
            # Fail closed: a mention that is not a parsed upload step may still be an upload of unknown shape.
            raise ValueError(f"unsupported upload-artifact reference (not a parsed step): {line.strip()!r}")
    return patterns


def entry_problem(path):
    """Return why one file or symlink must not be uploaded, or None."""
    if path.is_symlink():
        return "symlink"
    if not path.is_file():
        return None
    if path.suffix.lower() in OBJECT_SUFFIXES:
        return f"object or executable suffix {path.suffix}"
    if path.suffix.lower() in ARCHIVE_SUFFIXES:
        return f"archive suffix {path.suffix}"
    with path.open("rb") as handle:
        head = handle.read(4096)
        if head.startswith(ARCHIVE_MAGICS) or head[257:262] == b"ustar":
            return "archive signature"
        if head.startswith(SIGNATURES):
            return "ELF/Mach-O/ar object or executable signature"
        # A file under 0x40 bytes cannot carry the e_lfanew field, so it is not treated as a PE candidate.
        if head.startswith(b"MZ") and len(head) >= 0x40:
            handle.seek(int.from_bytes(head[0x3C:0x40], "little"))
            window = handle.read(4)
            if len(window) < 4:
                return "MZ header whose PE header lies beyond end of file"
            if window == b"PE\0\0" or window[:2] in (b"NE", b"LE", b"LX"):   # PE, or 16-bit/VxD/OS2 executables
                return "PE/NE/LE executable signature"
    return None


def tree_problems(path):
    """Return (entry, reason) for every problem in a matched entry, descending into directories."""
    if path.is_symlink():
        return [(path, "symlink")]
    if not path.is_dir():
        reason = entry_problem(path)
        return [(path, reason)] if reason else []
    found = []

    def unreadable(error):   # os.walk skips unreadable directories silently; that would hide files, so refuse
        raise error

    for directory, dirnames, filenames in os.walk(path, onerror=unreadable):   # followlinks is off: symlinked dirs are listed, not entered
        for name in dirnames + filenames:
            child = Path(directory, name)
            reason = entry_problem(child)
            if reason:
                found.append((child, reason))
    return found


def _rel(path, base):
    try:
        return path.relative_to(base).as_posix()
    except ValueError:   # outside the checkout (runner temp)
        return str(path)


def pattern_problems(base, label, pattern, strict):
    """Return (matched count, problems) for one pattern. strict (post-build): a pattern with no match is a problem."""
    if pattern in OUTSIDE_CHECKOUT:
        if not strict:
            return 0, []
        temp = os.environ.get("RUNNER_TEMP")
        if not temp:
            return 0, [f"{label}: RUNNER_TEMP unset for {pattern!r}"]
        paths = [p for p in [Path(temp, pattern.split("/", 1)[1])] if p.exists()]
    elif (not pattern or "${{" in pattern or pattern.startswith(("/", "~")) or ".." in Path(pattern).parts
          or re.match(r"[A-Za-z]:", pattern)):
        return 0, [f"{label}: unresolvable or unsafe pattern {pattern!r}"]
    elif any(char in pattern for char in UNSUPPORTED_PATTERN_CHARS):
        return 0, [f"{label}: unsupported pattern {pattern!r} (brace, extglob or alternation)"]
    else:
        paths = list(Path(base).glob(pattern))
    problems = []
    if strict and not paths:
        problems.append(f"{label}: pattern {pattern!r} matched no built artifact")
    for path in paths:
        for entry, reason in tree_problems(path):
            problems.append(f"{label}: {_rel(entry, base)} ({reason})")
    return len(paths), problems


def _workflow_files(base):
    return sorted(f for ext in ("yml", "yaml") for f in (*(base / WORKFLOWS).glob(f"*.{ext}"),
                                                         *(base / ACTIONS).glob(f"**/action.{ext}")))


def check_upload_artifacts(root=ROOT):
    """Static pre-build layer: raise ValueError if any upload-artifact pattern matches a linked executable, object or symlink."""
    total = 0
    matched = 0
    problems = []
    base = Path(root)
    for workflow in _workflow_files(base):
        label = workflow.relative_to(base).as_posix()
        for pattern in upload_patterns(workflow.read_text(encoding="utf-8")):
            total += 1
            count, found = pattern_problems(base, label, pattern, False)
            matched += count
            problems += found
    if total == 0:
        raise ValueError("no upload-artifact path patterns found; guard cannot establish the upload set")
    if problems:
        raise ValueError("artifact guard: linked executable or unsafe entry would be uploaded: "
                         + "; ".join(problems))
    print(f"PASS upload-artifact guard: {total} patterns, {matched} local entries, no linked executable or object")


JOB_RE = re.compile(r"^  [\w-]+\s*:\s*$")
GUARD_CALL = "tools/artifact_guard.py --post-build"


def _live(line):
    return line.strip() and not line.lstrip().startswith("#")


def _step_ranges(lines, dash):
    """Line-based step ranges (start, end, job) for every '- ' item at indent `dash`."""
    starts = [i for i, l in enumerate(lines) if _live(l) and _indent(l) == dash and l.lstrip().startswith("- ")]
    result = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        for i in range(start + 1, end):   # a live line dedented below the dash ends the step
            if _live(lines[i]) and _indent(lines[i]) < dash:
                end = i
                break
        job = next((lines[j].strip() for j in range(start, -1, -1) if JOB_RE.match(lines[j])), None)
        result.append((start, end, job))
    return result


def _step_keys(lines, start, end, dash):
    """Top-level mapping lines of one step (the dash line's own content plus lines at dash+2)."""
    return [lines[start].lstrip()[2:].strip()] + [l.strip() for l in lines[start + 1:end] if _live(l) and _indent(l) == dash + 2]


def _run_command(lines, start, end, dash):
    """First command word-line of the step's `run:` value (single-line or block scalar), or None."""
    for i in range(start, end):
        l = lines[i].lstrip()
        if i == start:
            l = l[2:].lstrip() if l.startswith("- ") else l
        elif not (_live(lines[i]) and _indent(lines[i]) == dash + 2):
            continue
        m = re.match(r"run\s*:\s*(.*)$", l)
        if not m:
            continue
        value = m.group(1).strip()
        if re.match(r"[|>][+-]?\d*\s*(#.*)?$", value):
            return next((x.strip() for x in lines[i + 1:end] if _live(x)), None)
        return value or None
    return None


def _guard_is_bare(command, call, keys):
    """Exit status must gate the step: bare guard call, optionally `&& more`; default shell or `shell: bash` only."""
    m = re.match(r"(python3?\s+)?" + re.escape(call) + r"(\s|$)", command or "")
    rest = (command[m.end():] if m else "").replace("&&", " ")
    shell_ok = all(re.sub(r"""["']""", "", k.split(":", 1)[1]).split("#")[0].strip() == "bash"
                   for k in keys if re.match(r"""["']?shell["']?\s*:""", k))
    return bool(m) and shell_ok and not re.search(r"[|&;]|\\$", rest)


def guard_placement_violations(text, workflow):
    """Each upload step needs exactly one guard step in its job, before it: real `run:`, no `if:`, no `continue-on-error:`."""
    lines = text.splitlines()
    if any(re.match(r"\s*steps\s*:\s*\[", l) for l in lines):
        raise ValueError(f"{workflow}: flow-style steps unsupported")
    problems = []
    for uses, line in enumerate(lines):
        if not (_live(line) and USES_RE.search(line)):
            continue
        key_indent = _indent(line) + (2 if line.lstrip().startswith("- ") else 0)
        dash = next(j for j in range(uses, -1, -1) if _live(lines[j]) and lines[j].lstrip().startswith("- ")
                    and _indent(lines[j]) + 2 <= key_indent)
        dash_indent = _indent(lines[dash])
        steps = _step_ranges(lines, dash_indent)
        up = next(s for s in steps if s[0] <= uses < s[1])
        name = next((NAME_RE.search(k).group(1) for k in _step_keys(lines, up[0], up[1], dash_indent) if k.startswith("name:")), "")
        call = f'{GUARD_CALL} {workflow} "{name}"'
        label = f"{workflow}: {name!r}"
        guards = [s for s in steps if s[2] == up[2] and any(_live(l) and call in l for l in lines[s[0]:s[1]])]
        if len(guards) != 1:
            problems.append(f"{label}: expected exactly one live guard step in the same job, found {len(guards)}")
            continue
        start, end, _ = guards[0]
        if start >= up[0]:
            problems.append(f"{label}: guard step is not before the upload step")
        keys = _step_keys(lines, start, end, dash_indent)
        command = _run_command(lines, start, end, dash_indent)
        if not _guard_is_bare(command, call, keys):
            problems.append(f"{label}: guard call is not the bare command of the step's run: (no ||, |, &, ;, custom shell:)")
        for key in ("if", "continue-on-error"):
            if any(re.match(rf"""["']?{key}["']?\s*:""", k) for k in keys):
                problems.append(f"{label}: guard step has {key}:")
    return problems


def check_post_build(workflow, step, root=ROOT):
    """Binding layer: after the build, check the real output of the named upload step. Missing output fails closed."""
    base = Path(root)
    path = base / workflow
    found = [patterns for name, patterns in upload_steps(path.read_text(encoding="utf-8")) if name == step]
    if len(found) != 1:
        raise ValueError(f"{workflow}: expected exactly one upload-artifact step named {step!r}, found {len(found)}")
    matched = 0
    problems = []
    for pattern in found[0]:
        count, issues = pattern_problems(base, workflow, pattern, True)
        matched += count
        problems += issues
    if problems:
        raise ValueError("artifact guard (post-build): " + "; ".join(problems))
    print(f"PASS post-build artifact guard: {workflow} [{step}] {len(found[0])} patterns, {matched} entries")


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--post-build"]:
            if len(sys.argv) != 4:
                raise ValueError("usage: artifact_guard.py [--post-build WORKFLOW STEP_NAME]")
            check_post_build(sys.argv[2], sys.argv[3])
        else:
            check_upload_artifacts()
    except (ValueError, OSError) as error:
        raise SystemExit(f"artifact_guard: {error}") from error
