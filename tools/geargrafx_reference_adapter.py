#!/usr/bin/env python3
"""Local Geargrafx reference observer for the private System Card BIOS.

Two observation modes are supported, both driven by the pinned upstream core:

* ``reference-reset-only`` measures the real reset vector state.
* ``slot-1e`` calls the slot-0x1E API entry (EX_GETVER) from a synthetic guest
  stub. It runs ONLY through the guarded C++ probe ``geargrafx_reference_jsr.cpp``,
  which enforces in-core RAM write protection (``Memory::GetMemoryMapWrite``),
  in-core CPU-address write breakpoints, hardware-page isolation and a per
  instruction budget. A post-run empty ``ram_diff`` is never treated as proof.

This adapter is local-only: it links the private BIOS and the GPL core. It is
never bundled, never uploaded and never run in CI.

The slot-1e output of ``jsr_observation()`` is a private-BIOS run record (``reference-observation/v1``).
It is not the V08 observation record: the schema of record for V08 is ``tools/jsr_observation.py``,
which accepts synthetic provenance only and records IRQ as OBSERVED or UNKNOWN.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/integration"))
import reference_jsr_gate as G
from geargrafx_api import compile_probe

SCHEMA = "oss-pce-cd-bios/reference-observation/v1"
JSR_CHECKPOINT = "slot_1e_ex_getver_returned_to_caller"
JSR_STATE_FIELDS = ("a", "x", "y", "p", "sp", "pc")


def validate_jsr_config(config):
    """Reject anything the guarded slot-1e probe cannot safely honour."""
    if config.get("api_id") != "slot-1e":
        raise ValueError("BLOCKED: reference JSR experiment supports only slot-1e")
    budget = config.get("max_instructions")
    if type(budget) is not int or not 4 <= budget <= 1_000_000:
        raise ValueError("BLOCKED: invalid JSR instruction budget")
    allow = config.get("ram_write_allowlist")
    if (type(allow) is not list or len(allow) != 1 or type(allow[0]) is not dict
            or set(allow[0]) != {"start", "end"}):
        raise ValueError("BLOCKED: slot-1e requires exactly one RAM allowlist page interval")
    start, end = allow[0]["start"], allow[0]["end"]
    # slot "$1E" execution environment fixes the RAM write allowlist to page 1
    # ([0x2000, 0x4000)): this is where this core's zero page and stack live.
    if (type(start) is not int or type(end) is not int
            or start != 0x2000 or end != 0x4000):
        raise ValueError("BLOCKED: slot-1e allowlist must be exactly [0x2000, 0x4000)")
    return budget, start, end


def _check_state(state):
    if type(state) is not dict or set(state) != set(JSR_STATE_FIELDS) | {"mpr"}:
        raise ValueError("BLOCKED: JSR probe state has the wrong shape")
    for key in JSR_STATE_FIELDS:
        value = state[key]
        limit = 0xFFFF if key == "pc" else 0xFF
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError("BLOCKED: JSR probe register out of range")
    mpr = state["mpr"]
    if type(mpr) is not list or len(mpr) != 8 or any(type(x) is not int or not 0 <= x <= 0xFF for x in mpr):
        raise ValueError("BLOCKED: JSR probe MPR snapshot invalid")


def jsr_observation(row):
    """Map a guarded-probe JSON record to a fail-closed observation, or raise BLOCKED."""
    if type(row) is not dict or row.get("mode") != "call":
        raise ValueError("BLOCKED: real-core JSR probe emitted no call record")
    if row.get("status") != "ok":
        raise ValueError(f"BLOCKED: real-core JSR probe rejected the call: {row.get('reason', '')}")
    if row.get("entry_reached") is not True or row.get("returned") is not True:
        raise ValueError("BLOCKED: JSR probe never entered and returned from the slot entry")
    if row.get("detected") is not False:
        raise ValueError("BLOCKED: JSR probe observed a store outside the allowlist")
    _check_state(row.get("before"))
    _check_state(row.get("after"))
    diffs = row.get("ram_diff")
    if type(diffs) is not list:
        raise ValueError("BLOCKED: JSR probe RAM diff missing")
    executed = row.get("instructions_executed")
    if type(executed) is not int or not 0 < executed:
        raise ValueError("BLOCKED: JSR probe instruction count invalid")
    after = row["after"]
    return {
        "schema": SCHEMA, "api_id": "slot-1e", "status": "OBSERVED", "engine": "geargrafx",
        "checkpoint": JSR_CHECKPOINT,
        "registers": {key: after[key] for key in JSR_STATE_FIELDS},
        "mpr": after["mpr"],
        "ram_diff": diffs,
        "instructions_executed": executed,
        "before": row["before"],
        "after": after,
        "call": {"entry_reached": True, "callee_pc": row["callee_pc"], "returned": True},
        "guard": {"detected": False, "status": "no_violation"},
    }


def observe_jsr(config):
    """Call slot 0x1E through the guarded probe; in-core enforcement is mandatory."""
    budget, allow_start, allow_end = validate_jsr_config(config)
    # Execution gate: refuse unless the reference JSR gate allows this slot entry. This runs
    # before the probe is compiled or bios_path is opened; no caller flag can skip it.
    G.require_allowed(config["api_id"])
    # Local probe compilation uses the pinned Geargrafx debugger build, never bundled.
    with tempfile.TemporaryDirectory(prefix="pce-reference-jsr-") as tmp:
        out = Path(tmp)
        probe = compile_probe(out, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-jsr-probe")
        (out / "synthetic.bin").write_bytes(bytes(2352 * 32))
        cue = out / "synthetic.cue"
        cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        cp = subprocess.run([str(probe), str(Path(config["bios_path"]).resolve()), str(cue),
                             "0x1e", str(budget), hex(allow_start), hex(allow_end), "call", "0"],
                            capture_output=True, text=True, timeout=60, check=False)
        if cp.returncode or len(cp.stdout) > 1 << 20 or len(cp.stderr) > 1 << 16:
            raise ValueError("BLOCKED: actual Geargrafx JSR probe failed")
        try:
            row = json.loads(cp.stderr)
        except (ValueError, TypeError) as error:
            raise ValueError("BLOCKED: real-core JSR probe emitted no JSON observation") from error
        return jsr_observation(row)


def observe(config):
    # Gate every branch (slot-1e and reset-only alike) before any dispatch or bios_path access.
    G.require_allowed(config.get("api_id"))
    if config.get("api_id") == "slot-1e":
        return observe_jsr(config)
    if config.get("api_id") != "reference-reset-only":
        raise ValueError("BLOCKED: no reference BIOS API call wrapper exists")
    budget = config.get("max_instructions")
    if type(budget) is not int or not 1 <= budget <= 10_000_000:
        raise ValueError("BLOCKED: invalid step budget")
    # Local probe compilation uses the pinned Geargrafx debugger build, never bundled.
    with tempfile.TemporaryDirectory(prefix="pce-reference-reset-") as tmp:
        out = Path(tmp)
        probe = compile_probe(out, source_name="geargrafx_reference_reset.cpp",
                              target_name="reference-reset-probe")
        (out / "synthetic.bin").write_bytes(bytes(2352 * 32))
        cue = out / "synthetic.cue"
        cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        # The actual C++ probe enforces the budget too; auto means stop after 1 CPU instruction.
        cp = subprocess.run([str(probe), str(Path(config["bios_path"]).resolve()),
                             str(cue), str(budget), "auto"],
                            capture_output=True, text=True, timeout=25, check=False)
        if cp.returncode or len(cp.stdout) > 65536 or len(cp.stderr) > 8192:
            raise ValueError("BLOCKED: actual Geargrafx reference reset probe failed")
        # The core logs to stdout; the probe emits exactly one JSON record on stderr.
        try:
            row = json.loads(cp.stderr)
        except (ValueError, TypeError) as error:
            raise ValueError("BLOCKED: real-core reset probe emitted no JSON observation") from error
        if type(row) is not dict or set(row) != {"checkpoint", "instructions_executed", "registers", "mpr"}:
            raise ValueError("BLOCKED: real-core probe produced wrong shape")
        return {
            "schema": SCHEMA, "api_id": "reference-reset-only",
            "status": "OBSERVED", "engine": "geargrafx",
            "checkpoint": "reset_first_instruction_no_api_callee",
            "registers": row["registers"], "mpr": row["mpr"],
            "ram_diff": [], "instructions_executed": row["instructions_executed"],
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    a = parser.parse_args()
    try:
        config = json.loads(a.config.read_text(encoding="utf-8"))
        result = observe(config)
        print(json.dumps(result, separators=(",", ":")))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {type(error).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
