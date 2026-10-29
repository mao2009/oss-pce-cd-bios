#!/usr/bin/env python3
"""Slot allocator for independent standalone Geargrafx instances (Issue #103).

At most MAX_SLOTS (2) real-core instances may run at once. Each slot owns a private
profile directory, work directory and loopback-only port (BASE_PORT + slot), so one
Worker never drives another Worker's emulator. A slot is claimed by creating
`slot-<n>.lock` with O_EXCL; only the owner that claimed it may release it.

ponytail: no automatic stale-lock reclaim (a crashed owner needs `release --force`),
add PID liveness checks when crashes become frequent.
"""
import argparse
import json
import os
from pathlib import Path
import sys

MAX_SLOTS = 2
BASE_PORT = 47110  # loopback only: 127.0.0.1:47110 and 47111; used only by HTTP transport
REPO_ROOT = Path(__file__).resolve().parent.parent


class SlotError(Exception):
    pass


def _check_state_dir(state_dir):
    d = Path(state_dir).resolve()
    if d == REPO_ROOT or REPO_ROOT in d.parents:
        raise SlotError("state dir must be outside the repository (use scratchpad)")
    d.mkdir(parents=True, exist_ok=True)
    return d


def acquire(state_dir, owner):
    """Claim a free slot; returns a dict with the slot's paths and port."""
    if not owner:
        raise SlotError("owner name required")
    d = _check_state_dir(state_dir)
    for n in range(MAX_SLOTS):
        lock = d / f"slot-{n}.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"slot": n, "owner": owner}, f)
        root = d / f"slot-{n}"
        (root / "profile").mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(parents=True, exist_ok=True)
        return {"slot": n, "owner": owner, "lock": str(lock),
                "profile": str(root / "profile"), "workdir": str(root / "work"),
                "port": BASE_PORT + n}
    raise SlotError(f"all {MAX_SLOTS} Geargrafx slots are in use")


def release(state_dir, slot, owner, force=False):
    """Release a slot. Only its owner may release it unless force is set."""
    d = _check_state_dir(state_dir)
    lock = d / f"slot-{slot}.lock"
    if not lock.is_file():
        raise SlotError(f"slot {slot} is not held")
    held = json.loads(lock.read_text(encoding="utf-8"))
    if held.get("owner") != owner and not force:
        raise SlotError(f"slot {slot} is owned by {held.get('owner')!r}")
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
