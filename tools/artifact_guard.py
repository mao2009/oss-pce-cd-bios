"""Fail-closed artifact guard (Issue #227): no linked executable or object reaches upload-artifact.

Every upload-artifact / upload-pages-artifact step in .github/workflows/*.yml|*.yaml and composite actions
(.github/actions/**/action.yml|yaml) is parsed as a whole block (key order does not matter),
its `path:` patterns are expanded against the checkout, and each matched entry is checked by content:
directories are walked, symlinks are refused, and ELF/Mach-O/PE/ar signatures and object/executable suffixes
are rejected. Unrecognised `path:` forms are refused rather than guessed. Names are not used, because legitimate
evidence JSON may contain "geargrafx". This is a content/signature tripwire only; it does not cover uploads made
from run: (a `gh release` mention is refused outright), and the verdict-upload exemption is unchanged. Distribution conditions
stay with docs/provenance.md and Issue #105.
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


def _step_patterns(lines, index, column):
    """Return every path pattern of the upload-artifact step whose `uses` key starts at lines[index][column]."""
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
    for offset, line in enumerate(step):
        body = line.lstrip()
        if body.startswith("- "):
            body = body[2:].lstrip()
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
    return patterns


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def upload_patterns(text):
    """Return the path patterns of every upload-artifact step in one workflow file."""
    lines = text.splitlines()
    patterns = []
    for index, line in enumerate(lines):
        match = USES_RE.search(line)
        if match:
            patterns.extend(_step_patterns(lines, index, match.start()))
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
    with path.open("rb") as handle:
        head = handle.read(4096)
        if head.startswith(SIGNATURES):
            return "ELF/Mach-O/ar object or executable signature"
        # A file under 0x40 bytes cannot carry the e_lfanew field, so it is not treated as a PE candidate.
        if head.startswith(b"MZ") and len(head) >= 0x40:
            handle.seek(int.from_bytes(head[0x3C:0x40], "little"))
            window = handle.read(4)
            if len(window) < 4:
                return "MZ header whose PE header lies beyond end of file"
            if window == b"PE\0\0":
                return "PE executable signature"
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


def check_upload_artifacts(root=ROOT):
    """Raise ValueError if any upload-artifact pattern matches a linked executable, object or symlink."""
    total = 0
    matched = 0
    problems = []
    base = Path(root)
    files = [f for ext in ("yml", "yaml") for f in (*(base / WORKFLOWS).glob(f"*.{ext}"),
                                                   *(base / ACTIONS).glob(f"**/action.{ext}"))]
    for workflow in sorted(files):
        for pattern in upload_patterns(workflow.read_text(encoding="utf-8")):
            total += 1
            if pattern in OUTSIDE_CHECKOUT:
                continue
            if not pattern or "${{" in pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
                problems.append(f"{workflow.relative_to(base).as_posix()}: unresolvable or unsafe pattern {pattern!r}")
                continue
            if any(char in pattern for char in UNSUPPORTED_PATTERN_CHARS):
                problems.append(f"{workflow.relative_to(base).as_posix()}: unsupported pattern {pattern!r} (brace, extglob or alternation)")
                continue
            for path in Path(root).glob(pattern):
                matched += 1
                for entry, reason in tree_problems(path):
                    problems.append(f"{workflow.relative_to(base).as_posix()}: {entry.relative_to(root).as_posix()} ({reason})")
    if total == 0:
        raise ValueError("no upload-artifact path patterns found; guard cannot establish the upload set")
    if problems:
        raise ValueError("artifact guard: linked executable or unsafe entry would be uploaded: "
                         + "; ".join(problems))
    print(f"PASS upload-artifact guard: {total} patterns, {matched} local entries, no linked executable or object")


if __name__ == "__main__":
    try:
        check_upload_artifacts()
    except (ValueError, OSError) as error:
        raise SystemExit(f"artifact_guard: {error}") from error
