"""Fail-closed artifact guard (Issue #227): no linked executable or object reaches a publication step.

The workflows are split into two kinds of job, on separate ephemeral GitHub-hosted runners. A build job runs the
pull-request code and hands its output over as an intermediate artifact (name intermediate-*); it runs no guard. A
guard job runs no pull-request code: only its first two steps may be checkouts (the pull-request workflow checkout,
which is pinned so it cannot be pointed at main or another repository, then the trusted main checkout into
.trusted-main), no later step may check out, a job that publishes from its own checkout may not download at all, the
Pages actions may not carry extra inputs, and every other non-`uses:` step must be one of the trusted run steps (python
resolve, placement check, stage, verify). It downloads the intermediate artifact of a job it `needs:` into .intake/,
stages what it will publish, and only then uploads. A step's `uses:`/`with:` are read from its own top-level keys only,
so a `uses:` or `with:` line hidden in a block scalar or behind an alias cannot stand in for a trusted step.

Every upload-artifact / upload-pages-artifact step in .github/workflows/*.yml|*.yaml and composite actions
(.github/actions/**/action.yml|yaml) is parsed as a whole block (key order does not matter). Its step name is the
dash-level `name:` only. Its `path:` patterns must be staged paths under .artifact-stage/ (or .intake/.artifact-stage/).
The guard runs from the trusted main copy and, in order:

  1. trusted checkout of main into .trusted-main (ref: main; sparse: this file only), second step of the guard job,
     after the workflow-files checkout, before any pull-request code could run there;
  2. resolve python3 into a step output (id: pyresolve), before any pull-request code runs in the job;
  3. --check-placement (this file, from the main copy) on the checked-out workflows;
  4. (build job) the build and the intermediate-artifact hand-off;
  5. --stage: expand the source patterns, check every matched entry by content, copy the matched files into a fresh
     .artifact-stage/ and record their sha256 in .artifact-stage-manifest.json;
  6. --verify: recompute the hashes and refuse any change, extra, missing or swapped file;
  7. upload the staged paths only.

Content checks: directories are walked, symlinks are refused (including a symlinked directory used as a path component,
which would otherwise reach outside the checkout), and ELF/Mach-O/PE/ar signatures and object/executable
suffixes are rejected. Unrecognised `path:` forms are refused rather than guessed. Names are not used, because legitimate
evidence JSON may contain "geargrafx". This is a content/signature tripwire only. It does not reject .pce by extension
(approved policy: original HuCARD and DEV diagnostic ROMs are legitimate artifacts), it does not detect BIOS-like content,
and a passing .pce is not proof of GPL-free origin (Issue #335). A signature check cannot establish where a .pce came
from; distribution conditions stay with docs/provenance.md and Issue #105.

Publication routes the parser cannot see through are refused rather than guessed. `check_upload_artifacts` and
`check_guard_placement` scan the same files with the same parser, so a route such as `gh release upload`,
`softprops/action-gh-release`, `ncipollo/release-action`, a local action or a reusable workflow fails both, and a
workflow with no upload step is not silently skipped by the placement check. The route list is closed: a newly
introduced publishing action must be taught to this file explicitly. The parser is line based, not a full YAML
parser: folded scalars, anchors/aliases, flow-style steps and YAML merge keys are refused where they could hide an
upload, and that limit is stated here rather than ignored.

Limits: each job is a fresh GitHub-hosted runner VM, so nothing from an earlier run or job survives into it, and no
pull-request code runs in the guard job. Within the guard job, however, the trusted copy, the staging and the upload
run under the same runner user in the same job: a compromised trusted step (or runner) could still overwrite the
trusted copy or the python3 binary, and the stage and verify steps narrow but cannot close the window between verify
and upload. See the pull request description for the residual risk.

Command line (run by the workflows from the trusted copy, with --root "$GITHUB_WORKSPACE"):
  --stage WORKFLOW STEP | --verify WORKFLOW STEP | --post-build WORKFLOW STEP | --check-placement | (no argument: static)
"""
from pathlib import Path
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ".github/workflows"
ACTIONS = ".github/actions"
STAGE_DIR = ".artifact-stage"
STAGE_MANIFEST = ".artifact-stage-manifest.json"
RUNNER_TEMP_PREFIX = "${{ runner.temp }}/"
# Upload patterns that resolve outside the checkout (runner temp). Exact match only; anything else with
# a GitHub expression is refused so that a new expression pattern gets reviewed.
OUTSIDE_CHECKOUT = {"${{ runner.temp }}/verdict/cdread-verdict.json"}
OBJECT_SUFFIXES = {".o", ".obj", ".a", ".lib", ".so", ".dll", ".dylib", ".exe", ".elf", ".out"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".lz4", ".cab", ".jar", ".whl", ".z"}
ARCHIVE_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"BZh", b"\xfd7zXZ\x00",
                  b"Rar!", b"\x28\xb5\x2f\xfd")
SIGNATURES = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
              b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"!<arch>\n")
BLOCK_SCALARS = ("|", "|-", "|+")   # line-based; folded '>' forms and anchors/aliases are refused as unparseable
# Step key: optional quotes, optional spacing before the colon, any letter case. Only the upload-artifact action matches.
USES_RE = re.compile(r"""["']?uses["']?\s*:\s*["']?actions/upload-(?:pages-)?artifact@""", re.IGNORECASE)
# Any other publishing route (release assets, third-party upload actions, local actions and reusable workflows
# whose upload cannot be seen from here) is refused rather than guessed.
UPLOAD_RE = re.compile(r"""upload-(?:pages-)?artifact|\bgh\s+release\b|release-action|action-gh-release|release-asset"""
                       r"""|create-release|uploads\.github\.com|api\.github\.com/repos/\S*/releases|/releases\b|\bgh\s+api\b.*\breleases\b"""
                       r"""|["']?uses["']?\s*:\s*["']?(?:\./|[^#\s]*\.github/workflows/)""", re.IGNORECASE)
# pathlib glob has no brace, extglob or alternation syntax: such a pattern would match nothing and hide its files.
UNSUPPORTED_PATTERN_CHARS = "{}()|"
# A guard-job step's own (top-level) `uses:` key and the actions/* action it names. Only these actions may be a
# guarded step; any other step must be one of the trusted run steps. Reading the top-level key only stops a
# `uses:`-looking line inside a run: block from masquerading as an allowed action and skipping the run checks.
USES_KEY_RE = re.compile(r"""["']?uses["']?\s*:\s*(.*)$""", re.IGNORECASE)
ACTION_PATH_RE = re.compile(
    r"actions/(checkout|download-artifact|configure-pages|deploy-pages|upload-artifact|upload-pages-artifact)@")
# Fail-closed allowlist (Issue #337). Every uses: key in a workflow or composite action must name one of these
# actions with a ref; any other action (a release action, a third-party upload) is refused, because a publication
# route the guard does not name would otherwise pass both layers. Adding an action is a reviewed change here.
KNOWN_ACTIONS = frozenset({
    "actions/checkout", "actions/upload-artifact", "actions/download-artifact", "actions/upload-pages-artifact",
    "actions/configure-pages", "actions/deploy-pages", "actions/attest-build-provenance",
})
# Issue #349: the ref must be one full 40-digit commit SHA. A tag can be moved after review, and a ref that is any
# text let `actions/checkout@v4 acme/publisher@v1` through.
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
# Publishing actions that must not appear in a file without an upload-artifact step (Issue #349, finding 1): such a
# file is not covered by the upload layer, so the placement check refuses it outright.
PUBLISH_USES_RE = re.compile(r"actions/(?:deploy-pages|upload-pages-artifact|attest[\w-]*)@", re.IGNORECASE)   # attest*: any attest action (Issue #349)
_UNLISTED_MESSAGE = ("uses: action not on the allowlist or not pinned to one full commit SHA "
                     "(fail closed, Issue #337, #349)")
# A uses: key anywhere on a line: block form, quoted key, or inside a flow mapping such as {uses: x}.
USES_ANY_RE = re.compile(r"""(?:^|[\s{,\[-])["']?uses["']?\s*:""", re.IGNORECASE)
# A key the line reader cannot see as text: a complex key (`? uses` with the value on the next line) or a double-quoted
# key with an escape (`"uses": x` is the key uses). Refused rather than guessed.
OPAQUE_KEY_RE = re.compile(r"""^\s*(?:-\s+)?\?(?:\s|$)|"[^"]*\\[^"]*"\s*:""")


def _unlisted_uses(line):
    """The uses: values on one line that do not name a KNOWN_ACTIONS entry pinned to one full commit SHA. Empty values
    (a value on the next line, a folded scalar indicator) are unlisted too."""
    values = []
    for key in USES_ANY_RE.finditer(line):
        value = _unquote(re.split(r"\s#|[,}\]]", line[key.end():], maxsplit=1)[0].strip())
        name, at, ref = value.partition("@")
        if not (at and FULL_SHA_RE.fullmatch(ref) and name.lower() in KNOWN_ACTIONS):
            values.append(value)
    return values


def _indent(line):
    return len(line) - len(line.lstrip())


PATH_KEY_RE = re.compile(r"""["']?path["']?\s*:""")


def _scalar(value):
    """The YAML scalar of a `key:` value: quotes removed, and a trailing comment (` #...`) of a plain scalar dropped."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _dash_values(lines, start, end, dash, key):
    """Raw values of `key:` keys at dash level only: the dash line's own key, or keys at indent dash+2.

    Nested keys (for example `with: name:`, the artifact name of upload-artifact) are not returned.
    """
    values = []
    for i in range(start, end):
        if not (lines[i].strip() and not lines[i].lstrip().startswith("#")):
            continue
        if i == start:
            body = lines[i].lstrip()[2:].lstrip()
        elif _indent(lines[i]) == dash + 2:
            body = lines[i].strip()
        else:
            continue
        m = re.match(r"""["']?%s["']?\s*:\s*(.*)$""" % key, body)
        if m:
            values.append(m.group(1))
    return values


def _step_patterns(lines, index, column):
    """Return (step name, path patterns) of the upload-artifact step whose `uses` key starts at lines[index][column].

    The step name is the dash-level `name:` only. More than one dash-level name is refused.
    """
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
    names = _dash_values(lines, start, end, dash, "name")
    if len(names) > 1:
        raise ValueError(f"upload-artifact step has {len(names)} step names at its own level; exactly one is allowed")
    step_name = _scalar(names[0]) if names else ""
    patterns = []
    path_keys = 0
    for offset, line in enumerate(step):
        body = line.lstrip()
        if body.startswith("- "):
            body = body[2:].lstrip()
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


def source_pattern(pattern):
    """The workspace (or intake) pattern that a staged upload path mirrors. An unstaged pattern is returned unchanged."""
    if pattern.startswith(INTAKE_STAGE):
        return pattern[len(INTAKE_STAGE):]
    prefix = STAGE_DIR + "/"
    if not pattern.startswith(prefix):
        return pattern
    rest = pattern[len(prefix):]
    if rest.startswith("_runner_temp/"):
        return RUNNER_TEMP_PREFIX + rest[len("_runner_temp/"):]
    return rest


def staged_pattern(source):
    """The staged upload path for a source pattern (the inverse of source_pattern)."""
    if source.startswith(RUNNER_TEMP_PREFIX):
        return f"{STAGE_DIR}/_runner_temp/" + source[len(RUNNER_TEMP_PREFIX):]
    return f"{STAGE_DIR}/{source}"


def upload_patterns(text):
    """Return the path patterns of every upload-artifact step in one workflow file."""
    return [p for _, patterns in upload_steps(text) for p in patterns]


def upload_steps(text):
    """Return (step name, path patterns) for every upload-artifact step in one workflow file."""
    lines = text.splitlines()
    patterns = []
    for index, line in enumerate(lines):
        value = USES_VALUE_RE.match(line)
        if value is not None:
            raw = value.group(1).split("#")[0].strip()
            # A `uses:` value we cannot read (a YAML escape sequence or an alias whose anchor may name another
            # action) is refused rather than guessed: either could hide an upload the placement gate would miss.
            if "\\" in raw or raw.startswith("*"):
                raise ValueError(f"unsupported uses: value (escape sequence or alias): {line.strip()!r}")
        match = USES_RE.search(line)
        if match:
            # Issue #349: an upload step's own action is held to the same allowlist and full-SHA pin as any other.
            if _unlisted_uses(line):
                raise ValueError(_UNLISTED_MESSAGE + f": {line.strip()!r}")
            patterns.append(_step_patterns(lines, index, match.start()))
        elif UPLOAD_RE.search(line):
            # Fail closed: a mention that is not a parsed upload step may still be an upload of unknown shape.
            raise ValueError(f"unsupported upload-artifact reference (not a parsed step): {line.strip()!r}")
        elif not line.lstrip().startswith("#") and OPAQUE_KEY_RE.search(line):
            raise ValueError(f"YAML key the line reader cannot see (complex or escaped key, fail closed): {line.strip()!r}")
        elif not line.lstrip().startswith("#") and _unlisted_uses(line):
            raise ValueError(_UNLISTED_MESSAGE + f": {line.strip()!r}")
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


def _symlinked_component(base, path):
    """The first path component of `path` below `base` that is a symlink, or None. Named, never followed, so a
    glob that reaches through a symlinked directory ('link/file') cannot escape the checkout."""
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return None
    current = Path(base)
    for part in parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def _paths_for(base, pattern):
    """Return (matched paths, error) for one source pattern. An unsafe or unsupported pattern gives an error."""
    if pattern in OUTSIDE_CHECKOUT:
        temp = os.environ.get("RUNNER_TEMP")
        if not temp:
            return [], f"RUNNER_TEMP unset for {pattern!r}"
        return [p for p in [Path(temp, pattern.split("/", 1)[1])] if p.exists()], None
    if (not pattern or "${{" in pattern or pattern.startswith(("/", "~")) or ".." in Path(pattern).parts
            or re.match(r"[A-Za-z]:", pattern)):
        return [], f"unresolvable or unsafe pattern {pattern!r}"
    if any(char in pattern for char in UNSUPPORTED_PATTERN_CHARS):
        return [], f"unsupported pattern {pattern!r} (brace, extglob or alternation)"
    paths = list(Path(base).glob(pattern))
    for path in paths:
        link = _symlinked_component(base, path)
        if link is not None:
            return [], f"pattern {pattern!r} reaches through a symlink ({_rel(link, base)}); refusing rather than stage it"
    return paths, None


def pattern_problems(base, label, pattern, strict):
    """Return (matched count, problems) for one pattern. strict (post-build): a pattern with no match is a problem."""
    if pattern in OUTSIDE_CHECKOUT and not strict:
        return 0, []
    paths, error = _paths_for(base, pattern)
    if error:
        return 0, [f"{label}: {error}"]
    problems = []
    if strict and not paths:
        problems.append(f"{label}: pattern {pattern!r} matched no built artifact")
    for path in paths:
        for entry, reason in tree_problems(path):
            problems.append(f"{label}: {_rel(entry, base)} ({reason})")
    return len(paths), problems


def _workflow_files(base):
    """Workflow and composite-action files. Matched by name case-insensitively: Linux globs are case-sensitive, so a
    `extra.YML` workflow would otherwise be skipped by the scan while GitHub-side tooling may still read it."""
    workflows, actions = base / WORKFLOWS, base / ACTIONS
    found = [p for p in workflows.iterdir() if p.is_file() and p.suffix.lower() in (".yml", ".yaml")] if workflows.is_dir() else []
    found += [p for p in actions.rglob("*") if p.is_file() and p.name.lower() in ("action.yml", "action.yaml")] if actions.is_dir() else []
    return sorted(found)


def check_upload_artifacts(root=ROOT):
    """Static pre-build layer: raise ValueError if any upload-artifact pattern matches a linked executable, object or symlink."""
    total = 0
    matched = 0
    problems = []
    base = Path(root)
    for workflow in _workflow_files(base):
        label = workflow.relative_to(base).as_posix()
        text = workflow.read_text(encoding="utf-8")
        for pattern in upload_patterns(text):
            total += 1
            count, found = pattern_problems(base, label, source_pattern(pattern), False)
            matched += count
            problems += found
    if total == 0:
        raise ValueError("no upload-artifact path patterns found; guard cannot establish the upload set")
    if problems:
        raise ValueError("artifact guard: linked executable or unsafe entry would be uploaded: "
                         + "; ".join(problems))
    print(f"PASS upload-artifact guard: {total} patterns, {matched} local entries, no linked executable or object")


# A job id is any YAML key at indent 2 (dots and quotes allowed); the name is the key without quotes.
JOB_RE = re.compile(r"""^  (?:"([^"]+)"|'([^']+)'|([^\s"'#:][^#:]*?))\s*:\s*(?:#.*)?$""")
UPLOAD_STEP_KEYS = {"name", "uses", "with"}   # no if:, continue-on-error:, env: on the upload itself
GUARD_STEP_KEYS = {"name", "run", "shell"}    # stage and verify steps
# Steps that run a trusted command (python resolve, placement check, stage, verify). `id` is for the python resolve;
# no if:, continue-on-error:, env: or working-directory is allowed, because any of them can skip or reroute the guard.
RUN_STEP_KEYS = {"name", "id", "run", "shell"}
PREAMBLE_STEP_KEYS = {"name", "id", "uses", "with", "run", "shell"}   # trusted checkout, python resolve, placement check
# The upload step's name is embedded in double quotes in the guard calls, so these must not appear in it.
NAME_FORBIDDEN = '"$`\\'
# Job-level or workflow-level settings that can change PATH, RUNNER_TEMP or the working directory the guard sees.
SCOPE_KEYS_REFUSED = {"env", "defaults", "container", "services"}
# Exact text of the trusted commands (the workflow must contain these runs verbatim, with the step name quoted).
TRUSTED_GUARD = '".trusted-main/tools/artifact_guard.py"'
TRUSTED_PY = '"${{ steps.pyresolve.outputs.python }}" ' + TRUSTED_GUARD + ' --root "$GITHUB_WORKSPACE"'
CHECK_TEXT = TRUSTED_PY + " --check-placement"
PY_RESOLVE_RUN = 'echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"'
CHECKOUT_PIN_RE = re.compile(r"actions/checkout@[0-9a-f]{40}$")
# The exact with: inputs of the trusted main checkout: the guard file must come from this repository's main.
TRUSTED_CHECKOUT_WANT = {"ref": "main", "path": ".trusted-main",
                         "sparse-checkout": "tools/artifact_guard.py", "sparse-checkout-cone-mode": "false"}
# The trusted checkout may only carry these with: inputs. Anything else (repository:, token:, ssh-key:, clean:)
# could fetch the guard file from somewhere other than this repository's main, or leave a planted copy in place.
TRUSTED_CHECKOUT_WITH = {"ref", "path", "sparse-checkout", "sparse-checkout-cone-mode", "persist-credentials"}
# The guard job's first step checks out the pull-request workflows that --check-placement then reads. It may not carry
# ref:, repository: or token:/ssh-key: (which would point it at main or another repository instead of the pull request).
PR_CHECKOUT_WITH = {"sparse-checkout", "sparse-checkout-cone-mode", "persist-credentials", "fetch-depth"}
# The Pages actions may only carry these keys. configure-pages can run a site generator, so a `with:` (e.g. a
# generator writing into the workspace) is refused: the guard job only deploys the artifact the guard itself staged.
PAGES_ACTION_KEYS = {"name", "id", "uses"}
# The value of a `uses:` key at a step-key position (anchored, so run: text that mentions uses: is not read).
USES_VALUE_RE = re.compile(r"""\s*(?:-\s+)?["']?uses["']?\s*:\s*(.*)$""")
# Guard jobs publish from an intake directory that holds the downloaded intermediate artifact.
INTAKE_ROOT = ".intake"
INTAKE_STAGE = INTAKE_ROOT + "/" + STAGE_DIR + "/"
INTAKE_PY = ('"${{ steps.pyresolve.outputs.python }}" ' + TRUSTED_GUARD + ' --root "' + INTAKE_ROOT
             + '" --workflow-root "$GITHUB_WORKSPACE"')


def _stage_text(workflow, name):
    return f'{TRUSTED_PY} --stage {workflow} "{name}"'


def _verify_text(workflow, name):
    return f'{TRUSTED_PY} --verify {workflow} "{name}"'


def _live(line):
    return line.strip() and not line.lstrip().startswith("#")


def _job_name(line):
    m = JOB_RE.match(line)
    return next(g for g in m.groups() if g is not None) if m else None


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
        job = next((_job_name(lines[j]) for j in range(start, -1, -1) if _job_name(lines[j]) is not None), None)
        result.append((start, end, job))
    return result


def _step_keys(lines, start, end, dash):
    """Top-level mapping lines of one step (the dash line's own content plus lines at dash+2)."""
    return [lines[start].lstrip()[2:].strip()] + [l.strip() for l in lines[start + 1:end] if _live(l) and _indent(l) == dash + 2]


def _key_names(keys):
    return [k.split(":", 1)[0].strip().strip("\"'") for k in keys]


def _run_lines(lines, start, end, dash):
    """Live lines of the step's `run:` value (inline value, or the body of a `|` / `>` block); [] if no run:."""
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
            body = []
            for x in lines[i + 1:end]:
                if _live(x) and _indent(x) <= dash + 2:
                    break
                if _live(x):
                    body.append(x.strip())
            return body
        if not value:
            return []
        # A plain (inline) scalar continues onto any more-indented following line (YAML folding), so the whole run is
        # not the one line we matched. Return the continuation too, so a single-line trusted command cannot be spoofed
        # by appending `; <command>` on the next line (a trusted step may only be its exact one-line command).
        continuation = []
        for x in lines[i + 1:end]:
            if _live(x) and _indent(x) <= dash + 2:
                break
            if _live(x):
                continuation.append(x.strip())
        return [value] + continuation
    return []


def _shell_ok(keys):
    """True when every `shell:` key of a step is absent or exactly `bash`/`"bash"` (no injected command)."""
    return all(re.sub(r"""["']""", "", k.split(":", 1)[1]).split("#")[0].strip() == "bash"
               for k in keys if re.match(r"""["']?shell["']?\s*:""", k))


def _guard_is_bare(command, expected, keys):
    """The step's whole `run:` value is ONE live line, and that line is exactly `expected`.

    The text must match exactly: the name is double-quoted in it and cannot contain `"`, `$`, backtick or backslash,
    so nothing inside the quotes is expanded. Any unquoted operator (`||`, `;`, `&`, `|`), comment, extra argument or
    glob outside the quotes makes the text differ. Token-based checks were not enough: shlex does not split `||`
    unless punctuation is requested (MINOR-B). `shell:` may be absent or `bash`.
    """
    if len(command) != 1:
        return False
    return _shell_ok(keys) and command[0] == expected


# Issue #349 (review, MAJOR): a publishing action may sit in any job of any file. It is refused unless its job is one of
# these (file name, job) pairs (the owner decision: the two attestations only), or it is a Pages action in a guard job
# that carries a final upload (pages.yml deploy: its placement is checked by guard_placement_violations).
# The attestation subjects each allowed job may sign (owner decision, Issue #349). The allowance binds the subject-path
# value and the with: keys too, not only the job name.
ATTEST_SUBJECTS = {
    ("api-source.yml", "attest-cdread"): frozenset({"evidence/cdread-probe/cdread-evidence.json",
                                                    "evidence/cdread-probe/cdread-probe-not-bios.pce"}),
    ("api-source.yml", "attest-cdread-verdict"): frozenset({"verdict/cdread-verdict.json"}),
}
PUBLISH_JOB_ALLOWLIST = frozenset(ATTEST_SUBJECTS)
PAGES_PUBLISH_ACTIONS = ("actions/deploy-pages@", "actions/upload-pages-artifact@")
ATTEST_USES_RE = re.compile(r"actions/attest-build-provenance@", re.IGNORECASE)
ATTEST_ANY_RE = re.compile(r"actions/attest", re.IGNORECASE)
# The only condition under which a signing job may run (owner decision, Issue #349). Compared as a whole expression after
# whitespace normalization: any removal, `!=`, a different ref, `||`, or a quoted/block form is a refusal. Policy lives in
# the workflow; this guard only enforces it. Residual risks not covered here: see the note at attest_condition_violations.
SIGN_CONDITION = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
# Issue #337 round 4. (a) signing or OIDC-token text is refused on any live line except the approved attest uses: line,
# a display name, and a read-only `gh attestation verify`; (b) an approved signing job keeps the job that produces its
# artifact in needs:; (c) continue-on-error is refused in a signing job; (d) an upload pattern that can select a ROM file
# must be on APPROVED_ROM_UPLOADS, and a staged ROM file must come from an approved pattern.
ATTEST_NEEDS = {("api-source.yml", "attest-cdread"): {"guard-bios-dev"},
                ("api-source.yml", "attest-cdread-verdict"): {"guard-cdread-verdict"}}
SIGNING_TERMS_RE = re.compile(r"sigstore|cosign|fulcio|rekor|(?<![\w-])attest(?![\w-])|actions/attest|gh\s+attestation"
                              r"|ACTIONS_ID_TOKEN_REQUEST_TOKEN", re.IGNORECASE)
SIGNING_PERMISSION_RE = re.compile(r"""^\s*(?:-\s+)?["']?(id-token|attestations)["']?\s*:\s*(\S+)""")
APPROVED_ATTEST_USES_RE = re.compile(r"""uses\s*:\s*actions/attest-build-provenance@[0-9a-f]{40}\s*(?:#.*)?$""")
GH_VERIFY_RE = re.compile(r"""(?:run\s*:\s*)?gh attestation verify\b""")
READ_ONLY_ATTEST_PERMISSION_RE = re.compile(r"""\s*(?:-\s+)?attestations\s*:\s*read\s*""")
# (a) An approved signing job runs exactly these steps, in order: a step that runs code (a script from the downloaded
# artifact, `printenv`, `uses: ./local`) would hold the OIDC token without any signing word, so it is refused by shape.
ATTEST_STEPS = {
    ("api-source.yml", "attest-cdread"): [("uses", "actions/download-artifact"),
                                          ("uses", "actions/attest-build-provenance")],
    ("api-source.yml", "attest-cdread-verdict"): [
        ("uses", "actions/download-artifact"),
        ("run", ('test -n "$ACTIONS_ID_TOKEN_REQUEST_URL" || { echo "no id-token, verdict not signed"; exit 1; }',)),
        ("uses", "actions/attest-build-provenance")],
}
# Issue #337 round 11. The download step of each approved signing job is pinned: the action pin, the artifact name it fetches
# (the name of the upload step in the same file), the directory it extracts into, and the only with: keys allowed. `files`
# are the signed files relative to that directory, so each subject-path must be path + "/" + file (attest_download_violations).
DOWNLOAD_USES = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
DOWNLOAD_WITH_KEYS = frozenset({"name", "path"})
ATTEST_DOWNLOADS = {
    ("api-source.yml", "attest-cdread"): {
        "name": "bios-dev-not-compatible-${{ github.sha }}", "path": "evidence",
        "files": frozenset({"cdread-probe/cdread-evidence.json", "cdread-probe/cdread-probe-not-bios.pce"})},
    ("api-source.yml", "attest-cdread-verdict"): {
        "name": "cdread-verdict-${{ github.sha }}", "path": "verdict", "files": frozenset({"cdread-verdict.json"})},
}
# Issue #337 round 11 (review): the intermediate downloads of the two guard jobs feed the uploads that are signed, so they are
# pinned the same way (name of the matching upload, path, with: keys). They sign nothing, so there is no subject check.
GUARD_DOWNLOADS = {
    ("api-source.yml", "guard-bios-dev"): {"name": "intermediate-bios-dev-${{ github.sha }}", "path": ".intake"},
    ("api-source.yml", "guard-cdread-verdict"): {"name": "intermediate-cdread-verdict-${{ github.sha }}", "path": ".intake"},
}
ROM_CANDIDATES = ("x.pce", "x.bin", "x.rom")
ROM_SUFFIXES = (".pce", ".bin", ".rom")
# The ROM upload patterns that exist today (staged form), per workflow file. Each is a synthetic probe ROM or the project's
# own debug build; the guard does not inspect what a glob selects beyond the executable and archive checks.
APPROVED_ROM_UPLOADS = frozenset(
    {("api-source.yml", ".intake/.artifact-stage/build/debug/bios-dev/*.pce"),
     ("api-source.yml", ".intake/.artifact-stage/build/debug/bios-dev/contract-probes/*.pce"),
     ("api-source.yml", ".intake/.artifact-stage/build/debug/startup-probe/*.pce"),
     ("api-source.yml", ".intake/.artifact-stage/build/debug/cdread-probe/*.pce"),
     ("api-source.yml", ".intake/.artifact-stage/build/debug/cdload-exec/*.pce"),
     ("api-source.yml", ".intake/.artifact-stage/build/debug/cdreset-probe/*.pce"),
     ("ci.yml", ".intake/.artifact-stage/build/debug/*.pce"),
     ("nightly.yml", ".intake/.artifact-stage/build/debug/*.pce")})


def _enclosing_job(lines, index):
    """The block job id that contains lines[index], or None. A line at indent 2 that is not a block job id (for example
    a flow-form job `zz: {...}`) ends the search with None, so it is never attributed to the job above it (Issue #349)."""
    for j in range(index, -1, -1):
        if _live(lines[j]) and _indent(lines[j]) <= 2:
            return _job_name(lines[j]) if _indent(lines[j]) == 2 else None
    return None


def _attest_inputs(lines, dash, end):
    """(with: keys, subject-path values) of one attest step whose list item starts at lines[dash], or None when the
    with: block is not plain block form. Keys and values are read from the step's own block only."""
    dash_indent = _indent(lines[dash])
    keys, paths, in_with, current = [], set(), False, None
    for j in range(dash + 1, end):
        if not _live(lines[j]):
            continue
        indent, text = _indent(lines[j]), lines[j].strip()
        if indent == dash_indent + 2:
            current = None
            in_with = text == "with:"
            if text.startswith("with:") and not in_with:
                return None
        elif indent == dash_indent + 4 and in_with:
            key, sep, value = text.partition(":")
            if not sep:
                return None
            key = key.strip().strip("\"'")
            keys.append(key)
            current = key if key == "subject-path" else None
            value = value.split(" #")[0].strip()
            if current and value and value not in BLOCK_SCALARS and value != ">":
                paths.add(value.strip("\"'"))
        elif indent > dash_indent + 4 and in_with and current == "subject-path":
            paths.add(text)
        elif indent > dash_indent + 2 and in_with:
            return None
    return keys, paths


def attest_input_violations(text, label):
    """Problems for an attest step in an allowed job: its with: must be exactly subject-path, and its subject-path must be
    the approved subject set for that (file, job). A step that cannot be parsed in this form is refused (Issue #349)."""
    lines = text.splitlines()
    problems = []
    for i, line in enumerate(lines):
        if not (_live(line) and ATTEST_ANY_RE.search(line)):
            continue
        job = _enclosing_job(lines, i)
        expected = ATTEST_SUBJECTS.get((Path(label).name, job))
        if expected is None:
            continue   # publish_violations refuses the job itself
        if not ATTEST_USES_RE.search(line):
            problems.append(f"{label}: job {job!r} may only sign with actions/attest-build-provenance "
                            f"(found {line.strip()!r}, Issue #349)")
            continue
        column = _indent(line) + (2 if line.lstrip().startswith("- ") else 0)
        dash = next((j for j in range(i, -1, -1) if _live(lines[j]) and lines[j].lstrip().startswith("- ")
                     and _indent(lines[j]) < column), None)
        if dash is None or column != _indent(lines[dash]) + 2:
            problems.append(f"{label}: attest step in job {job!r} is not a plain list item (refused, Issue #349)")
            continue
        end = next((j for j in range(dash + 1, len(lines)) if _live(lines[j]) and _indent(lines[j]) <= _indent(lines[dash])),
                   len(lines))
        found = _attest_inputs(lines, dash, end)
        if found is None or found[0] != ["subject-path"] or found[1] != set(expected):
            got = "unparseable" if found is None else f"keys {found[0]} subjects {sorted(found[1])}"
            problems.append(f"{label}: attest step in job {job!r} may only have subject-path with the approved subjects "
                            f"{sorted(expected)} (found {got}, Issue #349)")
    return problems


def _download_problems(label, job, steps, pin):
    """Issue #337 round 11: problems with the one download-artifact step of one job, and that step (None if not found).
    The step has only uses and with; uses is DOWNLOAD_USES; with: is exactly {name, path} with the pinned values, so run-id,
    repository, github-token, merge-multiple and any other key are refused."""
    uses = [s.get("uses") if isinstance(s, dict) else None for s in steps]
    downloads = [s for s, u in zip(steps, uses) if isinstance(u, str) and u.startswith("actions/download-artifact@")]
    if len(downloads) != 1:
        return [f"{label}: job {job!r} must have exactly one download-artifact step (found {len(downloads)}, "
                "Issue #337 round 11)"], None
    download = downloads[0]
    problems = []
    extra = sorted(set(download) - {"name", "uses", "with"}, key=str)   # name is the step's display name only
    if extra:
        problems.append(f"{label}: download-artifact step in job {job!r} sets {extra} (only name, uses and with are "
                        "allowed, Issue #337 round 11)")
    if download.get("uses") != DOWNLOAD_USES:
        problems.append(f"{label}: download-artifact step in job {job!r} must use {DOWNLOAD_USES!r} "
                        f"(found {download.get('uses')!r}, Issue #337 round 11)")
    with_ = download.get("with")
    if not isinstance(with_, dict):
        problems.append(f"{label}: download-artifact step in job {job!r} has no with: mapping (Issue #337 round 11)")
    else:
        bad = sorted(set(with_) - DOWNLOAD_WITH_KEYS, key=str)
        if bad:
            problems.append(f"{label}: download-artifact step in job {job!r} may not set with: {bad} (allowed "
                            f"{sorted(DOWNLOAD_WITH_KEYS)}; run-id, repository, github-token and merge-multiple are "
                            "refused, Issue #337 round 11)")
        if with_.get("name") != pin["name"] or with_.get("path") != pin["path"]:
            problems.append(f"{label}: download-artifact step in job {job!r} must fetch {pin['name']!r} into "
                            f"{pin['path']!r} (found name {with_.get('name')!r}, path {with_.get('path')!r}, "
                            "Issue #337 round 11)")
    return problems, download


def attest_download_violations(text, label, partial_fixtures=False):
    """Issue #337 round 11, for one workflow file (YAML decoded with the strict loader). For each pinned job of this file:
    GUARD_DOWNLOADS (the two guard jobs) and ATTEST_DOWNLOADS (the two signing jobs), the download step is checked by
    _download_problems. For a signing job, the job also has exactly one attest-build-provenance step, and its subject-path is
    exactly path + "/" + each pinned file, a set equal to ATTEST_SUBJECTS for the job.

    A pinned job missing from the file is refused. Only a caller that passes partial_fixtures=True (a test fixture that holds
    part of the workflow) skips a missing job. The real workflow is checked with partial_fixtures=False."""
    name = Path(label).name
    pins = {job: (pin, True) for (file, job), pin in ATTEST_DOWNLOADS.items() if file == name}
    pins.update({job: (pin, False) for (file, job), pin in GUARD_DOWNLOADS.items() if file == name})
    if not pins:
        return []
    try:
        import yaml
    except ImportError:  # fail closed with a finding, like the other checks; never a crash
        return [f"{label}: PyYAML unavailable, download pins cannot be checked (Issue #337 round 11)"]
    try:
        doc = yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError) as error:
        return [f"{label}: YAML cannot be read, download pins cannot be checked ({error}; Issue #337 round 11)"]
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    problems = []
    for job, (pin, signing) in sorted(pins.items()):
        if not isinstance(jobs, dict) or job not in jobs:
            if not partial_fixtures:
                problems.append(f"{label}: job {job!r} is missing from {name}; the real workflow must keep it "
                                "(Issue #337 round 11)")
            continue
        body = jobs.get(job)
        steps = body.get("steps") if isinstance(body, dict) else None
        if not isinstance(steps, list):
            problems.append(f"{label}: job {job!r} has no steps list to check (Issue #337 round 11)")
            continue
        step_problems, download = _download_problems(label, job, steps, pin)
        problems += step_problems
        if not signing or download is None:
            continue
        attests = [s for s in steps if isinstance(s, dict) and isinstance(s.get("uses"), str)
                   and s["uses"].startswith("actions/attest-build-provenance@")]
        if len(attests) != 1:
            problems.append(f"{label}: signing job {job!r} must have exactly one attest-build-provenance step "
                            f"(found {len(attests)}, Issue #337 round 11)")
            continue
        attest_with = attests[0].get("with")
        subject_text = attest_with.get("subject-path") if isinstance(attest_with, dict) else None
        subjects = {line.strip() for line in subject_text.splitlines() if line.strip()} if isinstance(subject_text, str) else set()
        expected = {f"{pin['path']}/{file}" for file in pin["files"]}
        if subjects != expected or subjects != set(ATTEST_SUBJECTS[(name, job)]):
            problems.append(f"{label}: attest step in job {job!r} must sign exactly the files of the pinned download, "
                            f"{sorted(expected)} (found {sorted(subjects)}, Issue #337 round 11)")
    return problems


def yaml_integrity_violations(text, label):
    """Issue #337 round 11, for every workflow and composite action file. A mapping with a duplicate key is refused (PyYAML
    keeps the last value silently) and so is any YAML anchor or alias, which can repeat or hide a value. Applies to the whole
    file, so it covers jobs outside the signing set too."""
    try:
        import yaml
    except ImportError:  # fail closed with a finding, like the other checks; never a crash
        return [f"{label}: PyYAML unavailable, YAML integrity cannot be checked (Issue #337 round 11)"]
    try:
        yaml.load(text, Loader=_strict_loader())
        events = list(yaml.parse(text, Loader=yaml.SafeLoader))
    except (yaml.YAMLError, ValueError) as error:
        return [f"{label}: YAML refused ({error}; Issue #337 round 11)"]
    problems = []
    for event in events:
        if isinstance(event, yaml.AliasEvent):
            problems.append(f"{label}: YAML alias *{event.anchor} is refused (Issue #337 round 11)")
        elif getattr(event, "anchor", None):
            problems.append(f"{label}: YAML anchor &{event.anchor} is refused (Issue #337 round 11)")
    return problems


def _if_scalars(lines, head, end):
    """Each job-level `if:` key in lines[head+1:end], as its scalar parts: the key line's own value, then every live line
    indented deeper than the key (a plain or quoted continuation, or a block body). Issue #337 round 2: a continuation
    line was ignored before, so `|| github.event_name == 'pull_request'` under `if:` passed the text check."""
    found = []
    for i in range(head + 1, end):
        line = lines[i]
        if not (_live(line) and _indent(line) == 4 and re.match(r"""["']?if["']?\s*:""", line.strip())):
            continue
        parts = [re.split(r"\s#", line.strip().split(":", 1)[1], maxsplit=1)[0]]
        for j in range(i + 1, end):
            if _live(lines[j]) and _indent(lines[j]) <= 4:
                break
            if _live(lines[j]):
                parts.append(re.split(r"\s#", lines[j].strip(), maxsplit=1)[0])
        found.append(parts)
    return found


SAFE_TAGS = frozenset({"tag:yaml.org,2002:str", "tag:yaml.org,2002:map"})
# A job-level key (indent 4) must be a plain key: no tag `!`, anchor `&`, alias `*`, quotes or complex-key `?` (Issue #337 r3).
JOB_KEY_RE = re.compile(r"[A-Za-z_][\w.-]*[ \t]*:(\s|$)")


def _strict_loader():
    """SafeLoader subclass that refuses a mapping with a duplicate key (Issue #337 round 3: last-wins hid a second `if:`)."""
    import yaml

    class StrictLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            if isinstance(node, yaml.MappingNode):
                keys = [k.value for k, _ in node.value if isinstance(k, yaml.ScalarNode)]
                dups = sorted({k for k in keys if keys.count(k) > 1})
                if dups:
                    raise ValueError(f"duplicate mapping key(s) {dups}")
            return super().construct_mapping(node, deep)
    return StrictLoader


def _node_problems(block):
    """Reasons the job block is refused by the event stream: any anchor, any alias, or any tag other than str/map.
    Issue #337 round 3. Empty when clean; an unparseable block is a refusal too."""
    try:
        import yaml
    except ImportError:
        return ["PyYAML unavailable"]
    reasons = []
    try:
        for event in yaml.parse("\n".join(block) + "\n", Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent):
                reasons.append(f"alias *{event.anchor}")
                continue
            if getattr(event, "anchor", None):
                reasons.append(f"anchor &{event.anchor}")
            tag = getattr(event, "tag", None)
            if tag and tag not in SAFE_TAGS:
                reasons.append(f"tag {tag}")
    except yaml.YAMLError:
        reasons.append("unparseable")
    return reasons


def _effective_if(lines, head, end, job):
    """The job's `if:` value as PyYAML parses the job's own block (lines[head:end]), or None when it cannot be read as one
    string. Issue #337 round 2: this is the source of truth next to the text check. The block is parsed alone, so a
    malformed unrelated part of the file cannot hide a signing job's value. No PyYAML means None, so the caller refuses.
    Issue #337 round 3: a duplicate key anywhere in the block makes this None (strict loader)."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        value = yaml.load("\n".join(lines[head:end]) + "\n", Loader=_strict_loader())[job]["if"]
    except (yaml.YAMLError, KeyError, TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _key_problems(lines, head, end):
    """Job-level keys (indent 4) that are not plain keys, and how many keys normalize to `if` (Issue #337 round 3).
    The approved form is exactly one plain `if:` line; a tagged, anchored, quoted, `?` or duplicate key is refused."""
    bad, if_keys = [], 0
    for i in range(head + 1, end):
        if not (_live(lines[i]) and _indent(lines[i]) == 4):
            continue
        text = lines[i].strip()
        if not JOB_KEY_RE.match(text):
            bad.append(text)
        elif text.split(":", 1)[0].strip().lower() == "if":
            if_keys += 1
    return bad, if_keys


def _normal(value):
    return " ".join(value.split())


def attest_condition_violations(text, label):
    """Each signing job present in this file must be a block-form job with exactly one job-level `if:`, equal to
    SIGN_CONDITION after whitespace normalization (Issue #349). Flow-form, duplicated or missing jobs, and any other value
    are refused. Issue #337 round 2: the WHOLE value is compared, continuation lines included, and a block or quoted scalar
    is refused. The effective value that PyYAML parses must agree with the text value, or the check refuses.
    A signing job absent from the file is not checked here: publish_violations refuses any attest step that appears in
    another job or file.

    Issue #337 round 3: a job-level key with a tag, anchor, alias, quotes or `?` is refused, a second key normalizing to
    `if` is refused, a duplicate key anywhere in the job block is refused (strict loader), and any anchor, alias or tag
    other than str/map in the job block is refused (event stream).

    Round 4: a direct Sigstore or cosign call in run:, id-token permission, continue-on-error and needs: removal are checked
    by signing_violations; ROM uploads by rom_upload_violations. Residuals of those checks are listed in their docstrings.
    """
    lines = text.splitlines()
    problems = []
    for name, job in sorted(ATTEST_SUBJECTS):
        if name != Path(label).name:
            continue
        heads = [i for i, l in enumerate(lines) if _live(l) and _indent(l) == 2
                 and re.match(r"""^\s*["']?%s["']?\s*:""" % re.escape(job), l)]
        if not heads:
            continue
        if len(heads) != 1 or _job_name(lines[heads[0]]) != job:
            problems.append(f"{label}: signing job {job!r} must appear once in block form (found {len(heads)}; "
                            "flow form is refused, Issue #349)")
            continue
        end = next((j for j in range(heads[0] + 1, len(lines)) if _live(lines[j]) and _indent(lines[j]) <= 2), len(lines))
        bad_keys, if_keys = _key_problems(lines, heads[0], end)
        if bad_keys or if_keys != 1:
            problems.append(f"{label}: signing job {job!r} must have exactly one plain job-level if: key "
                            f"(non-plain keys {bad_keys}, if keys {if_keys}, Issue #337 round 3)")
        node_reasons = _node_problems(lines[heads[0]:end])
        if node_reasons:
            problems.append(f"{label}: signing job {job!r} refused: anchor, alias or non-standard tag {node_reasons} "
                            "(Issue #337 round 3)")
        found = _if_scalars(lines, heads[0], end)
        values = [_normal(" ".join(parts)) for parts in found]
        effective = _effective_if(lines, heads[0], end, job)
        scalar_form = any(parts[0].strip()[:1] in ("|", ">", '"', "'") for parts in found if parts[0].strip())
        if (len(found) != 1 or scalar_form or values != [SIGN_CONDITION]
                or effective is None or _normal(effective) != SIGN_CONDITION):
            problems.append(f"{label}: signing job {job!r} must have exactly one job-level if: {SIGN_CONDITION!r} "
                            f"(found {values}, effective {effective!r}, block or quoted form refused, Issue #349, #337)")
    return problems


def _job_blocks(lines):
    """(job id, header index, end index) of each block-form job (an indent-2 key). The end is the next top-level line."""
    blocks = []
    for head, line in enumerate(lines):
        job = _job_name(line) if _live(line) and _indent(line) == 2 else None
        if job is None:
            continue
        end = next((j for j in range(head + 1, len(lines)) if _live(lines[j]) and _indent(lines[j]) <= 2), len(lines))
        blocks.append((job, head, end))
    return blocks


def _signing_exempt(line):
    """A line whose signing words name no action: a display name at key level, the approved attest uses: line, or a
    read-only `gh attestation verify` with no other signing term after it (Issue #337 round 4)."""
    body = line.lstrip()
    key = body[2:].lstrip() if body.startswith("- ") else body
    if re.match(r"name\s*:", key) and _indent(line) in (4, 6, 8):
        return True
    if APPROVED_ATTEST_USES_RE.match(key) and _indent(line) in (6, 8):
        return True
    if READ_ONLY_ATTEST_PERMISSION_RE.fullmatch(line.split(" #", 1)[0]):
        return True   # `attestations: read` (the trailing comment is not executed)
    match = GH_VERIFY_RE.match(key)
    return bool(match) and not SIGNING_TERMS_RE.search(key[match.end():])


# Issue #337 round 10. A key that changes the environment a step runs in changes how the approved run texts resolve python3,
# bash and sh, so it is refused. Top-level env: and defaults: are refused for the whole file. In a job that holds id-token:
# write (approved or not), only HELD_JOB_KEYS may appear at job level (fail-closed: env, container, services, defaults and
# any unknown key are refused), and a step may carry only its own keys (HELD_*_STEP_KEYS).
HELD_JOB_KEYS = frozenset({"name", "runs-on", "permissions", "needs", "steps", "if", "outputs", "timeout-minutes",
                           "strategy", "concurrency", "environment"})
HELD_ENVIRONMENT_KEYS = frozenset({"name", "url"})   # a mapping form is accepted only with these keys and string values
HELD_RUN_STEP_KEYS = frozenset({"name", "id", "run"})
HELD_USES_STEP_KEYS = frozenset({"name", "id", "uses", "with"})


def _plain_environment(value):
    """environment: as a name, or a mapping with only name and url, all string values (Issue #337 round 10)."""
    if isinstance(value, str):
        return True
    return (isinstance(value, dict) and set(value) <= HELD_ENVIRONMENT_KEYS
            and all(isinstance(v, str) for v in value.values()))


def execution_scope_violations(text, label):
    """Issue #337 round 10, for one workflow file (YAML decoded). Refused:
    - top-level env: (any key; it can set PATH, BASH_ENV, PYTHONPATH, GIT_* and similar) and top-level defaults: (a shell
      override for every run:);
    - in a job whose permissions hold id-token: write (or write-all), any job-level key outside HELD_JOB_KEYS, so env,
      container, services and defaults, and any unknown key;
    - in such a job, a step with a key outside its allowed set (a run: step allows name, id, run; a uses: step allows name,
      id, uses, with), so env, shell, working-directory and if are refused.
    Residual: a job without id-token is not checked here; its env: and container: are not a signing risk by themselves.
    """
    try:
        import yaml
    except ImportError:
        return [f"{label}: PyYAML unavailable, execution scope cannot be checked (Issue #337 round 10)"]
    try:
        doc = yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError) as error:
        return [f"{label}: YAML cannot be read, execution scope cannot be checked ({error}; Issue #337 round 10)"]
    if not isinstance(doc, dict):
        return [f"{label}: the file is not a mapping (Issue #337 round 10)"]
    problems = []
    if "env" in doc:
        problems.append(f"{label}: top-level env: is refused (it can change how python3, bash or sh resolve, "
                        "Issue #337 round 10)")
    if "defaults" in doc:
        problems.append(f"{label}: top-level defaults: is refused (it sets the shell of every run:, Issue #337 round 10)")
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return problems
    for job, body in jobs.items():
        if not isinstance(body, dict):
            continue   # run_allowlist_violations refuses a job that is not a mapping
        perms = body.get("permissions", doc.get("permissions"))
        if not (perms == "write-all" or (isinstance(perms, dict) and perms.get("id-token") == "write")):
            continue
        for key in sorted(set(body) - HELD_JOB_KEYS, key=str):
            problems.append(f"{label}: job {job!r} holds id-token and sets job-level {key!r} (refused; allowed keys "
                            f"{sorted(HELD_JOB_KEYS)}, Issue #337 round 10)")
        if "environment" in body and not _plain_environment(body["environment"]):
            problems.append(f"{label}: job {job!r} environment: must be a name, or name and url only (Issue #337 round 10)")
        steps = body.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                problems.append(f"{label}: job {job!r} has a step that is not a mapping (Issue #337 round 10)")
                continue
            allowed = HELD_RUN_STEP_KEYS if "run" in step else HELD_USES_STEP_KEYS
            for key in sorted(set(step) - allowed, key=str):
                problems.append(f"{label}: job {job!r} step {step.get('name')!r} sets {key!r} (refused; a step in a job "
                                f"that holds id-token may carry only {sorted(allowed)}, Issue #337 round 10)")
    return problems


def signing_violations(text, label):
    """Issue #337 round 4 (a)(b)(c), for one workflow file.
    (a) signing or OIDC-token text (sigstore, cosign, attest, gh attestation, the OIDC request token) on any live line that is
    not an exempt form, in any job; a job that holds attestations write must be an approved signing job. id-token alone is
    not a signing right (pages.yml deploy needs it), so it is not refused by itself.
    (b) an approved signing job keeps id-token and attestations write, and keeps the job that produces its artifact in needs:.
    (c) continue-on-error is refused in an approved signing job, at job or step level, in any spelling on the line.
    Round 5 and 6: the signing terms are also tested on each run: value as YAML decodes it and _shell_text normalizes it, in
    any job. In a job with id-token: write that is not approved, each run: must exactly match APPROVED_RUN_TEXTS
    (run_allowlist_violations, round 8).
    Not covered here: a signing call under a name none of these terms match is refused by the uses: allowlist, not here."""
    name = Path(label).name
    lines = text.splitlines()
    problems = []
    if any(_live(line) and "write-all" in line for line in lines):
        problems.append(f"{label}: write-all permissions are refused (Issue #337 round 4)")
    for i, line in enumerate(lines):
        if _live(line) and SIGNING_TERMS_RE.search(line) and not _signing_exempt(line):
            problems.append(f"{label}: line {i + 1} calls signing or an OIDC token outside the approved attest step "
                            f"({line.strip()!r}, Issue #337 round 4 (a))")
        if _live(line) and _indent(line) == 2 and _job_name(line) is None and re.search(r"id-token|attestations", line):
            problems.append(f"{label}: line {i + 1} is a flow-form job with an id-token or attestations key (refused, "
                            "Issue #337 round 4 (a))")
    for job, head, end in _job_blocks(lines):
        body = [line for line in lines[head + 1:end] if _live(line)]
        perms = {}
        for line in body:
            match = SIGNING_PERMISSION_RE.match(line)
            if match:
                perms[match.group(1)] = match.group(2)
        approved = (name, job) in ATTEST_NEEDS
        if perms.get("attestations") not in (None, "read", "none") and not approved:
            problems.append(f"{label}: job {job!r} holds attestations write and is not an approved signing job "
                            "(Issue #337 round 4 (a))")
        if not approved:
            continue
        if perms.get("id-token") != "write" or perms.get("attestations") != "write":
            problems.append(f"{label}: signing job {job!r} must keep id-token: write and attestations: write "
                            "(Issue #337 round 4)")
        missing = ATTEST_NEEDS[(name, job)] - set(_job_needs(lines, job))
        if missing:
            problems.append(f"{label}: signing job {job!r} must keep needs: {sorted(missing)}, the job that produces its "
                            "artifact (removal refused, Issue #337 round 4 (b))")
        if any("continue-on-error" in line for line in body):
            problems.append(f"{label}: signing job {job!r} may not use continue-on-error (Issue #337 round 4 (c))")
        steps = [_step_kind(lines, s, e) for s, e, j in _step_ranges(lines, 6) if j == job]
        if steps != ATTEST_STEPS[(name, job)]:
            problems.append(f"{label}: signing job {job!r} must run exactly the approved steps, found {steps} "
                            "(a step that runs code would hold the OIDC token, Issue #337 round 4 (a))")
    problems += execution_scope_violations(text, label)
    problems += run_allowlist_violations(text, label)
    return problems


# Issue #337 round 6: a run: value is the YAML-decoded string (escapes such as \x63 and c are already decoded), and it is
# normalized as shell text by _shell_text before the signing terms are tested, in every job (GH_VERIFY_SPAN_RE: read-only
# verification is exempt wherever it occurs).
GH_VERIFY_SPAN_RE = re.compile(r"gh attestation verify\b")
# Issue #337 round 8: EXACT MATCH is the rule. In a job with id-token: write that is not an approved signing job, a run: value is
# accepted only if its YAML-decoded text, less one trailing newline, equals APPROVED_RUN_TEXTS[(workflow, job, step name)].
# Anything else is refused, so no shell parsing decides acceptance. Keyed by workflow, job and step name, a changed run text
# fails, and the same text in another job or under another step name fails. Each key may be used once per job.
# The pages.yml test (test_artifact_guard_exact_r8) loads the real file and fails if this registry drifts from it.
APPROVED_RUN_TEXTS = {
    ("pages.yml", "deploy", "Resolve python3 (trusted)"):
        'echo "python=$(command -v python3)" >> "$GITHUB_OUTPUT"',
    ("pages.yml", "deploy", "Guard placement (trusted)"):
        '"${{ steps.pyresolve.outputs.python }}" ".trusted-main/tools/artifact_guard.py" --root "$GITHUB_WORKSPACE" --check-placement',
    ("pages.yml", "deploy", "Stage artifacts (trusted guard) for Upload static site"):
        '"${{ steps.pyresolve.outputs.python }}" ".trusted-main/tools/artifact_guard.py" --root "$GITHUB_WORKSPACE" --stage .github/workflows/pages.yml "Upload static site"',
    ("pages.yml", "deploy", "Verify staged artifacts (trusted guard) for Upload static site"):
        '"${{ steps.pyresolve.outputs.python }}" ".trusted-main/tools/artifact_guard.py" --root "$GITHUB_WORKSPACE" --verify .github/workflows/pages.yml "Upload static site"',
}
# Round 6/7 refusals, kept as a second layer behind the exact match (they can refuse, never accept). Only the Pages deploy job
# holds id-token outside the approved signing jobs. `command` is allowed only as `command -v NAME`.
RUN_ALLOWLIST = {("pages.yml", "deploy"): frozenset({"echo", "command"})}
RUN_EXPRESSIONS = {("pages.yml", "deploy"): frozenset({"${{ steps.pyresolve.outputs.python }}"})}
SHELL_PUNCTUATION = "();<>|&{}"
SHELL_OPERATOR_RE = re.compile(r"[();<>|&{}]+")   # a token made only of operator characters (a quoted word never is)
SHELL_SEPARATOR_RE = re.compile(r"[;()|&{}]")
REDIRECTION_RE = re.compile(r"[<>][&|]?")
GH_EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}")


def _shell_text(parts):
    """One run: value as shell text: each backslash-newline joins its two lines, then quotes and backslashes are removed."""
    joined = []
    for part in parts:
        if joined and joined[-1].endswith("\\"):
            joined[-1] = joined[-1][:-1] + part
        else:
            joined.append(part)
    return " ".join(re.sub(r"[\\'\"]", "", "\n".join(joined)).split())


def _simple_commands(script):
    """The simple commands of one shell script, each a list of words. A newline ends a command (shlex treats it as plain
    whitespace, which is why round 6 let a multi-line run through), and so does an operator token holding one of ;()|&{}
    (`;`, `|`, `|&`, `&&`, parentheses, braces). A redirection such as >, >>, >&, >| or <& does not end one.
    Non-POSIX shlex keeps quotes in a word, so the quote rule can see them."""
    lexer = shlex.shlex(re.sub(r"[\r\n]+", " ; ", script), posix=False, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    commands, command = [], []
    for token in lexer:
        if SHELL_OPERATOR_RE.fullmatch(token) and SHELL_SEPARATOR_RE.search(token) and not REDIRECTION_RE.fullmatch(token):
            if command:
                commands.append(command)
            command = []
        else:
            command.append(token)
    if command:
        commands.append(command)
    return commands


def _close_paren(text, open_index):
    """Index of the parenthesis that closes text[open_index], or None."""
    depth = 0
    for j in range(open_index, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            return j
    return None


def _command_problem(command, names, expressions):
    """The problem with one simple command (its words), or None. The first word is compared whole (round 6)."""
    word = command[0]
    if word in {f'"{expression}"' for expression in expressions}:
        return None
    if any(char in word for char in "$'\""):
        return f"command word {word!r} contains $ or a quote"
    if word not in names:
        return f"command word {word!r} is not on the allowlist for this job"
    if word == "command" and (len(command) != 3 or command[1] != "-v"):
        return "command is allowed only as `command -v NAME`"
    return None


def _script_problems(script, names, expressions, where):
    """Problems in one run: script of a job that holds id-token: write and is not an approved signing job (round 6).
    Only the allowlisted command words pass. A backslash or backtick, a parameter expansion, a process substitution, an
    unlisted GitHub expression, or an unclosed quote is refused, because the shell and the word reader could disagree
    on it. Each command substitution is read as a script of its own."""
    refuse = f"{where} (Issue #337 round 4 (a), round 6)"
    if "\\" in script or "`" in script:
        return [f"{refuse}: run: contains a backslash or backtick"]
    for expression in GH_EXPRESSION_RE.findall(script):
        if expression not in expressions:
            return [f"{refuse}: run: uses the GitHub expression {expression!r}, which is not allowed in this job"]
    rest = script
    for expression in expressions:
        rest = rest.replace(expression, "")
    if "${" in rest or "<(" in rest or ">(" in rest:
        return [f"{refuse}: run: uses parameter expansion or process substitution"]
    problems = []
    start = script.find("$(")
    while start != -1:
        end = _close_paren(script, start + 1)
        if end is None:
            return [f"{refuse}: run: has an unclosed command substitution"]
        problems += _script_problems(script[start + 2:end], names, expressions, where)
        start = script.find("$(", end)
    try:
        commands = _simple_commands(script)
    except ValueError:   # shlex: no closing quotation
        return problems + [f"{refuse}: run: has an unclosed quote"]
    quoted = {f'"{expression}"' for expression in expressions}
    for command in commands:
        for index, word in enumerate(command):   # a GitHub expression is allowed only as the first word of a command
            if "${{" in word and not (index == 0 and word in quoted):
                return problems + [f"{refuse}: run: uses a GitHub expression outside the first word of a command"]
        problem = _command_problem(command, names, expressions)
        if problem:
            problems.append(f"{refuse}: {problem}")
    return problems


def run_allowlist_violations(text, label):
    """Issue #337 round 4 (a) and round 6, for one workflow or composite action file (YAML decoded, not line-read).
    The signing terms are tested on every run: value, in every job. In a job with id-token: write that is not an
    approved signing job (ATTEST_NEEDS), each run: must exactly match APPROVED_RUN_TEXTS (round 8), must also pass
    _script_problems (round 6/7 refusals), and the job may not set defaults: or a
    step may not set shell: (both would change how the run: is read). A composite action has no job, so only the
    signing-term test applies to it. Residuals: docs/provenance.md."""
    try:
        import yaml
    except ImportError:
        return [f"{label}: PyYAML unavailable, run: values cannot be checked (Issue #337 round 6)"]
    try:
        doc = yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError) as error:
        return [f"{label}: YAML cannot be read, run: values cannot be checked ({error}; Issue #337 round 6)"]
    if not isinstance(doc, dict):
        return [f"{label}: the file is not a mapping (Issue #337 round 6)"]
    name = Path(label).name
    jobs = doc.get("jobs")
    scopes = list(jobs.items()) if isinstance(jobs, dict) else [(None, doc.get("runs") or {})]
    problems = []
    for job, body in scopes:
        if not isinstance(body, dict):
            problems.append(f"{label}: job {job!r} is not a mapping (Issue #337 round 6)")
            continue
        perms = body.get("permissions", doc.get("permissions"))
        held = perms == "write-all" or (isinstance(perms, dict) and perms.get("id-token") == "write")
        checked = job is not None and (name, job) not in ATTEST_NEEDS and held
        if checked and "defaults" in body:
            problems.append(f"{label}: job {job!r} sets defaults: (a shell override is refused, Issue #337 round 4 (a), round 6)")
        steps = body.get("steps") or []
        if not isinstance(steps, list):
            problems.append(f"{label}: steps of job {job!r} are not a list (Issue #337 round 6)")
            continue
        used = set()
        for step in steps:
            if not isinstance(step, dict):
                if checked:
                    problems.append(f"{label}: job {job!r} has a step that is not a mapping (Issue #337 round 4 (a), round 6)")
                continue
            if checked and "shell" in step:
                problems.append(f"{label}: job {job!r} sets shell: (refused, Issue #337 round 4 (a), round 6)")
            run = step.get("run")
            if run is None:
                continue
            if not isinstance(run, str):
                problems.append(f"{label}: job {job!r} has a run: that is not a string (Issue #337 round 6)")
                continue
            if SIGNING_TERMS_RE.search(GH_VERIFY_SPAN_RE.sub("", _shell_text(run.split("\n")))):
                problems.append(f"{label}: job {job!r} run: calls signing or an OIDC token after YAML decoding and shell "
                                f"normalization ({run!r}, Issue #337 round 4 (a), round 5, round 6)")
            if checked:
                step_name = step.get("name") if isinstance(step.get("name"), str) else None
                key = (name, job, step_name)
                text = run[:-1] if run.endswith("\n") else run
                # Round 9: an approved step may carry only name, id (optional) and run. Any other key (env, if,
                # working-directory, shell, continue-on-error, ...) changes how the run: is executed, so it is refused.
                extra_keys = set(step) - {"name", "id", "run"}
                if key in used or extra_keys or key not in APPROVED_RUN_TEXTS or APPROVED_RUN_TEXTS[key] != text:
                    problems.append(f"{label}: job {job!r} run: {run!r} is not the approved run text for step "
                                    f"{step_name!r} (exact match, Issue #337 round 8)")
                used.add(key)
                names = RUN_ALLOWLIST.get((name, job), frozenset())
                expressions = RUN_EXPRESSIONS.get((name, job), frozenset())
                problems += _script_problems(run, names, expressions, f"{label}: job {job!r} run: {run!r}")
    return problems


def _step_kind(lines, start, end):
    """('uses', action path without @ref) for a step whose only action is a uses: key, ('run', body lines) for a run: step,
    or ('other', ()) for anything else (both keys, no key, a flow form the line reader cannot read)."""
    keys = _step_keys(lines, start, end, 6)
    uses = [m.group(1).split("#")[0].strip().strip("\"'").split("@")[0]
            for m in (USES_KEY_RE.match(k) for k in keys) if m]
    run = _run_lines(lines, start, end, 6)
    if len(uses) == 1 and not run:
        return ("uses", uses[0])
    if run and not uses:
        return ("run", tuple(run))
    return ("other", ())


def _rom_capable(pattern):
    """True when an upload pattern can select a ROM file by its last path segment. A plain directory name is not judged
    here: stage_artifacts refuses a ROM file that a directory selects unless its pattern is approved (Issue #337 round 4 (d))."""
    last = pattern.rstrip("/").rsplit("/", 1)[-1].lower()
    if last.endswith(ROM_SUFFIXES) or any(fnmatch.fnmatchcase(candidate, last) for candidate in ROM_CANDIDATES):
        return True
    return any(c in last for c in "*?[") and "." not in last


def rom_upload_violations(text, label):
    """Issue #337 round 4 (d): every upload pattern that can select a ROM file must be on APPROVED_ROM_UPLOADS for this file."""
    name = Path(label).name
    return [f"{label}: upload path {p!r} can select a ROM file (*.pce, *.bin, *.rom) and is not on APPROVED_ROM_UPLOADS "
            "(Issue #337 round 4 (d))"
            for p in upload_patterns(text) if _rom_capable(p) and (name, p) not in APPROVED_ROM_UPLOADS]


def publish_violations(text, label):
    """Problems for publishing actions (attest-build-provenance, deploy-pages, upload-pages-artifact) in one file, by job.
    A signing action is allowed only in its allowlisted (file, job) pair. A Pages action is decided by
    pages_structure_violations (Issue #349, the Pages structure of the publishing job), and only in pages.yml; a Pages
    action in any other file is refused here."""
    lines = text.splitlines()
    name = Path(label).name
    problems = []
    for i, line in enumerate(lines):
        match = PUBLISH_USES_RE.search(line) if _live(line) else None
        if not match:
            continue
        job = _enclosing_job(lines, i)
        if match.group(0).lower() in PAGES_PUBLISH_ACTIONS:
            if name != PAGES_FILE:
                problems.append(f"{label}: Pages publish action {match.group(0)!r} in job {job!r} is refused outside "
                                f"{PAGES_FILE} (Issue #349)")
            continue   # pages.yml: pages_structure_violations decides the job
        if (name, job) in PUBLISH_JOB_ALLOWLIST:
            continue
        problems.append(f"{label}: publish action {match.group(0)!r} in job {job!r} is refused unless the (file, job) "
                        "pair is allowlisted (Issue #349)")
    return problems


# Issue #349 (Pages privilege split, #374). The publishing job is judged by its structure, from the decoded YAML (strict
# loader): the split layout (validate -> build -> deploy) or the single-job layout. Any other shape is refused.
PAGES_FILE = "pages.yml"
PAGES_SPLIT_JOBS = frozenset({"validate", "build", "deploy"})
PAGES_DEPLOY_IF = "github.event_name == 'push' && github.ref == 'refs/heads/main'"   # push only: pull_request_target and workflow_run also report ref main (Issue #349 review)
PAGES_ALLOWED_TRIGGERS = frozenset({"pull_request", "push", "workflow_dispatch"})   # main's pages.yml triggers (2026-10-20)
WORKFLOW_ALLOWED_TRIGGERS = frozenset({"pull_request", "push", "workflow_dispatch"})   # triggers used on main (2026-10-20); pull_request_target and workflow_run are refused everywhere
SCHEDULE_WORKFLOWS = frozenset({"nightly.yml"})   # the only workflow on main that uses schedule:
PAGES_DEPLOY_PERMISSIONS = {"pages": "write", "id-token": "write"}
PAGES_BUILD_PERMISSIONS = {"contents": "read"}
PAGES_LEGACY_PERMISSIONS = {"contents": "read", "actions": "read", "pages": "write", "id-token": "write"}
PAGES_JOB_KEYS = frozenset({"name", "runs-on", "needs", "if", "permissions", "environment", "timeout-minutes", "steps"})
PAGES_BUILD_KEYS = frozenset({"name", "runs-on", "needs", "permissions", "timeout-minutes", "steps"})
PAGES_DEPLOY_STEP_KEYS = frozenset({"name", "id", "uses"})   # a deploy step: no run, env, if, with, shell or working-directory
PAGES_DEPLOY_USES = re.compile(r"actions/deploy-pages@[0-9a-f]{40}")
PAGES_UPLOAD_USES = re.compile(r"actions/upload-pages-artifact@[0-9a-f]{40}")


def _pages_uses(node):
    """Every uses: value that names a Pages publishing action, anywhere in the decoded document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str) and value.lower().startswith(PAGES_PUBLISH_ACTIONS):
                yield value
            yield from _pages_uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _pages_uses(item)


def _pages_job_problems(job, body, label, needs, condition, permissions, keys):
    """Job-level shape of a publishing job: needs:, if: and permissions: exactly, and only the allowed keys."""
    problems = []
    if body.get("needs") != needs:
        problems.append(f"{label}: job {job!r} must have needs: {needs!r} (found {body.get('needs')!r}, Issue #349)")
    if condition is not None and body.get("if") != condition:
        problems.append(f"{label}: job {job!r} must have if: {condition!r} (found {body.get('if')!r}, Issue #349)")
    if body.get("permissions") != permissions:
        problems.append(f"{label}: job {job!r} must have permissions {permissions} exactly "
                        f"(found {body.get('permissions')!r}, Issue #349)")
    for key in sorted(set(body) - keys, key=str):
        problems.append(f"{label}: job {job!r} sets job-level {key!r} (refused; allowed {sorted(keys)}, Issue #349)")
    if "environment" in body and not _plain_environment(body["environment"]):
        problems.append(f"{label}: job {job!r} environment: must be a name, or name and url only (Issue #349)")
    return problems


def _pages_deploy_step_problems(job, body, label):
    """The deploy job runs exactly one step: actions/deploy-pages pinned to a full SHA, with only name, id and uses."""
    steps = body.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        return [f"{label}: job {job!r} must have exactly one step, the deploy action (Issue #349)"]
    step = steps[0]
    problems = [f"{label}: job {job!r} step sets {key!r} (refused, Issue #349)"
                for key in sorted(set(step) - PAGES_DEPLOY_STEP_KEYS, key=str)]
    if not (isinstance(step.get("uses"), str) and PAGES_DEPLOY_USES.fullmatch(step["uses"])):
        problems.append(f"{label}: job {job!r} deploy action must be actions/deploy-pages pinned to a full commit SHA "
                        f"(found {step.get('uses')!r}, Issue #349)")
    return problems


def pages_structure_violations(text, label):
    """Issue #349, for one workflow file (decoded YAML). A Pages publishing action is allowed only in a job whose structure
    is approved here. Split layout (validate, build, deploy): actions/upload-pages-artifact in build (needs: validate,
    permissions exactly contents: read, no id-token) and actions/deploy-pages in deploy (needs: build, the main-only if:,
    permissions exactly pages: write and id-token: write, exactly one SHA-pinned step). Single-job layout (deploy holds
    both the upload and the deploy): its own exact shape. Any other shape, a Pages action outside those jobs, a second deploy
    action, or an unpinned action is refused. Residual: stage and verify placement is checked by
    guard_placement_violations; this checks the job structure, not the commands inside the upload job."""
    try:
        import yaml
    except ImportError:
        return [f"{label}: PyYAML unavailable, Pages structure cannot be checked (Issue #349)"]
    try:
        doc = yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError) as error:
        return [f"{label}: YAML cannot be read, Pages structure cannot be checked ({error}; Issue #349)"]
    return _privilege_problems(doc, label) + _pages_structure_problems(doc, label)


def _write_scopes(perms):
    """Scopes a permissions value grants write to: `write-all` as one token, else every key whose value is `write`."""
    if perms == "write-all":
        return ["write-all"]
    if isinstance(perms, dict):
        return sorted(str(k) for k, v in perms.items() if v == "write")
    return []


# Issue #349 (review): every write grant is refused unless it is listed here for its exact (file, job). Keyed by file and
# job, and each entry is the exact set of scopes allowed there. The entries are the approved Pages deploy job and the
# approved attest jobs in api-source.yml; nothing else holds a write grant.
WRITE_ALLOWANCES = {
    ("pages.yml", "deploy"): {"pages", "id-token"},
    ("api-source.yml", "attest-cdread"): {"id-token", "attestations"},
    ("api-source.yml", "attest-cdread-verdict"): {"id-token", "attestations"},
}


def _privilege_problems(doc, label):
    """Issue #349 (review): pages: write is held only by the deploy job of pages.yml, contents: write by no job in any file
    (no release route is approved), and pages.yml takes only the triggers main has. Checked for every workflow file, at
    the workflow level and per job, so a new job or a top-level grant cannot hold the Pages API unseen."""
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
    holders = [("workflow", doc.get("permissions"))]
    holders += [(job, body.get("permissions")) for job, body in jobs.items() if isinstance(body, dict)]
    problems = []
    name = Path(label).name
    for holder, perms in holders:
        where = "workflow" if holder == "workflow" else f"job {holder!r}"
        allowed = WRITE_ALLOWANCES.get((name, holder), set())
        for scope in _write_scopes(perms):
            if scope not in allowed:
                grant = "write-all" if scope == "write-all" else f"{scope}: write"
                problems.append(f"{label}: {where} grants {grant} (refused unless allowlisted in WRITE_ALLOWANCES, "
                                "Issue #349)")
    # Triggers: every workflow file may use only the triggers main uses; pages.yml may use only its own set.
    on = doc.get("on", doc.get(True))   # YAML 1.1 reads a bare `on:` key as True
    names = set(on) if isinstance(on, (dict, list)) else {on} if isinstance(on, str) else set()
    if Path(label).name == PAGES_FILE:
        allowed = PAGES_ALLOWED_TRIGGERS
    else:
        allowed = WORKFLOW_ALLOWED_TRIGGERS | ({"schedule"} if Path(label).name in SCHEDULE_WORKFLOWS else set())
    extra = sorted(str(n) for n in names - allowed)
    if not names or extra:
        problems.append(f"{label}: triggers {sorted(map(str, names))} refused; only {sorted(allowed)} are allowed "
                        f"(extra {extra}, Issue #349)")
    # Permissions: without a workflow-level block, every job needs its own, or it inherits the repository default scope.
    if "permissions" not in doc:
        for job, body in jobs.items():
            if isinstance(body, dict) and "permissions" not in body:
                problems.append(f"{label}: job {job!r} has no permissions and the workflow has none (repository default "
                                "GITHUB_TOKEN scope would apply, Issue #349)")
    return problems


def _pages_structure_problems(doc, label):
    found = list(_pages_uses(doc))
    if not found:
        return []
    if Path(label).name != PAGES_FILE:
        return [f"{label}: Pages publish action outside {PAGES_FILE} is refused (Issue #349)"]
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or any(not isinstance(body, dict) for body in jobs.values()):
        return [f"{label}: Pages publish action without a mapping of jobs is refused (Issue #349)"]
    per_job = {job: uses for job, body in jobs.items() if (uses := list(_pages_uses(body)))}
    if sum(len(v) for v in per_job.values()) != len(found):
        return [f"{label}: Pages publish action outside a job step is refused (Issue #349)"]
    if set(jobs) == PAGES_SPLIT_JOBS:
        return _pages_split_problems(jobs, per_job, label)
    return _pages_single_problems(jobs, per_job, label)


def _pages_split_problems(jobs, per_job, label):
    upload, deploy = per_job.get("build", []), per_job.get("deploy", [])
    if per_job.get("validate") or set(per_job) - {"build", "deploy"} or len(upload) != 1 or len(deploy) != 1:
        return [f"{label}: the split layout must hold exactly one upload in build and exactly one deploy in deploy, and no "
                f"Pages action elsewhere (found {per_job}, Issue #349)"]
    problems = []
    # validate holds no Pages action, but it must not hold pages: write or id-token: write either (Issue #349, review).
    problems += _pages_job_problems("validate", jobs["validate"], label, None, None, PAGES_BUILD_PERMISSIONS,
                                    PAGES_BUILD_KEYS)
    if not PAGES_UPLOAD_USES.fullmatch(upload[0]):
        problems.append(f"{label}: build upload-pages-artifact must be pinned to a full commit SHA (Issue #349)")
    problems += _pages_job_problems("build", jobs["build"], label, "validate", None, PAGES_BUILD_PERMISSIONS,
                                    PAGES_BUILD_KEYS)
    problems += _pages_job_problems("deploy", jobs["deploy"], label, "build", PAGES_DEPLOY_IF, PAGES_DEPLOY_PERMISSIONS,
                                    PAGES_JOB_KEYS)
    problems += _pages_deploy_step_problems("deploy", jobs["deploy"], label)
    return problems


def _pages_single_problems(jobs, per_job, label):
    uses = per_job.get("deploy", [])
    if set(per_job) - {"deploy"} or len(uses) != 2:
        return [f"{label}: the single-job layout must hold one upload and one deploy in job 'deploy' and no Pages action "
                f"elsewhere (found {per_job}, Issue #349)"]
    if sum(bool(PAGES_DEPLOY_USES.fullmatch(u)) for u in uses) != 1 or not all(
            PAGES_DEPLOY_USES.fullmatch(u) or PAGES_UPLOAD_USES.fullmatch(u) for u in uses):
        return [f"{label}: the single-job layout needs exactly one deploy action and one upload, both pinned to full commit "
                "SHAs (Issue #349)"]
    return _pages_job_problems("deploy", jobs["deploy"], label, "validate", PAGES_DEPLOY_IF, PAGES_LEGACY_PERMISSIONS,
                               PAGES_JOB_KEYS)


def check_guard_placement(root=ROOT, partial_fixtures=False):
    """Static placement layer (run by make check and by the trusted copy): the preamble and stage/verify steps must
    surround every upload step, in the same job, exactly as written here.

    It scans the same files with the same parser as check_upload_artifacts, so a publication route the parser cannot
    see through (release assets, `gh release`, reusable workflows) fails here too. Gating on "this file contains an
    upload-artifact uses: line" left those routes uninspected by --check-placement while check_upload_artifacts
    refused them (review finding A). A file with no upload step and no such route is not a publication and passes.

    partial_fixtures=True is for tests only: a fixture api-source.yml may hold part of the workflow. main() never sets it,
    so the real workflow must keep both signing jobs and both guard jobs (Issue #337 round 11).
    """
    base = Path(root)
    problems = []
    files = _workflow_files(base)
    if not files:
        raise ValueError(f"no workflow or action files under {WORKFLOWS}/ or {ACTIONS}/ to check; "
                         "refusing to pass an empty placement check")
    for workflow in files:
        label = workflow.relative_to(base).as_posix()
        text = workflow.read_text(encoding="utf-8")
        try:
            steps = upload_steps(text)
        except ValueError as error:   # an unsupported publishing route or an unparseable upload step
            raise ValueError(f"{label}: {error}") from error
        if steps:
            problems += guard_placement_violations(text, label)
        else:
            problems += scope_key_violations(text, label)   # a file with an upload step gets it in guard_placement_violations
        problems += publish_violations(text, label)
        problems += pages_structure_violations(text, label)
        problems += yaml_integrity_violations(text, label)
        problems += attest_input_violations(text, label)
        problems += attest_download_violations(text, label, partial_fixtures)
        problems += attest_condition_violations(text, label)
        problems += signing_violations(text, label)
        problems += rom_upload_violations(text, label)
    if problems:
        raise ValueError("artifact guard placement: " + "; ".join(problems))
    print("PASS artifact guard placement: trusted preamble, staged paths, stage and verify before every upload")


def _continued_name(lines, start, end, dash):
    """True if a `name:` key of this step has a continuation line (multi-line plain or quoted scalar, or a block)."""
    for i in range(start, end):
        if not _live(lines[i]):
            continue
        body = lines[i].lstrip()
        if i == start:
            body = body[2:].lstrip() if body.startswith("- ") else body
        elif _indent(lines[i]) != dash + 2:
            continue
        if not re.match(r"""["']?name["']?\s*:""", body):
            continue
        for j in range(i + 1, end):
            if _live(lines[j]):
                if _indent(lines[j]) > dash + 2:
                    return True
                break
    return False


def _step_key_pairs(lines, start, end, dash):
    """(key, raw value) for each top-level key of a step (its dash line and the lines at exactly dash+2), in order."""
    pairs = []
    for key in _step_keys(lines, start, end, dash):
        name, sep, value = key.partition(":")
        pairs.append((name.strip().strip("\"'"), value.strip() if sep else ""))
    return pairs


def _with_pairs(lines, start, end, dash):
    """(key, value) pairs under the step's own top-level `with:` key, or None when it is not a plain block.

    Only the step's own top-level keys are read, so a `with:`-looking line inside a block scalar (e.g. a `name: |`
    body) cannot stand in for the real one, and a `with:` carrying an inline value (a flow mapping or an anchor/alias)
    is refused rather than guessed. [] when the step has no with: at all.
    """
    header = None
    for i in range(start, end):
        if not _live(lines[i]):
            continue
        if i == start:
            body = lines[i].lstrip()
            body = body[2:].strip() if body.startswith("- ") else body.strip()
        elif _indent(lines[i]) == dash + 2:
            body = lines[i].strip()
        else:
            continue
        match = re.match(r"""["']?with["']?\s*:\s*(.*)$""", body)
        if not match:
            continue
        if match.group(1).split("#")[0].strip():   # flow mapping, alias or any inline value: refuse rather than guess
            return None
        header = i
        break
    if header is None:
        return []
    pairs = []
    for j in range(header + 1, end):
        if not _live(lines[j]):
            continue
        if _indent(lines[j]) <= dash + 2:
            break
        text = lines[j].strip()
        key, sep, value = text.partition(":")
        if not sep or not key.strip() or value.split("#")[0].strip().startswith(("*", "&")):
            return None
        pairs.append((key.strip().strip("\"'"), value.split("#")[0].strip().strip("\"'")))
    return pairs


def _is_trusted_checkout(lines, step, dash):
    start, end = step[0], step[1]
    pairs = _step_key_pairs(lines, start, end, dash)
    names = [key for key, _ in pairs]
    if len(set(names)) != len(names):
        return False
    values = dict(pairs)
    uses = values.get("uses", "").split("#")[0].strip().strip("\"'")
    if not CHECKOUT_PIN_RE.fullmatch(uses):
        return False
    with_pairs = _with_pairs(lines, start, end, dash)
    if with_pairs is None:
        return False
    with_names = [key for key, _ in with_pairs]
    # A repeated with: key (e.g. `ref: main` then `ref: <pull request>`) could override the trusted value if the
    # YAML parser keeps the last duplicate, so duplicates are refused along with out-of-allowlist keys.
    if len(set(with_names)) != len(with_names) or not set(with_names) <= TRUSTED_CHECKOUT_WITH:
        return False
    with_values = dict(with_pairs)
    return all(with_values.get(key) == value for key, value in TRUSTED_CHECKOUT_WANT.items())


def _is_checkout(lines, step, dash):
    """The guard job's first step: a pinned checkout of the pull-request workflows (not main, not another repository).

    Its with: may not carry ref:, repository: or token:/ssh-key:, any of which would make --check-placement read a
    different workflow set from the one GitHub actually runs.
    """
    start, end = step[0], step[1]
    pairs = _step_key_pairs(lines, start, end, dash)
    names = [key for key, _ in pairs]
    if len(set(names)) != len(names):
        return False
    values = dict(pairs)
    uses = values.get("uses", "").split("#")[0].strip().strip("\"'")
    if not CHECKOUT_PIN_RE.fullmatch(uses):
        return False
    with_pairs = _with_pairs(lines, start, end, dash)
    if with_pairs is None:
        return False
    with_names = [key for key, _ in with_pairs]
    return len(set(with_names)) == len(with_names) and set(with_names) <= PR_CHECKOUT_WITH


def _is_py_resolve(lines, step, dash):
    body = [l.strip() for l in lines[step[0]:step[1]] if _live(l)]
    return "id: pyresolve" in body and _run_lines(lines, step[0], step[1], dash) == [PY_RESOLVE_RUN]


def _job_needs(lines, job):
    """The job ids listed in `needs:` of one job (inline, flow list or block list). [] when there is none."""
    start = next((i for i, l in enumerate(lines) if _live(l) and _job_name(l) == job), None)
    if start is None:
        return []
    needs = []
    i = start + 1
    while i < len(lines) and not (_live(lines[i]) and (_job_name(lines[i]) is not None or _indent(lines[i]) == 0)):
        l = lines[i]
        if _live(l) and _indent(l) == 4 and l.strip().startswith("needs:"):
            value = l.strip()[len("needs:"):].strip()
            if value.startswith("["):
                needs += [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()]
            elif value:
                needs.append(value.strip("\"'"))
            else:
                j = i + 1
                while j < len(lines) and (not _live(lines[j]) or _indent(lines[j]) >= 6):
                    if _live(lines[j]) and lines[j].strip().startswith("- "):
                        needs.append(lines[j].strip()[2:].strip().strip("\"'"))
                    j += 1
        i += 1
    return needs


def _step_body(lines, step):
    """The live lines of one step, stripped (the dash line first)."""
    return [l.strip() for l in lines[step[0]:step[1]] if _live(l)]


def _uses_action(lines, start, end, dash):
    """The actions/* action of a step's own top-level `uses:` key (e.g. 'checkout'), or None.

    Only the step's top-level keys are read, so a `uses:`-looking line inside a run: block cannot masquerade as an
    allowed action and skip the trusted-run checks.
    """
    for key in _step_keys(lines, start, end, dash):
        match = USES_KEY_RE.match(key)
        if not match:
            continue
        value = match.group(1).split("#")[0].strip().strip("\"'")
        found = ACTION_PATH_RE.match(value)
        return found.group(1) if found else None
    return None


def _downloads(lines, steps, job):
    """(step, artifact name or None, path is the intake root) for each download-artifact step of one job."""
    found = []
    for s in steps:
        if s[2] != job:
            continue
        body = _step_body(lines, s)
        if not any(re.search(r"uses:\s*actions/download-artifact@", b) for b in body):
            continue
        names = [b.split(":", 1)[1].strip() for b in body[1:] if b.startswith("name:")]
        paths = [b for b in body if b.startswith("path:")]
        found.append((s, names[0] if len(names) == 1 else None, paths == [f"path: {INTAKE_ROOT}"]))
    return found


def _nested_artifact_name(lines, step):
    """The artifact name of an upload or download step: its nested `name:` (under with:), or None."""
    body = _step_body(lines, step)
    names = [b.split(":", 1)[1].strip() for b in body[1:] if b.startswith("name:")]
    return names[0] if len(names) == 1 else None


def _upload_kind(name, patterns, artifact):
    """intermediate: a build job's hand-off to the guard job (artifact name intermediate-*). final-intake: publication
    from the guard job's intake (downloaded intermediate, staged). final-workspace: publication of a staged checkout
    tree (pages). None: refused."""
    if artifact and artifact.startswith("intermediate-") and all(p.startswith("intermediate/") for p in patterns):
        return "intermediate"
    if all(p.startswith(INTAKE_STAGE) for p in patterns):
        return "final-intake"
    if all(p.startswith(STAGE_DIR + "/") for p in patterns):
        return "final-workspace"
    return None


def _guard_text(kind, workflow, name, what):
    """The exact run text of the stage or verify step for one upload, by the root it works on."""
    py = INTAKE_PY if kind == "final-intake" else TRUSTED_PY
    return f'{py} --{what} {workflow} "{name}"'


def _allowed_guard_run(run, finals, workflow):
    """Run lines a guard job may contain: the python resolve, the placement check, and the stage/verify of its uploads."""
    if run in ([PY_RESOLVE_RUN], [CHECK_TEXT]):
        return True
    return any(run in ([_guard_text(u["kind"], workflow, u["name"], "stage")],
                       [_guard_text(u["kind"], workflow, u["name"], "verify")]) for u in finals)


# Issue #337 round 14. A step key that changes the environment or shell a step runs in is refused in every workflow and
# composite action, unless listed here. env: is allowed per variable: STEP_EXCEPTIONS, keyed by (file, job id or 'runs',
# exact step name as YAML parses it, "env", variable name). shell: is allowed per value: STEP_SHELL_ALLOWED. working-directory:
# has no exception. The reasons are for review; the guard checks only that an entry exists.
STEP_ENV_KEYS = frozenset({"env", "shell", "working-directory"})
RUN_PATH_CHANGE_RE = re.compile(r"GITHUB_PATH|GITHUB_ENV|BASH_ENV|_runner_file|set_env|add_path|(?:^|[\s;&|(])(?:export\s+)?PATH\+?=")
# Issue #337 round 15. Matched on run: text with quotes and backslashes removed, so "GITHUB""_ENV" and G\ITHUB_ENV are read as GITHUB_ENV.
INDIRECT_RE = re.compile(r"\$\{!|\b(?:declare|typeset|local)\b[^;&|\n]*\s-\w*n")          # ${!X} indirection, nameref flags
DYNAMIC_GLOB_RE = re.compile(r"\$[^\s;&|]*[*?\[]|[*?\[][^\s;&|]*\$")                      # a glob beside a variable: a runner path found by pattern
WRITE_TARGET_RE = re.compile(r"(?:&?>>?\|?&?|\btee\b(?:\s+-\w+)*)\s*([^\s;&|()<>]+)")       # the word after a redirection (>&F too) or tee
TARGET_DYNAMIC_RE = re.compile(r"[$`*?\[{]")
EXPANSION_RE = re.compile(r"[$`{]")     # run-time expansion; a glob alone is only a name pattern
GLOB_RE = re.compile(r"[*?\[]")
# Issue #337 round 16. A file-copying command in command position (cp, mv, install, dd, tee); its arguments are checked
# token by token, so a destination assembled at run time (cp a $D, tee --append $F, dd of=$D) is refused.
# Command position: start of text, after ; & | ( { ! or a newline, after a keyword (if, then, do, sudo...), with an optional
# path prefix (/bin/cp). Leading NAME=value assignments are skipped (FOO=1 cp), and so is the ) of a case arm. A wrapper may
# take an option and one argument (sudo -u root, nice -n 5) but not a writer name; the (?!-) keeps this from backtracking.
FILE_WRITER_RE = re.compile(r"(?:(?:^|[;&|({!)\n])\s*(?:\w+=\S+\s+)*"
                            r"|\b(?:then|do|else|if|elif|while|until|exec|command|xargs|env|time|nice|nohup|sudo)\b"
                            r"(?:\s+-\S+(?:\s+(?!-)(?!(?:cp|mv|install|dd|tee)\b)\S+)?|\s+\w+=\S+)*\s+)"
                            r"(?:[\w./-]*/)?(cp|mv|install|dd|tee)\b([^;&|\n()<>]*)")
TARGET_OK_RE = re.compile(r"\$GITHUB_OUTPUT|\$GITHUB_STEP_SUMMARY|\$RUNNER_TEMP/[^$`*?\[{]*")  # the only variable targets allowed
_GH_TOKEN = "GH_TOKEN: the gh CLI reads its token from this variable; it selects no program, path or shell"
STEP_SHELL_ALLOWED = {
    "bash": "shell: bash resolves bash on PATH, as the runner's default shell does, and adds pipefail; PATH and "
            "BASH_ENV cannot be set by any allowed step (env: and run: are checked)",
}
_SRC = ".github/workflows/"
STEP_EXCEPTIONS = {
    (_SRC + "api-source.yml", "verify-cdread-attestation",
     "Verify CD-read attestation against main (gh + tools/evidence_provenance.py)", "env", "GH_TOKEN"): _GH_TOKEN,
    (_SRC + "api-source.yml", "verify-cdread-attestation",
     "Produce CD_READ verdict (Issue", "env", "GH_TOKEN"): _GH_TOKEN,   # YAML ends the name at " #"
    (_SRC + "api-source.yml", "verify-cdread-verdict", "Verify verdict attestation against main",
     "env", "GH_TOKEN"): _GH_TOKEN,
    (_SRC + "ci.yml", "verify",
     "Provenance guard on changed content (list edits need a verified maintainer approval)", "env", "GH_TOKEN"): _GH_TOKEN,
    (_SRC + "nightly.yml", "plan", "Compare exact main SHA against last successful built artifact",
     "env", "GH_TOKEN"): _GH_TOKEN,
    (_SRC + "nightly.yml", "build", "Record candidate success state", "env", "SOURCE_SHA"):
        "SOURCE_SHA is a job output (needs.plan.outputs.source_sha) passed to a python script; it names no program",
}


def _step_problems(workflow, where, step):
    """Refusals for one step: a step env:, shell: or working-directory: key without an exception, and a run: text that
    changes the PATH of later steps (GITHUB_PATH, GITHUB_ENV) or sets BASH_ENV or PATH. Issue #337 round 14."""
    if not isinstance(step, dict):
        return [f"{workflow}: {where} has a step that is not a mapping (Issue #337 round 14)"]
    name, problems = step.get("name"), []
    for key in step:
        if str(key).lower() not in STEP_ENV_KEYS:
            continue
        value = step[key]
        if key == "env" and isinstance(value, dict):
            refused = [str(n) for n in value if (workflow, where, str(name), "env", str(n)) not in STEP_EXCEPTIONS]
            if refused:
                problems.append(f"{workflow}: {where} step {name!r} sets env {refused} (refused; it can change PATH, "
                                "BASH_ENV or similar; add an exception with a reason to STEP_EXCEPTIONS, Issue #337 round 14)")
        elif key == "shell" and isinstance(value, str) and value in STEP_SHELL_ALLOWED:
            continue
        else:
            problems.append(f"{workflow}: {where} step {name!r} sets {key!r} (refused; it can change the program or the "
                            "directory a step runs in, Issue #337 round 14)")
    run = step.get("run")
    if run is not None and not isinstance(run, str):
        problems.append(f"{workflow}: {where} step {name!r} has a run: that is not a string (refused: it cannot be read)")
    elif isinstance(run, str):
        reason = _run_write_problem(run)
        if reason:
            problems.append(f"{workflow}: {where} step {name!r} run: {reason} "
                            "(refused; it changes the PATH of later steps, Issue #337 round 14, 15)")
    return problems


def _run_write_problem(run):
    """Issue #337 round 15. The reason a run: text may write GITHUB_ENV, GITHUB_PATH, BASH_ENV, PATH or a runner file
    command, or None. Refused: those names (also split by quotes or backslashes); an indirect expansion ${!X} or a nameref;
    a glob beside a variable; and a redirection or tee target with a $, glob, backtick or GitHub expression unless it is
    $GITHUB_OUTPUT, $GITHUB_STEP_SUMMARY or a $RUNNER_TEMP/ path without further expansion; and any argument of cp, mv,
    install, dd or tee that has a $, glob, backtick or brace unless it is one of those allowed forms (Issue #337 round 16).
    This is a pattern check, not a parser. NOT GUARANTEED (not refused, not claimed safe):
    - eval, bash -c, and $(...) or backticks that build a command at run time;
    - source or . of a file, and in-repo scripts and make targets (their own run text is not read);
    - values set by read or printf -v that are later run as a command or passed to eval (values are not traced);
    - LD_PRELOAD, PYTHONPATH and other loader or interpreter-path exports that change what runs without touching PATH;
    - a file-copy command behind a wrapper not listed in FILE_WRITER_RE (timeout 5 cp, busybox cp, a command in a variable);
    - other writers that take a destination: curl -o, wget -O, ln, sed -i, tar -C, and any program that writes its own file."""
    run = run.replace("\\\n", "")  # bash joins a backslash-newline into one word: "_runner_\<newline>file" is one path
    plain = re.sub(r"[\"'\\]", "", run)
    if RUN_PATH_CHANGE_RE.search(plain):
        return "writes GITHUB_PATH, GITHUB_ENV, BASH_ENV, PATH or a runner file command"
    if INDIRECT_RE.search(plain):
        return "uses an indirect expansion (${!X}) or a nameref, which can name GITHUB_ENV at run time"
    if DYNAMIC_GLOB_RE.search(plain):
        return "uses a glob beside a variable, which can name a runner file at run time"
    for target in WRITE_TARGET_RE.findall(plain):
        if TARGET_DYNAMIC_RE.search(target) and not TARGET_OK_RE.fullmatch(target):
            return f"writes to a target assembled at run time ({target!r})"
    for command, args in FILE_WRITER_RE.findall(plain):
        words = args.split()
        if command in ("cp", "mv", "install"):
            # Options may come after operands (GNU), so any run-time expansion in any word is refused. A glob is refused only
            # in a destination: the last word, or a -t/--t... option and the word or attached text after it. A glob in a
            # source (cp --parents build/*.pce intermediate/) only reads. mv deletes its sources, so a glob there removes
            # matching files in the repo, not a GITHUB_ENV path; that is accepted and not checked here.
            targets = set(words[-1:])
            for i, word in enumerate(words):
                if word.startswith("--t") or (word.startswith("-") and not word.startswith("--") and "t" in word):
                    targets.add(word)
                    if i + 1 < len(words):
                        targets.add(words[i + 1])
            for word in words:
                if EXPANSION_RE.search(word) and not TARGET_OK_RE.fullmatch(word):
                    return f"copies or writes to an argument assembled at run time ({word!r}), which can name GITHUB_ENV"
            for word in targets:
                if GLOB_RE.search(word) and not TARGET_OK_RE.fullmatch(word):
                    return f"writes to a destination given by a glob ({word!r})"
            continue
        for word in words:
            if TARGET_DYNAMIC_RE.search(word) and not TARGET_OK_RE.fullmatch(word):
                return f"copies or writes to an argument assembled at run time ({word!r}), which can name GITHUB_ENV"
    return None


def _yaml_unreadable(text):
    """True when PyYAML cannot read text. Without PyYAML this is False, and scope_key_violations then refuses the file."""
    try:
        import yaml
    except ImportError:
        return False
    try:
        yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError):
        return True
    return False


def scope_key_violations(text, workflow):
    """Issue #337 round 10/12/13/14. Refused, decided on the parsed YAML (indentation, quoting, flow style, key case and
    order do not matter; keys are compared case-insensitively):
    - top-level env, defaults, container, services (they can change the PATH, RUNNER_TEMP or shell of a step);
    - job-level env, defaults, container, services in every job;
    - in every step of every job (or of runs.steps in a composite action), an env, shell or working-directory key, unless
      STEP_EXCEPTIONS names it, or a shell: value other than STEP_SHELL_ALLOWED; and a run: text that writes GITHUB_PATH,
      GITHUB_ENV, BASH_ENV or PATH;
    - a workflow whose jobs: is not a mapping, a job that is not a mapping, and a steps: that is not a list (fail closed).
    Applies to every workflow and composite action file, with or without an upload step. A file that cannot be parsed is
    refused here too (round 14), not only by yaml_integrity_violations."""
    try:
        import yaml
    except ImportError:
        return [f"{workflow}: PyYAML unavailable, scope keys cannot be checked (Issue #337 round 13)"]
    try:
        doc = yaml.load(text, Loader=_strict_loader())
    except (yaml.YAMLError, ValueError) as error:
        return [f"{workflow}: YAML cannot be read, scope keys cannot be checked ({error}; Issue #337 round 14)"]
    if not isinstance(doc, dict):
        return [f"{workflow}: the file is not a mapping, scope keys cannot be checked (Issue #337 round 14)"]
    problems = [f"{workflow}: top-level {key} is refused (can change the guard's PATH or RUNNER_TEMP)"
                for key in sorted(doc, key=str) if str(key).lower() in SCOPE_KEYS_REFUSED]
    if workflow.startswith(ACTIONS + "/"):
        runs = doc.get("runs")
        if not isinstance(runs, dict):
            return problems + [f"{workflow}: runs: must be a mapping, scope keys cannot be checked (Issue #337 round 14)"]
        scopes = [("runs", runs.get("steps"))]
    else:
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            return problems + [f"{workflow}: jobs: must be a mapping, found {type(jobs).__name__}; scope keys cannot be "
                               "checked (Issue #337 round 14)"]
        scopes = []
        for job, body in jobs.items():
            if not isinstance(body, dict):
                problems.append(f"{workflow}: job {job!r} must be a mapping (Issue #337 round 14)")
                continue
            for key in sorted(body, key=str):
                if str(key).lower() in SCOPE_KEYS_REFUSED:
                    problems.append(f"{workflow}: job-level {key} in job {job!r} is refused "
                                    "(can change the guard's PATH or RUNNER_TEMP)")
            scopes.append((job, body.get("steps")))
    for where, steps in scopes:
        if steps is None:
            continue
        if not isinstance(steps, list):
            problems.append(f"{workflow}: {where} steps: must be a list (Issue #337 round 14)")
            continue
        for step in steps:
            problems += _step_problems(workflow, where, step)
    return problems


def guard_placement_violations(text, workflow):
    """Two kinds of job. A build job uploads only intermediate/ artifacts and runs no guard. A guard job publishes
    only staged paths: it needs the build job that produced its intermediate artifact, and its steps are the trusted
    checkout, the python resolve, checkouts, downloads, the placement check, stage and verify, the uploads and the
    Pages steps. Nothing else runs there, so no build code runs in it. Each upload is preceded by its stage and verify
    steps, and the intake download must be the intermediate artifact of a needed job. Anything unparseable raises
    ValueError (fail closed).
    """
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", workflow):
        raise ValueError(f"unsafe workflow path {workflow!r} (only letters, digits, . _ / - are allowed in the guard call)")
    lines = text.splitlines()
    if any(re.match(r"\s*steps\s*:\s*\[", l) for l in lines):
        raise ValueError(f"{workflow}: flow-style steps unsupported")
    job_names = [_job_name(l) for l in lines if _live(l) and _job_name(l) is not None]
    dup = next((n for n in job_names if job_names.count(n) > 1), None)
    if dup is not None:
        raise ValueError(f"{workflow}: duplicate job name {dup!r}")
    has_jobs = any(_live(l) and l.startswith("jobs:") for l in lines)
    # An unreadable file gets no scope check here: check_guard_placement refuses it in yaml_integrity_violations, and
    # scope_key_violations itself refuses it too (Issue #337 round 14). Only parsed structure is read.
    problems = [] if _yaml_unreadable(text) else scope_key_violations(text, workflow)
    names = [name for name, _ in upload_steps(text)]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"{workflow}: upload step name {name!r} is used more than once (the binding check would see one)")
    uploads = []
    for uses, line in enumerate(lines):
        match = USES_RE.search(line) if _live(line) else None
        if not match:
            continue
        key_indent = _indent(line) + (2 if line.lstrip().startswith("- ") else 0)
        dash = next((j for j in range(uses, -1, -1) if _live(lines[j]) and lines[j].lstrip().startswith("- ")
                     and _indent(lines[j]) + 2 <= key_indent), None)
        if dash is None:
            problems.append(f"{workflow}: line {uses + 1} is an upload-artifact reference outside a list item")
            continue
        dash_indent = _indent(lines[dash])
        steps = _step_ranges(lines, dash_indent)
        index = next((n for n, s in enumerate(steps) if s[0] <= uses < s[1]), None)
        if index is None:
            problems.append(f"{workflow}: line {uses + 1} is an upload-artifact reference outside a parsed step")
            continue
        up = steps[index]
        name, patterns = _step_patterns(lines, uses, match.start())
        label = f"{workflow}: {name!r}"
        if not name or any(c in NAME_FORBIDDEN for c in name):
            problems.append(f"{label}: upload step name must be non-empty and contain none of the characters {NAME_FORBIDDEN!r} "
                            "(the guard calls embed it in double quotes)")
            continue
        artifact = _nested_artifact_name(lines, up)
        kind = _upload_kind(name, patterns, artifact)
        if kind is None:
            problems.append(f"{label}: upload paths must be intermediate/ (build job) or staged (guard job); found {patterns}")
            continue
        up_keys = _key_names(_step_keys(lines, up[0], up[1], dash_indent))
        if len(set(up_keys)) != len(up_keys) or not set(up_keys) <= UPLOAD_STEP_KEYS:
            problems.append(f"{label}: upload step may only have name, uses and with, each once (found {up_keys})")
        if _continued_name(lines, up[0], up[1], dash_indent):
            problems.append(f"{label}: upload step name continues on the next line (the YAML name would differ from the guard call)")
        if has_jobs and up[2] is None:
            problems.append(f"{label}: upload step is outside any job")
            continue
        uploads.append({"name": name, "artifact": artifact, "kind": kind, "label": label, "step": up, "steps": steps,
                        "dash": dash_indent, "job": up[2], "patterns": patterns})
    for job in sorted({u["job"] for u in uploads}, key=str):
        job_uploads = [u for u in uploads if u["job"] == job]
        finals = [u for u in job_uploads if u["kind"].startswith("final")]
        inters = [u for u in job_uploads if u["kind"] == "intermediate"]
        steps = job_uploads[0]["steps"]
        dash = job_uploads[0]["dash"]
        if finals and inters:
            problems.append(f"{workflow}: job {job!r} both builds (intermediate upload) and publishes (final upload); split them")
            continue
        if inters:
            if any(_live(l) and "artifact_guard.py" in l for s in steps if s[2] == job for l in lines[s[0]:s[1]]):
                problems.append(f"{workflow}: build job {job!r} runs the guard; the guard belongs in the job that needs it")
            for u in inters:
                consumers = [g for g in set(job_names) if g != job and job in _job_needs(lines, g)
                             and any(art == u["artifact"] for _, art, _ in _downloads(lines, steps, g))]
                if not consumers:
                    problems.append(f"{u['label']}: no job that needs {job!r} downloads this intermediate artifact")
            continue
        intake = any(u["kind"] == "final-intake" for u in finals)
        needed = _job_needs(lines, job)
        if intake and not needed:
            problems.append(f"{workflow}: guard job {job!r} has no needs: (it must follow the build job that produced its intermediate artifact)")
        job_steps = sorted((s for s in steps if s[2] == job), key=lambda s: s[0])
        for index, s in enumerate(job_steps):
            body = _step_body(lines, s)
            names = _key_names(_step_keys(lines, s[0], s[1], dash))
            action = _uses_action(lines, s[0], s[1], dash)
            if action:
                # The trusted copy is fetched into .trusted-main by job_steps[1]. Any later action that writes files
                # could overwrite it with the pull-request content and run it as the guard, so the only checkouts a
                # guard job may have are the first two steps (the pull-request checkout and the trusted checkout).
                if len(set(names)) != len(names):
                    problems.append(f"{workflow}: guard job {job!r}: a step key is repeated; found {names}")
                if "run" in names:
                    problems.append(f"{workflow}: guard job {job!r}: a step may not have both uses: and run:")
                if action == "checkout" and index > 1:
                    problems.append(f"{workflow}: guard job {job!r}: only the first two steps may be checkouts; "
                                    f"step {index + 1} is another checkout (it could overwrite .trusted-main)")
                if action == "download-artifact" and not intake:
                    problems.append(f"{workflow}: guard job {job!r}: a job that publishes from the checkout must not "
                                    f"download an artifact")
                if action in ("configure-pages", "deploy-pages") and not set(names) <= PAGES_ACTION_KEYS:
                    problems.append(f"{workflow}: guard job {job!r}: a {action} step may only have "
                                    f"{sorted(PAGES_ACTION_KEYS)}; found {names}")
                continue
            keys = _step_keys(lines, s[0], s[1], dash)
            if len(set(names)) != len(names) or not set(names) <= RUN_STEP_KEYS or not _shell_ok(keys):
                problems.append(f"{workflow}: guard job {job!r}: a trusted run step may only have {sorted(RUN_STEP_KEYS)} "
                                f"(shell: bash), each once; found {names}")
            run = _run_lines(lines, s[0], s[1], dash)
            if not run or not _allowed_guard_run(run, finals, workflow):
                problems.append(f"{workflow}: guard job {job!r} may only run trusted steps; found step {body[0]!r}")
        if len(job_steps) < 3 or not _is_checkout(lines, job_steps[0], dash):
            problems.append(f"{workflow}: guard job {job!r}: the first step must be a checkout (before the trusted checkout, whose path must not be cleaned)")
        if len(job_steps) < 3 or not _is_trusted_checkout(lines, job_steps[1], dash):
            problems.append(f"{workflow}: guard job {job!r}: the second step must be the trusted main checkout (ref: main into .trusted-main)")
        if len(job_steps) < 3 or not _is_py_resolve(lines, job_steps[2], dash):
            problems.append(f"{workflow}: guard job {job!r}: the third step must resolve python3 (id: pyresolve)")
        for s in job_steps[:3]:
            keys = _key_names(_step_keys(lines, s[0], s[1], dash))
            if not set(keys) <= PREAMBLE_STEP_KEYS or len(set(keys)) != len(keys):
                problems.append(f"{workflow}: guard job {job!r}: a trusted preamble step may only have {sorted(PREAMBLE_STEP_KEYS)}, each once (found {keys})")
        for u in finals:
            for expected, what in ((_guard_text(u["kind"], workflow, u["name"], "stage"), "stage"),
                                   (_guard_text(u["kind"], workflow, u["name"], "verify"), "verify"),
                                   (CHECK_TEXT, "placement check")):
                count = sum(_run_lines(lines, s[0], s[1], dash) == [expected] for s in job_steps)
                if count != 1:
                    problems.append(f"{u['label']}: the guard job must contain exactly one trusted {what} run (found {count})")
        first_final = min(u["step"][0] for u in finals)
        if not any(s[0] < first_final and _run_lines(lines, s[0], s[1], dash) == [CHECK_TEXT] for s in job_steps):
            problems.append(f"{workflow}: guard job {job!r}: the trusted placement check must run before the first upload")
        for u in finals:
            pos = next((p for p, s in enumerate(job_steps) if s == u["step"]), None)
            if pos is None or pos < 2:
                problems.append(f"{u['label']}: the stage and verify steps must be the two steps immediately before the upload")
                continue
            for step_range, what, before in ((job_steps[pos - 1], "verify", "upload step"),
                                              (job_steps[pos - 2], "stage", "verify step")):
                expected = _guard_text(u["kind"], workflow, u["name"], what)
                run = _run_lines(lines, step_range[0], step_range[1], dash)
                keys = _step_keys(lines, step_range[0], step_range[1], dash)
                if not _guard_is_bare(run, expected, keys):
                    problems.append(f"{u['label']}: the {what} step is not the step immediately before the {before} "
                                    f"(or its run: is not the trusted {what} command)")
                names_of_keys = _key_names(keys)
                if len(set(names_of_keys)) != len(names_of_keys) or not set(names_of_keys) <= GUARD_STEP_KEYS:
                    problems.append(f"{u['label']}: the {what} step may only have name, run and shell, each once (found {names_of_keys})")
                if _continued_name(lines, step_range[0], step_range[1], dash):
                    problems.append(f"{u['label']}: the {what} step name continues on the next line")
        if intake:
            downloads = _downloads(lines, steps, job)
            if not downloads:
                problems.append(f"{workflow}: guard job {job!r} downloads no intermediate artifact")
            stage_starts = [s[0] for s in job_steps
                            if any(_run_lines(lines, s[0], s[1], dash) == [_guard_text(u["kind"], workflow, u["name"], "stage")]
                                   for u in finals)]
            first_stage = min(stage_starts) if stage_starts else None
            for s, art, path_ok in downloads:
                if art is None or not path_ok:
                    problems.append(f"{workflow}: guard job {job!r}: a download must name one artifact and use path: {INTAKE_ROOT}")
                    continue
                if first_stage is not None and s[0] > first_stage:
                    problems.append(f"{workflow}: guard job {job!r}: the download of {art!r} must come before stage")
                if not any(u["kind"] == "intermediate" and u["artifact"] == art and u["job"] in needed for u in uploads):
                    problems.append(f"{workflow}: guard job {job!r} downloads {art!r}, which no needed job uploads")
            downloaded = {art for _, art, _ in downloads}
            for u in uploads:
                if u["kind"] == "intermediate" and u["job"] in needed and u["artifact"] not in downloaded:
                    problems.append(f"{workflow}: guard job {job!r} does not download the intermediate artifact {u['artifact']!r}")
    return problems


def _entry_files(path):
    """The regular files under a matched entry (the entry itself when it is a file)."""
    if path.is_dir() and not path.is_symlink():
        return sorted(Path(directory, name) for directory, _, names in os.walk(path) for name in names)
    return [path]


def _staged_location(base, path):
    """Where a matched file lives inside the staging directory, mirroring its place under the checkout or runner temp."""
    try:
        return f"{STAGE_DIR}/{path.relative_to(base).as_posix()}"
    except ValueError:
        temp = Path(os.environ["RUNNER_TEMP"])
        return f"{STAGE_DIR}/_runner_temp/{path.relative_to(temp).as_posix()}"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _named_step_patterns(workflows_base, workflow, step):
    found = [patterns for name, patterns in upload_steps((workflows_base / workflow).read_text(encoding="utf-8"))
             if name == step]
    if len(found) != 1:
        raise ValueError(f"{workflow}: expected exactly one upload-artifact step named {step!r}, found {len(found)}")
    if any(not (p.startswith(STAGE_DIR + "/") or p.startswith(INTAKE_STAGE)) for p in found[0]):
        raise ValueError(f"{workflow} [{step}]: upload paths must be staged under {STAGE_DIR}/")
    return found[0]


def _selected_locations(base, patterns):
    """The staged locations that the upload patterns select under base: what stage copies and verify compares."""
    selected = set()
    for pattern in patterns:
        paths, error = _paths_for(base, source_pattern(pattern))
        if error:
            raise ValueError(error)
        for path in paths:
            for entry in _entry_files(path):
                selected.add(_staged_location(base, entry))
    return selected


def stage_artifacts(workflow, step, root=ROOT, workflows_root=None):
    """Check the source patterns of one upload step (as post-build), then copy the matched files into a fresh staging
    directory under root and record their sha256 in a manifest. Nothing is staged when any check fails.
    root is the checkout, or the intake of a guard job; workflows_root is where the workflow file is read."""
    base = Path(root).resolve()
    patterns = _named_step_patterns(Path(workflows_root or root).resolve(), workflow, step)
    problems = []
    copies = []
    for pattern in patterns:
        source = source_pattern(pattern)
        paths, error = _paths_for(base, source)
        if error:
            problems.append(f"{workflow}: {error}")
            continue
        if not paths:
            problems.append(f"{workflow}: pattern {source!r} matched no built artifact")
        for path in paths:
            for entry, reason in tree_problems(path):
                problems.append(f"{workflow}: {_rel(entry, base)} ({reason})")
            for entry in _entry_files(path):
                if entry.suffix.lower() in ROM_SUFFIXES and (Path(workflow).name, pattern) not in APPROVED_ROM_UPLOADS:
                    problems.append(f"{workflow}: {_rel(entry, base)} is a ROM file selected by {pattern!r}, which is not on "
                                    "APPROVED_ROM_UPLOADS (Issue #337 round 4 (d))")
            copies += [(f, _staged_location(base, f)) for f in _entry_files(path)]
    if problems:
        raise ValueError("artifact guard (stage): " + "; ".join(problems))
    stage = base / STAGE_DIR
    if stage.is_symlink():
        raise ValueError(f"{STAGE_DIR} is a symlink; refusing to stage")
    if stage.exists():
        shutil.rmtree(stage)   # content from an earlier step, or pre-created by a build step, is discarded
    manifest_path = base / STAGE_MANIFEST
    if manifest_path.is_symlink() or manifest_path.exists():
        manifest_path.unlink()
    files = {}
    for source_file, location in copies:
        target = base / location
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        files[location] = _sha256(target)
    if _selected_locations(base, patterns) != set(files):
        raise ValueError(f"{workflow} [{step}]: staged files do not match what the upload patterns select")
    manifest_path.write_text(json.dumps({"workflow": workflow, "step": step, "files": files}, sort_keys=True),
                             encoding="utf-8")
    print(f"PASS artifact guard stage: {workflow} [{step}] {len(files)} files staged with sha256 manifest")


def verify_artifacts(workflow, step, root=ROOT, workflows_root=None):
    """Recompute the hashes of the staged files and refuse any missing, extra, changed or symlinked entry."""
    base = Path(root).resolve()
    patterns = _named_step_patterns(Path(workflows_root or root).resolve(), workflow, step)
    manifest_path = base / STAGE_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("no staging manifest: the stage step did not run for this upload")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("workflow") != workflow or manifest.get("step") != step:
        raise ValueError("staging manifest belongs to another upload step")
    recorded = manifest["files"]
    stage = base / STAGE_DIR
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("staging directory is missing or is a symlink")
    actual = {}
    problems = []
    for entry in _entry_files(stage):
        location = entry.relative_to(base).as_posix()
        if entry.is_symlink():
            problems.append(f"{location} is a symlink")
            continue
        actual[location] = _sha256(entry)
    for directory, dirnames, _ in os.walk(stage):   # followlinks is off: a symlinked directory is listed, not entered
        for name in dirnames:
            entry = Path(directory, name)
            if entry.is_symlink():
                problems.append(f"{_rel(entry, base)} is a symlink")
    for location in sorted(set(recorded) | set(actual)):
        if location not in actual:
            problems.append(f"{location} is missing from the staging directory")
        elif location not in recorded:
            problems.append(f"{location} is an extra file in the staging directory")
        elif actual[location] != recorded[location]:
            problems.append(f"{location} changed after staging (sha256 mismatch)")
    if _selected_locations(base, patterns) != set(recorded):
        problems.append("the upload patterns no longer select exactly the staged files")
    if problems:
        raise ValueError(f"artifact guard (verify) {workflow} [{step}]: " + "; ".join(problems))
    print(f"PASS artifact guard verify: {workflow} [{step}] {len(recorded)} files match the manifest")


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
        count, issues = pattern_problems(base, workflow, source_pattern(pattern), True)
        matched += count
        problems += issues
    if problems:
        raise ValueError("artifact guard (post-build): " + "; ".join(problems))
    print(f"PASS post-build artifact guard: {workflow} [{step}] {len(found[0])} patterns, {matched} entries")


def main(argv):
    root, workflows_root = ROOT, None
    while argv[:1] in (["--root"], ["--workflow-root"]):
        if len(argv) < 2:
            raise ValueError("usage: --root DIR | --workflow-root DIR")
        if argv[0] == "--root":
            root = Path(argv[1])
        else:
            workflows_root = Path(argv[1])
        argv = argv[2:]
    if not argv:
        # Issue #349 (review): the default entry point is fail-closed. It runs every check that --check-placement runs.
        check_upload_artifacts(root)
        check_guard_placement(root)
    elif argv[0] == "--post-build" and len(argv) == 3:
        check_post_build(argv[1], argv[2], root)
    elif argv[0] == "--stage" and len(argv) == 3:
        stage_artifacts(argv[1], argv[2], root, workflows_root)
    elif argv[0] == "--verify" and len(argv) == 3:
        verify_artifacts(argv[1], argv[2], root, workflows_root)
    elif argv == ["--check-placement"]:
        check_guard_placement(root)
    else:
        raise ValueError("usage: artifact_guard.py [--root DIR] [--workflow-root DIR] "
                         "[--post-build|--stage|--verify WORKFLOW STEP | --check-placement]")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError) as error:
        raise SystemExit(f"artifact_guard: {error}") from error
