#!/usr/bin/env python3
"""Slot allocator for independent standalone Geargrafx instances (Issue #103).

At most MAX_SLOTS (2) real-core instances may run at once, by advisory coordination:
a slot is claimed by creating `slot-<n>.lock` with O_EXCL, and only its owner may release
it. Each slot names a private profile/work directory and a reserved loopback port
(BASE_PORT + slot). tools/mcp_checkpoint.py does NOT consult these locks; enforcement is
the operator's responsibility.

ponytail: no automatic stale-lock reclaim (a crashed owner needs `release --force`),
add PID liveness checks when crashes become frequent.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import stat

MAX_SLOTS = 2
BASE_PORT = 47110  # reserved only: no HTTP transport uses it yet; loopback when used
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_MARKER = ".oss-pce-cd-bios-geargrafx-slots"
STATE_MARKER_VALUE = "oss-pce-cd-bios/geargrafx-slots/v1\n"


class SlotError(Exception):
    pass


def _check_state_dir(state_dir, *, create=False):
    """Accept only an explicitly named, project-owned and canonical slot directory."""
    raw = Path(state_dir).expanduser()
    if not raw.is_absolute():
        raise SlotError("state dir must be an explicit absolute path")
    d = raw.resolve()
    if raw != d:
        raise SlotError("state dir must be canonical and must not traverse symlinks")
    home = Path.home().resolve()
    if d == home or d == REPO_ROOT or d in REPO_ROOT.parents or REPO_ROOT in d.parents:
        raise SlotError("state dir cannot be home, a repository, or a repository parent")
    if create:
        d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir():
        raise SlotError("slot state directory does not exist")
    marker = d / STATE_MARKER
    if not marker.exists() and not marker.is_symlink() and create:
        # Refuse to claim an existing directory that already contains unrelated
        # files/locks. Initialization must be done in a dedicated empty folder.
        if any(d.iterdir()):
            raise SlotError("unmarked state directory is not empty; use a dedicated slot directory")
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass  # Another cooperating process initialized it; validate below.
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(STATE_MARKER_VALUE)
    if marker.is_symlink() or not marker.is_file():
        raise SlotError("slot state directory lacks the project ownership marker")
    if marker.read_text(encoding="utf-8") != STATE_MARKER_VALUE:
        raise SlotError("slot state marker is invalid")
    return d


def acquire(state_dir, owner):
    """Claim an advisory slot in an exclusively marked state directory."""
    if not owner:
        raise SlotError("owner name required")
    d = _check_state_dir(state_dir, create=True)
    for n in range(MAX_SLOTS):
        lock = d / f"slot-{n}.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        identity = os.fstat(fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"slot": n, "owner": owner}, stream)
            root = d / f"slot-{n}"
            (root / "profile").mkdir(parents=True, exist_ok=True)
            (root / "work").mkdir(parents=True, exist_ok=True)
        except Exception:
            # Only undo our own newly created lock, never another worker's.
            try:
                current = lock.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    lock.unlink()
            except FileNotFoundError:
                pass
            raise
        return {"slot": n, "owner": owner, "lock": str(lock),
                "profile": str(root / "profile"), "workdir": str(root / "work"),
                "port": BASE_PORT + n}
    raise SlotError(f"all {MAX_SLOTS} Geargrafx slots are in use")


def release(state_dir, slot, owner, force=False):
    """Remove only a known, regular slot lock inside the marked directory."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < MAX_SLOTS:
        raise SlotError(f"slot must be in range 0..{MAX_SLOTS - 1}")
    if not owner:
        raise SlotError("owner name required")
    d = _check_state_dir(state_dir, create=False)
    lock = d / f"slot-{slot}.lock"
    try:
        fd = os.open(lock, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as error:
        raise SlotError(f"slot {slot} is not held") from error
    except OSError as error:
        raise SlotError("slot lock cannot be opened safely") from error
    with os.fdopen(fd, "rb") as stream:
        identity = os.fstat(stream.fileno())
        if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
            raise SlotError("slot lock must be a single-link regular file")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise SlotError("slot lock contains excessive data")
    try:
        held_owner = json.loads(raw.decode("utf-8")).get("owner")
    except (UnicodeError, ValueError, AttributeError):
        held_owner = None  # interrupted O_EXCL write: only explicit force may recover
    if held_owner != owner and not force:
        raise SlotError(f"slot {slot} is owned by {held_owner!r} (unknown if None; use --force)")
    try:
        current = lock.lstat()
    except FileNotFoundError as error:
        raise SlotError("slot lock changed during release") from error
    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or not stat.S_ISREG(current.st_mode):
        raise SlotError("slot lock changed during release")
    lock.unlink()
    return {"slot": slot, "released": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--state-dir", required=True)
    a.add_argument("--owner", required=True)
    r = sub.add_parser("release")
    r.add_argument("--state-dir", required=True)
    r.add_argument("--slot", type=int, required=True)
    r.add_argument("--owner", required=True)
    r.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    try:
        out = (acquire(args.state_dir, args.owner) if args.cmd == "acquire"
               else release(args.state_dir, args.slot, args.owner, args.force))
    except SlotError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 3
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
