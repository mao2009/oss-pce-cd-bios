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
import hashlib
import json
import os
import re
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
PUBLISH_USES_RE = re.compile(r"actions/(?:deploy-pages|upload-pages-artifact|attest-build-provenance)@", re.IGNORECASE)
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
PUBLISH_JOB_ALLOWLIST = frozenset({("api-source.yml", "attest-cdread"), ("api-source.yml", "attest-cdread-verdict")})
PAGES_PUBLISH_ACTIONS = ("actions/deploy-pages@", "actions/upload-pages-artifact@")


def _enclosing_job(lines, index):
    """The job id whose block contains lines[index] (its nearest job line above), or None."""
    for j in range(index, -1, -1):
        if _live(lines[j]) and _job_name(lines[j]) is not None:
            return _job_name(lines[j])
    return None


def publish_violations(text, label):
    """Problems for publishing actions (deploy-pages, upload-pages-artifact, attest-build-provenance) in one file, by job."""
    lines = text.splitlines()
    guard_jobs = set()
    for i, line in enumerate(lines):
        match = USES_RE.search(line) if _live(line) else None
        if match:
            name, patterns = _step_patterns(lines, i, match.start())
            if _upload_kind(name, patterns, None) in ("final-intake", "final-workspace"):
                guard_jobs.add(_enclosing_job(lines, i))
    problems = []
    for i, line in enumerate(lines):
        match = PUBLISH_USES_RE.search(line) if _live(line) else None
        if not match:
            continue
        job = _enclosing_job(lines, i)
        if (Path(label).name, job) in PUBLISH_JOB_ALLOWLIST:
            continue
        if job in guard_jobs and any(match.group(0).lower() == p for p in PAGES_PUBLISH_ACTIONS):
            continue
        problems.append(f"{label}: publish action {match.group(0)!r} in job {job!r} is refused unless the (file, job) "
                        "pair is allowlisted (Issue #349)")
    return problems


def check_guard_placement(root=ROOT):
    """Static placement layer (run by make check and by the trusted copy): the preamble and stage/verify steps must
    surround every upload step, in the same job, exactly as written here.

    It scans the same files with the same parser as check_upload_artifacts, so a publication route the parser cannot
    see through (release assets, `gh release`, reusable workflows) fails here too. Gating on "this file contains an
    upload-artifact uses: line" left those routes uninspected by --check-placement while check_upload_artifacts
    refused them (review finding A). A file with no upload step and no such route is not a publication and passes.
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
        problems += publish_violations(text, label)
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
    problems = []
    scope_re = re.compile(r"""["']?(%s)["']?\s*:""" % "|".join(sorted(SCOPE_KEYS_REFUSED)))
    for l in lines:
        if not _live(l):
            continue
        m = scope_re.match(l.strip())
        if m and _indent(l) in (0, 4):
            where = "top-level" if _indent(l) == 0 else "job-level"
            problems.append(f"{workflow}: {where} {m.group(1)} is refused (can change the guard's PATH or RUNNER_TEMP)")
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
        check_upload_artifacts(root)
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
