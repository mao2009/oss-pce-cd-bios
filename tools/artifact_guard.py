"""Fail-closed artifact guard (Issue #227): no linked executable or object reaches upload-artifact.

Every `path:` pattern of an actions/upload-artifact step is read from .github/workflows/*.yml (line-based,
no YAML dependency), expanded against the checkout, and each matched entry is checked by content:
symlinks, ELF/Mach-O/PE/ar signatures and object/executable suffixes are rejected. Names are not used,
because legitimate evidence JSON may contain "geargrafx". This is a tripwire, not a licence decision;
distribution conditions stay with docs/provenance.md and Issue #105.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ".github/workflows"
# Upload patterns that resolve outside the checkout (runner temp). Exact match only; anything else with
# a GitHub expression is refused so that a new expression pattern gets reviewed.
OUTSIDE_CHECKOUT = {"${{ runner.temp }}/verdict/cdread-verdict.json"}
OBJECT_SUFFIXES = {".o", ".obj", ".a", ".lib", ".so", ".dll", ".dylib", ".exe", ".elf", ".out"}
SIGNATURES = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
              b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"!<arch>\n")


def upload_patterns(text):
    """Return the path patterns of every upload-artifact step in one workflow file."""
    lines = text.splitlines()
    patterns = []
    for index, line in enumerate(lines):
        if "uses:" not in line or "actions/upload-artifact@" not in line:
            continue
        uses_indent = len(line) - len(line.lstrip())
        end = index + 1
        while end < len(lines) and not (lines[end].strip().startswith("- ")
                                        and len(lines[end]) - len(lines[end].lstrip()) < uses_indent):
            end += 1
        for path_index in range(index + 1, end):
            stripped = lines[path_index].strip()
            if not stripped.startswith("path:"):
                continue
            path_indent = len(lines[path_index]) - len(lines[path_index].lstrip())
            value = stripped[len("path:"):].strip()
            if value in ("|", ">", "|-", ">-"):
                for block in lines[path_index + 1:end]:
                    if block.strip() and len(block) - len(block.lstrip()) > path_indent:
                        patterns.append(block.strip())
                    elif block.strip():
                        break
            else:
                patterns.append(value.strip("'\""))
    return patterns


def entry_problem(path):
    """Return why a matched entry must not be uploaded, or None."""
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
    if head.startswith(b"MZ") and len(head) >= 0x40:
        offset = int.from_bytes(head[0x3C:0x40], "little")
        if head[offset:offset + 4] == b"PE\0\0":
            return "PE executable signature"
    return None


def check_upload_artifacts(root=ROOT):
    """Raise ValueError if any upload-artifact pattern matches a linked executable or object."""
    total = 0
    matched = 0
    problems = []
    for workflow in sorted((Path(root) / WORKFLOWS).glob("*.yml")):
        for pattern in upload_patterns(workflow.read_text(encoding="utf-8")):
            total += 1
            if pattern in OUTSIDE_CHECKOUT:
                continue
            if "${{" in pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
                problems.append(f"{workflow.name}: unresolvable or unsafe pattern {pattern!r}")
                continue
            for path in Path(root).glob(pattern):
                matched += 1
                reason = entry_problem(path)
                if reason:
                    problems.append(f"{workflow.name}: {path.relative_to(root).as_posix()} ({reason})")
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
