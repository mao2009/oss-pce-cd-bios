#!/usr/bin/env python3
"""Private reference BIOS observer driver: subprocess protocol, fail-closed.

An explicit trusted local Geargrafx adapter is REQUIRED. This module never claims that
the repository diagnostic probe can execute retail BIOS APIs. No emulator backend ships here.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from reference_bios_preflight import validate

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"schema", "api_id", "status", "engine", "checkpoint", "registers", "mpr",
          "ram_diff", "instructions_executed"}
# A guarded slot-1e call additionally reports the caller/entry/callee states and the
# in-core guard verdict. Reset observation keeps the original nine-field shape.
JSR_FIELDS = FIELDS | {"before", "after", "call", "guard"}
OBS_SCHEMA = "oss-pce-cd-bios/reference-observation/v1"
REGS = {"a", "x", "y", "p", "sp", "pc"}
STATE_FIELDS = REGS | {"mpr"}


class Blocked(ValueError):
    pass


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def _check_state(state):
    if type(state) is not dict or set(state) != STATE_FIELDS:
        raise Blocked("invalid CPU state snapshot")
    for key in REGS:
        value = state[key]
        limit = 65535 if key == "pc" else 255
        if type(value) is not int or not 0 <= value <= limit:
            raise Blocked("invalid CPU state register")
    mpr = state["mpr"]
    if type(mpr) is not list or len(mpr) != 8 or any(type(x) is not int or not 0 <= x <= 255 for x in mpr):
        raise Blocked("invalid CPU state MPR snapshot")


def check_observation(observation, cfg):
    jsr = cfg.get("api_id") == "slot-1e"
    fields = JSR_FIELDS if jsr else FIELDS
    if type(observation) is not dict or set(observation) != fields:
        raise Blocked("adapter observation schema mismatch")
    if (observation["schema"] != OBS_SCHEMA or observation["api_id"] != cfg["api_id"]
            or observation["engine"] != "geargrafx"
            or observation["status"] != "OBSERVED"):
        raise Blocked("adapter did not report a valid real-core observation")
    if type(observation["checkpoint"]) is not str or not observation["checkpoint"]:
        raise Blocked("missing CPU checkpoint")
    regs = observation["registers"]
    if type(regs) is not dict or set(regs) != REGS or any(type(x) is not int or not 0 <= x <= (65535 if k == "pc" else 255) for k, x in regs.items()):
        raise Blocked("invalid CPU registers")
    mpr = observation["mpr"]
    if type(mpr) is not list or len(mpr) != 8 or any(type(x) is not int or not 0 <= x <= 255 for x in mpr):
        raise Blocked("invalid MPR snapshot")
    n = observation["instructions_executed"]
    if type(n) is not int or not 0 < n <= cfg["max_instructions"]:
        raise Blocked("instruction count invalid or budget exceeded")
    diffs = observation["ram_diff"]
    if type(diffs) is not list or len(diffs) > 65536:
        raise Blocked("invalid RAM diffs")
    allow = cfg["ram_write_allowlist"]
    seen = set()
    for change in diffs:
        if type(change) is not dict or set(change) != {"address", "before", "after"}:
            raise Blocked("invalid RAM diff entry")
        a, before, after = change["address"], change["before"], change["after"]
        if (type(a) is not int or type(before) is not int or type(after) is not int
                or not (0 <= before <= 255 and 0 <= after <= 255)
                or a in seen or not any(r["start"] <= a < r["end"] for r in allow)):
            raise Blocked("out-of-range or duplicate RAM diff")
        seen.add(a)
    if not jsr:
        return
    _check_state(observation["before"])
    _check_state(observation["after"])
    if observation["registers"] != {k: observation["after"][k] for k in REGS}:
        raise Blocked("returned registers disagree with the after-call state")
    if observation["mpr"] != observation["after"]["mpr"]:
        raise Blocked("returned MPR snapshot disagrees with the after-call state")
    call = observation["call"]
    if (type(call) is not dict or set(call) != {"entry_reached", "callee_pc", "returned"}
            or call["entry_reached"] is not True or call["returned"] is not True
            or type(call["callee_pc"]) is not int or not 0 <= call["callee_pc"] <= 65535):
        raise Blocked("slot-1e call did not enter and return from the callee")
    guard = observation["guard"]
    if (type(guard) is not dict or set(guard) != {"detected", "status"}
            or guard["detected"] is not False or guard["status"] != "no_violation"):
        raise Blocked("slot-1e guard did not confirm an in-allowlist run")


def run(cfg, adapter):
    errors = validate(cfg)
    if errors:
        raise Blocked("; ".join(errors))
    if not adapter or not Path(adapter).is_file():
        raise Blocked("local Geargrafx retail-BIOS adapter is NOT_IMPLEMENTED or missing")
    for kind in ("bios", "test_rom"):
        path = Path(cfg[kind + "_path"])
        if not path.is_file() or sha(path) != cfg[kind + "_sha256"]:
            raise Blocked(f"{kind} file missing or sha256 mismatch")
    # Adapter protocol: argv [adapter, --config, config-file].
    # Must be a locally audited executable with bounded stepping, no network,
    # and independent enforcement of RAM access constraints.
    with tempfile.TemporaryDirectory(prefix="pce-reference-", dir=None) as tmp:
        p = Path(tmp) / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        try:
            command = ([sys.executable, str(Path(adapter).resolve())] if Path(adapter).suffix == ".py"
                       else [str(Path(adapter).resolve())])
            result = subprocess.run([*command, "--config", str(p)],
                                    capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Blocked(f"adapter unavailable or timed out: {type(error).__name__}") from error
        if result.returncode != 0 or len(result.stdout) > 262144:
            raise Blocked("adapter failed or stdout exceeded limit")
        try:
            observation = json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise Blocked("adapter did not return one JSON observation") from error
        check_observation(observation, cfg)
        return observation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--adapter", type=Path, help="audited local Geargrafx reference BIOS adapter; required")
    parser.add_argument("--output", type=Path, help="local-only observation JSON; never commit")
    args = parser.parse_args(argv)
    try:
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        observation = run(cfg, args.adapter)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # A raw observation is LOCAL_ONLY, not attested evidence.
            args.output.write_text(json.dumps({"trust": "LOCAL_ONLY_NOT_VERIFIED",
                                               "observation": observation},
                                              indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("OBSERVED LOCAL_ONLY: adapter output schema and hash preconditions checked; no VERIFIED claim")
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"BLOCKED reference BIOS run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
