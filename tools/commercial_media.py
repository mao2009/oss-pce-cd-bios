"""Safe selection of local commercial CD media from ./rom/ (Issue #635, opt-in only).

Commercial measurement runs only when the caller passes --commercial to
geargrafx_api_entry.py. This module never reads BIN contents, never writes to
./rom/, and never logs media names, paths, or hashes: every error message is a
fixed sanitized code (NOT_RUN / BLOCKED / FAIL) without private identifiers.

Provided the resolved ./rom/ directory passes validation, the harness may use
its single CUE read-only (or the user-selected one when several exist). Pure
BIOS images (*.pce) and other files are ignored: only *.cue files are
considered commercial-media candidates.

Where ./rom/ lives:
- The primary location is <root>/rom (root = repository root that hosts this
  tools/ directory). In a git worktree checkout this may not exist because
  ./rom/ is untracked (gitignored) and not shared between worktrees.
- If the primary does not exist, the fallback is the main worktree's ./rom/
  (derived from `git rev-parse --git-common-dir`). That is the same repository's
  ./rom/ from a worktree's perspective. If neither exists, detection finds no
  candidates and the caller reports NOT_RUN (never PASS).
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROM_DIR_NAME = "rom"
CUE_SUFFIX = ".cue"

# Fail-closed limits (prevent pathological inputs from stalling the harness).
_MAX_CUE_BYTES = 1 << 20  # 1 MiB
_MAX_FILE_REFS = 100
_MAX_CUE_CANDIDATES = 100
_MAX_WALK_FILES = 10000
_MAX_WALK_DEPTH = 10

_FILE_RE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+\S+\s*$', re.IGNORECASE)
_FILE_START_RE = re.compile(r'^\s*FILE\b', re.IGNORECASE)


class CommercialMediaError(ValueError):
    pass


def _is_within(child: Path, parent: Path) -> bool:
    child_r = Path(os.path.realpath(child))
    parent_r = Path(os.path.realpath(parent))
    return child_r == parent_r or child_r.is_relative_to(parent_r)


def _lstat_strict(path: Path, what: str):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        raise CommercialMediaError(f"BLOCKED: {what} not found")
    except OSError:
        raise CommercialMediaError(f"BLOCKED: {what} unreadable")


def resolve_rom_dir(root: Path) -> Path:
    """Return the ./rom directory to search (primary, or main-worktree fallback).

    Raises CommercialMediaError BLOCKED if the chosen directory is a symlink
    or not a directory. Returns the primary path (possibly non-existent)
    when neither location exists; the caller then reports NOT_RUN.
    """
    primary = root / ROM_DIR_NAME
    try:
        st = os.lstat(primary)
    except FileNotFoundError:
        pass
    except OSError:
        raise CommercialMediaError("BLOCKED: ./rom/ unreadable")
    else:
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise CommercialMediaError("BLOCKED: ./rom/ is not a directory")
        return primary
    # Primary missing: try the main worktree's ./rom/ (worktrees do not share
    # untracked ./rom/; the main checkout's ./rom/ is the same repository's
    # ./rom/ from a worktree's perspective).
    fallback = _main_worktree_rom(root)
    if fallback is not None:
        try:
            st = os.lstat(fallback)
        except OSError:
            raise CommercialMediaError("BLOCKED: ./rom/ unreadable")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise CommercialMediaError("BLOCKED: ./rom/ is not a directory")
        return fallback
    return primary


def _main_worktree_rom(root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common = result.stdout.strip()
    if not common:
        return None
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = Path(root) / common_path
    try:
        common_real = Path(os.path.realpath(common_path))
    except OSError:
        return None
    main_root = common_real.parent
    candidate = main_root / ROM_DIR_NAME
    try:
        st = os.lstat(candidate)
    except FileNotFoundError:
        return None
    except OSError:
        raise CommercialMediaError("BLOCKED: ./rom/ unreadable")
    # Symlink/not-dir is reported by the caller as BLOCKED; signal existence
    # by returning the candidate even when invalid (caller re-checks).
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return candidate
    return candidate


def detect_cue_files(rom_dir: Path) -> list[Path]:
    """Return sorted *.cue candidates under rom_dir (recursive).

    Fail-closed: any symlinked directory encountered, or any symlinked /
    non-regular *.cue file, raises BLOCKED. Non-.cue entries are ignored and
    never treated as commercial media (so a *.pce BIOS dump is never picked).
    A missing rom_dir yields no candidates (NOT_RUN is decided by selection).
    Unreadable directories, excessive depth, and excessive entries raise BLOCKED
    rather than being silently skipped.
    """
    try:
        st = os.lstat(rom_dir)
    except FileNotFoundError:
        return []
    except OSError:
        raise CommercialMediaError("BLOCKED: ./rom/ unreadable")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise CommercialMediaError("BLOCKED: ./rom/ is not a directory")

    def _walk_error(_error):
        raise CommercialMediaError("BLOCKED: ./rom/ unreadable")

    found: list[Path] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(rom_dir, topdown=True, followlinks=False, onerror=_walk_error):
        rel = Path(dirpath).relative_to(rom_dir)
        if len(rel.parts) > _MAX_WALK_DEPTH:
            raise CommercialMediaError("BLOCKED: ./rom/ too deep")
        for dirname in dirnames:
            full = Path(dirpath) / dirname
            dst = _lstat_strict(full, "directory")
            if stat.S_ISLNK(dst.st_mode):
                raise CommercialMediaError("BLOCKED: ./rom/ contains a symlinked directory")
        scanned += len(dirnames) + len(filenames)
        if scanned > _MAX_WALK_FILES:
            raise CommercialMediaError("BLOCKED: ./rom/ too many files")
        for filename in filenames:
            if filename.lower().endswith(CUE_SUFFIX):
                full = Path(dirpath) / filename
                fst = _lstat_strict(full, "CUE file")
                if stat.S_ISLNK(fst.st_mode):
                    raise CommercialMediaError("BLOCKED: CUE file is a symlink")
                if not stat.S_ISREG(fst.st_mode):
                    raise CommercialMediaError("BLOCKED: CUE file is not a regular file")
                found.append(full)
                if len(found) > _MAX_CUE_CANDIDATES:
                    raise CommercialMediaError("BLOCKED: too many CUE files")
    found.sort(key=lambda p: p.relative_to(rom_dir).as_posix())
    return found


def select_cue(candidates: list[Path], index: int | None = None) -> Path:
    """Pick one CUE from detected candidates without auto-picking the first."""
    if not candidates:
        raise CommercialMediaError("NOT_RUN: no commercial CUE found in ./rom/")
    if index is not None:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(candidates):
            raise CommercialMediaError("BLOCKED: commercial CUE index out of range")
    if len(candidates) == 1:
        return candidates[0]
    if index is None:
        raise CommercialMediaError("BLOCKED: multiple commercial CUEs; specify --commercial-index")
    return candidates[index]


def parse_cue_file_refs(cue_text: str) -> list[str]:
    """Extract FILE reference names from CUE text (order preserved).

    Any line starting with FILE (case-insensitive) that does not match the
    strict FILE "..." TYPE form raises BLOCKED instead of being silently
    ignored, closing the Python/emulator parser differential.
    """
    refs: list[str] = []
    for line in cue_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("REM"):
            continue
        if _FILE_START_RE.match(line):
            match = _FILE_RE.match(line)
            if not match:
                raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")
            refs.append(match.group(1) if match.group(1) is not None else match.group(2))
    return refs


def _check_no_symlink_components(target: Path, rom_dir: Path) -> None:
    """Reject if target or any ancestor under rom_dir is a symlink."""
    rom_real = Path(os.path.realpath(rom_dir))
    target_abs = Path(os.path.abspath(target))
    # Walk from rom_dir towards target_abs via its relative parts.
    try:
        rel = target_abs.relative_to(Path(os.path.abspath(rom_dir)))
    except ValueError:
        raise CommercialMediaError("BLOCKED: path escapes ./rom/")
    current = Path(os.path.abspath(rom_dir))
    for part in rel.parts:
        current = current / part
        st = _lstat_strict(current, "file")
        if stat.S_ISLNK(st.st_mode):
            raise CommercialMediaError("BLOCKED: path contains a symlink")
    _ = rom_real


def _validate_ref_string(ref: str) -> None:
    if not ref or ref.strip() != ref:
        raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")
    if "\x00" in ref:
        raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")
    if "\\" in ref:
        raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")
    if ref.startswith("/") or (len(ref) >= 2 and ref[1] == ":"):
        raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")
    parts = ref.split("/")
    for part in parts:
        # "." is normalized away (safe); "" and ".." are rejected fail-closed.
        if part in ("", ".."):
            raise CommercialMediaError("BLOCKED: CUE has an invalid FILE reference")


def validate_cue(cue_path: Path, rom_dir: Path) -> list[Path]:
    """Validate that cue_path is a safe commercial CUE inside rom_dir.

    Checks: inside ./rom/ (lexical + realpath), no symlinks (CUE, BIN, and
    intermediate directories), FILE references are relative, traversal-free,
    and point to existing regular files inside ./rom/. Reads only the CUE
    text (plus metadata stats); never reads BIN contents and never writes.
    Returns the validated BIN paths (callers must not log them).
    """
    rom_real = Path(os.path.realpath(rom_dir))
    cue_abs = Path(os.path.abspath(cue_path))
    # Lexical inside check first (fail-closed on outside).
    if not _is_within(Path(os.path.abspath(cue_abs)), Path(os.path.abspath(rom_dir))):
        raise CommercialMediaError("BLOCKED: CUE outside ./rom/")
    if not _is_within(Path(os.path.realpath(cue_abs)), rom_real):
        raise CommercialMediaError("BLOCKED: CUE outside ./rom/")
    _check_no_symlink_components(cue_abs, rom_dir)
    cue_st = _lstat_strict(cue_abs, "CUE file")
    if not stat.S_ISREG(cue_st.st_mode):
        raise CommercialMediaError("BLOCKED: CUE file is not a regular file")
    if cue_st.st_size > _MAX_CUE_BYTES:
        raise CommercialMediaError("BLOCKED: CUE file too large")
    # Read-only, symlink-safe read (O_NOFOLLOW + fstat) of the CUE text only.
    # Never writes and never reads BIN contents here.
    if not hasattr(os, "O_NOFOLLOW") or os.O_NOFOLLOW is None:
        raise CommercialMediaError("BLOCKED: O_NOFOLLOW unavailable")
    try:
        fd = os.open(str(cue_abs), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode) or fst.st_size > _MAX_CUE_BYTES:
            raise CommercialMediaError("BLOCKED: CUE file unreadable")
        chunks = []
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
            if sum(len(c) for c in chunks) > _MAX_CUE_BYTES:
                raise CommercialMediaError("BLOCKED: CUE file too large")
        cue_bytes = b"".join(chunks)
    except CommercialMediaError:
        raise
    except OSError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")
    finally:
        try:
            os.close(fd)
        except OSError:
            raise CommercialMediaError("BLOCKED: CUE file unreadable")
    try:
        cue_text = cue_bytes.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")
    refs = parse_cue_file_refs(cue_text)
    if not refs:
        raise CommercialMediaError("BLOCKED: CUE has no FILE reference")
    if len(refs) > _MAX_FILE_REFS:
        raise CommercialMediaError("BLOCKED: CUE has too many FILE references")
    validated: list[Path] = []
    for ref in refs:
        _validate_ref_string(ref)
        target_abs = Path(os.path.normpath(cue_abs.parent / ref))
        if not _is_within(Path(os.path.abspath(target_abs)), Path(os.path.abspath(rom_dir))):
            raise CommercialMediaError("BLOCKED: CUE FILE reference outside ./rom/")
        if not _is_within(Path(os.path.realpath(target_abs)), rom_real):
            raise CommercialMediaError("BLOCKED: CUE FILE reference outside ./rom/")
        _check_no_symlink_components(target_abs, rom_dir)
        tst = _lstat_strict(target_abs, "FILE reference")
        if not stat.S_ISREG(tst.st_mode):
            raise CommercialMediaError("BLOCKED: CUE FILE reference is not a regular file")
        validated.append(target_abs)
    return validated


# Sector sizes for the CUE modes understood by cue_analysis (stat-only layout below).
_SECTOR_BYTES = {
    "MODE1/2048": 2048,
    "MODE1/2352": 2352,
    "MODE2/2336": 2336,
    "MODE2/2352": 2352,
    "AUDIO": 2352,
}


def _read_cue_text_safely(cue_path: Path) -> str:
    """Read CUE text with O_NOFOLLOW (same limits as validate_cue). Sanitized errors only."""
    cue_abs = Path(os.path.abspath(cue_path))
    if not hasattr(os, "O_NOFOLLOW") or os.O_NOFOLLOW is None:
        raise CommercialMediaError("BLOCKED: O_NOFOLLOW unavailable")
    try:
        fd = os.open(str(cue_abs), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode) or fst.st_size > _MAX_CUE_BYTES:
            raise CommercialMediaError("BLOCKED: CUE file unreadable")
        chunks = []
        total = 0
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if total > _MAX_CUE_BYTES:
                raise CommercialMediaError("BLOCKED: CUE file too large")
        cue_bytes = b"".join(chunks)
    except CommercialMediaError:
        raise
    except OSError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")
    finally:
        try:
            os.close(fd)
        except OSError:
            raise CommercialMediaError("BLOCKED: CUE file unreadable")
    try:
        return cue_bytes.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        raise CommercialMediaError("BLOCKED: CUE file unreadable")


def data_track_index01_lba(cue_path: Path, rom_dir: Path) -> int:
    """Return the first MODE1/2352 track's INDEX 01 absolute LBA, stat-only.

    Fail-closed and sanitized: the CUE is validated with validate_cue, parsed
    with cue_analysis.parse_cue_text, and BIN sizes are taken from lstat only.
    No BIN content is read, no hash is computed, and no media name, path or
    LBA value appears in any error message. Exactly one MODE1/2352 track is
    required; zero or multiple such tracks are BLOCKED (never guessed).
    """
    cue_path = Path(cue_path)
    # Full CUE/BIN safety validation first (symlinks, refs, existence).
    validate_cue(cue_path, rom_dir)
    cue_text = _read_cue_text_safely(cue_path)
    try:
        try:
            from cue_analysis import parse_cue_text
        except ImportError:
            from tools.cue_analysis import parse_cue_text  # type: ignore
        sheet = parse_cue_text(cue_text)
    except Exception:
        raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
    cue_abs = Path(os.path.abspath(cue_path))
    disc_cursor = 0
    mode1_starts: list[int] = []
    try:
        for file in sheet["files"]:
            if not file["tracks"]:
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            sizes = {_SECTOR_BYTES.get(t["mode"]) for t in file["tracks"]}
            if len(sizes) != 1 or None in sizes:
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            sector_bytes = sizes.pop()
            # BIN identity by lstat only; never read BIN contents here.
            ref = file["name"]
            _validate_ref_string(ref)
            bin_abs = Path(os.path.normpath(cue_abs.parent / ref))
            if not _is_within(Path(os.path.abspath(bin_abs)), Path(os.path.abspath(rom_dir))):
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            _check_no_symlink_components(bin_abs, rom_dir)
            bst = _lstat_strict(bin_abs, "FILE reference")
            if not stat.S_ISREG(bst.st_mode):
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            if bst.st_size % sector_bytes:
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            file_frames = bst.st_size // sector_bytes
            for track in file["tracks"]:
                if 1 not in track["index"]:
                    raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
                if 0 in track["index"] and track["index"][0] > track["index"][1]:
                    raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
                if "pregap" in track and 0 in track["index"]:
                    raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            starts = [min(t["index"].values()) for t in file["tracks"]]
            if starts != sorted(starts) or starts[0] != 0:
                raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
            for position, track in enumerate(file["tracks"]):
                bin_start = starts[position]
                index01 = track["index"][1]
                bin_end = starts[position + 1] if position + 1 < len(starts) else file_frames
                user_frames = bin_end - index01
                if user_frames <= 0:
                    raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
                pregap_frames = (index01 - bin_start) + track.get("pregap", 0)
                start_lba = disc_cursor + pregap_frames
                if track["mode"] == "MODE1/2352":
                    mode1_starts.append(start_lba)
                disc_cursor = start_lba + user_frames
    except CommercialMediaError:
        raise
    except Exception:
        raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
    if len(mode1_starts) != 1:
        raise CommercialMediaError("BLOCKED: CUE layout cannot be derived")
    return mode1_starts[0]
