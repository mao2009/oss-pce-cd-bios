"""PCE ROM provenance and reproducibility check (Issue #335).

Fail-closed gate that runs before any .pce is handed over for publication. It does not replace the artifact guard
(tools/artifact_guard.py, signatures and staging) or the provenance guard (tools/provenance_guard.py, denylist); it
adds two checks those do not make:

  1. Record check (--rom/--manifest): the manifest.json beside a ROM (written by tools/build.py or
     tools/build_rom.py) must agree with the bytes: sha256 and size match, the source commit is the expected one, the
     tree was not dirty, the assembler is the pinned cc65 revision, and no release claim is set. This is internal
     consistency only. The manifest is unsigned, so a tamperer who rewrites both files passes; origin is not proven.
  2. Same-input reproducibility (--reproduce): the same tool is run twice on the same checkout into two fresh
     directories, the two ROM files must be byte-identical, and the rebuilt manifest must pass the record check.
     The CI verify job also byte-compares the file it hands off with the rebuilt ROM, so the published bytes are the
     checked bytes.

A mismatch in either check exits 1 (FAIL) and must stop the workflow before the upload-artifact handoff.

Limits (stated in docs/provenance.md): a matching hash and record show that the bytes came from the recorded commit
with the pinned cc65 and are reproducible. They do not prove the absence of GPL-derived code, the absence of BIOS-like
content, or the legal status of the output. Those remain human review items (Issue #105, #335).

Usage:
  python3 tools/rom_provenance.py --rom ROM --manifest MANIFEST [--commit SHA]
  python3 tools/rom_provenance.py --reproduce smoke|dev [--mode debug|release] [--work DIR] [--commit SHA]

Exit codes: 0 PASS, 1 FAIL (a check contradicts the record or the rebuild), 2 ERROR (tool or build failure).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, ROOT  # noqa: E402  (pinned cc65 revision)

EXIT_PASS, EXIT_FAIL, EXIT_ERROR = 0, 1, 2
SHA40 = re.compile(r"[0-9a-f]{40}")
# Output kind -> (builder script, ROM file name written into the --output directory).
BUILDERS = {
    "smoke": ("build.py", "smoke-test-not-bios.pce"),
    "dev": ("build_rom.py", "dev-only-not-compatible-syscard3.pce"),
}


class ToolError(RuntimeError):
    """A build or tool failure (exit 2), distinct from a provenance mismatch (exit 1)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_record(rom: bytes, manifest: dict, expected_commit: str) -> list[str]:
    """Return a list of problems; an empty list means the manifest describes exactly this ROM."""
    problems = []
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]
    if manifest.get("rom_sha256") != sha256(rom):
        problems.append("rom_sha256 does not match the ROM bytes")
    if manifest.get("rom_bytes") != len(rom):
        problems.append("rom_bytes does not match the ROM size")
    source = manifest.get("source_sha")
    if not isinstance(source, str) or not SHA40.fullmatch(source):
        problems.append("source_sha is missing or not a full commit SHA")
    elif source != expected_commit:
        problems.append(f"source_sha {source} is not the expected commit {expected_commit}")
    if manifest.get("working_tree_dirty") is not False:
        problems.append("working_tree_dirty is not false (built from an unclean tree)")
    if manifest.get("cc65_revision") != DEPENDENCIES["cc65"]["revision"]:
        problems.append("cc65_revision is not the pinned revision")
    if manifest.get("release_eligible") not in (None, False):
        problems.append("release_eligible is set (a release claim is not provable here)")
    return problems


def check_reproducible(first: bytes, second: bytes) -> list[str]:
    """Two builds from the same input must produce byte-identical ROMs."""
    if first == second:
        return []
    return [f"two builds from the same input differ: {sha256(first)} != {sha256(second)}"]


OWNER_MARKER = ".rom-provenance-owner"  # written by this tool into every directory it creates under --work
MARKER_TEXT = "created by tools/rom_provenance.py (Issue #335)\n"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400  # Windows junctions and other reparse points (os.lstat st_file_attributes)


def _is_link(path: Path) -> bool:
    """True for a symlink or a Windows reparse point (junction). A missing path is not a link."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _owned(directory: Path) -> bool:
    """True only for a real directory holding a real (non-link) ownership marker written by this tool."""
    marker = directory / OWNER_MARKER
    return not _is_link(directory) and directory.is_dir() and not _is_link(marker) and marker.is_file()


def _claim(directory: Path) -> None:
    """Create a directory this tool owns. os.mkdir fails if the path exists, so nothing is reused silently."""
    os.mkdir(directory)
    (directory / OWNER_MARKER).write_text(MARKER_TEXT, encoding="utf-8")


def reproduce(build_once, work: Path) -> tuple[Path, Path]:
    """Run build_once(out_dir) -> rom_path twice into new directories a and b under work; return both ROM paths.

    build_once is injected so tests can use a fake builder. Nothing is deleted. Refused (ToolError): a link or
    junction at work, a/ or b/; a non-empty work without the marker; an existing a/ or b/ (even one with the
    marker, since a marker can be copied, so a repeat run needs a new --work).
    """
    root = ROOT.resolve()
    if _is_link(work) or work.resolve() in (root, *root.parents):
        raise ToolError(f"refusing --work {work}: a symlink or junction, or the repository root or above")
    if os.path.lexists(work):
        if not work.is_dir():
            raise ToolError(f"refusing --work {work}: not a directory")
        if any(work.iterdir()) and not _owned(work):
            raise ToolError(f"refusing --work {work}: not empty and not created by this tool; pass a new --work")
        if not _owned(work):  # an empty directory we may claim: stamp it so the next run recognises it
            (work / OWNER_MARKER).write_text(MARKER_TEXT, encoding="utf-8")
    else:
        work.parent.mkdir(parents=True, exist_ok=True)
        _claim(work)
    for name in ("a", "b"):
        target = work / name
        if _is_link(target):
            raise ToolError(f"refusing {target}: a symlink or junction")
        if os.path.lexists(target):
            raise ToolError(f"refusing to overwrite existing {target}; remove it or pass a new --work")
        try:
            _claim(target)
        except FileExistsError as error:  # created between the check and os.mkdir
            raise ToolError(f"refusing to overwrite existing {target}") from error
    first = build_once(work / "a")
    second = build_once(work / "b")
    return Path(first), Path(second)


def _head_commit() -> str:
    result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    if result.returncode != 0 or not SHA40.fullmatch(result.stdout.strip()):
        raise ToolError("cannot read HEAD commit; pass --commit explicitly")
    return result.stdout.strip()


def _subprocess_builder(kind: str, mode: str):
    script, rom_name = BUILDERS[kind]

    def build_once(out: Path) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(ROOT / "tools" / script), "--mode", mode, "--output", str(out)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise ToolError(f"{script} failed (exit {result.returncode}): {result.stderr.strip()[-400:]}")
        return out / rom_name

    return build_once


def _read_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolError(f"manifest unreadable: {path}: {error}") from error


def verify_one(rom_path: Path, manifest_path: Path, commit: str) -> list[str]:
    try:
        rom = rom_path.read_bytes()
    except OSError as error:
        raise ToolError(f"ROM unreadable: {rom_path}: {error}") from error
    return check_record(rom, _read_manifest(manifest_path), commit)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--reproduce", choices=sorted(BUILDERS))
    parser.add_argument("--mode", default="debug", choices=("debug", "release"))
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "rom-provenance")
    parser.add_argument("--commit", help="expected source commit (default: HEAD of this checkout)")
    args = parser.parse_args(argv)
    try:
        if bool(args.rom) != bool(args.manifest) or bool(args.rom) == bool(args.reproduce):
            parser.error("use either --rom with --manifest, or --reproduce")
        commit = args.commit or _head_commit()
        if not SHA40.fullmatch(commit):
            raise ToolError("--commit must be a full 40-hex SHA")
        if args.rom:
            problems = verify_one(args.rom, args.manifest, commit)
            label = str(args.rom)
            rom_bytes = args.rom.read_bytes()
        else:
            first, second = reproduce(_subprocess_builder(args.reproduce, args.mode), args.work)
            rom_bytes = first.read_bytes()
            problems = check_reproducible(rom_bytes, second.read_bytes())
            problems += check_record(rom_bytes, _read_manifest(first.parent / "manifest.json"), commit)
            label = f"{args.reproduce}/{args.mode}"
    except ToolError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR
    if problems:
        for problem in problems:
            print(f"FAIL {label}: {problem}", file=sys.stderr)
        return EXIT_FAIL
    print(f"PASS {label}: sha256={sha256(rom_bytes)} commit={commit} cc65={DEPENDENCIES['cc65']['revision'][:9]}")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
