#!/usr/bin/env python3
"""Local Geargrafx adapter for a private BIOS RESET checkpoint, not an API call.

The test-ROM digest is checked by the driver but the test ROM is NOT executed.
Only api_id=reference-reset-only is supported, fail-closed for all 90 API IDs.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/integration"))
from geargrafx_api import compile_probe

SCHEMA = "oss-pce-cd-bios/reference-observation/v1"



def observe_jsr(config):
    """Real guest JSR in an explicitly injected test-only CPU/RAM environment."""
    if config.get("api_id") != "slot-1e":
        raise ValueError("BLOCKED: reference JSR experiment supports only slot-1e")
    budget = config.get("max_instructions")
    if type(budget) is not int or not 3 <= budget <= 1000000:
        raise ValueError("BLOCKED: invalid JSR instruction budget")
    with tempfile.TemporaryDirectory(prefix="pce-reference-jsr-") as tmp:
        out = Path(tmp)
        probe = compile_probe(out, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-jsr-probe")
        (out / "synthetic.bin").write_bytes(bytes(2352 * 32))
        cue = out / "synthetic.cue"
        cue.write_text('FILE "synthetic.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        cp = subprocess.run([str(probe), str(Path(config["bios_path"]).resolve()),
                             str(cue), "0x1e", str(budget)],
                            capture_output=True, text=True, timeout=25, check=False)
        if cp.returncode or len(cp.stdout) > 16384:
            raise ValueError("BLOCKED: reference JSR did not complete within limits")
        lines = cp.stdout.splitlines()
        if len(lines) != 3 or [x.split(" ", 1)[0] for x in lines] != ["BEFORE", "ENTRY", "AFTER"]:
            raise ValueError("BLOCKED: reference JSR checkpoints missing")
        before, entry, after = [json.loads(x.split(" ", 1)[1]) for x in lines]
        if (before["pc"] != 0x2200 or before["sp"] != 255 or
                entry["pc"] != 0xe05a or entry["sp"] != 253 or
                after["pc"] != 0x2203 or after["step"] > budget):
            raise ValueError("BLOCKED: unexpected JSR call/return")
        if len(after["mpr"]) != 8:
            raise ValueError("BLOCKED: malformed MPR")
        return {
            "schema": SCHEMA, "api_id": "slot-1e",
            "status": "OBSERVED", "engine": "geargrafx",
            "checkpoint": "synthetic_guest_jsr_return_not_abi_verified",
            "registers": {k: after[k] for k in ("a", "x", "y", "p", "sp", "pc")},
            "mpr": after["mpr"], "ram_diff": [],
            "instructions_executed": after["step"],
        }

def observe(config):
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
        if cp.returncode or len(cp.stdout) > 8192:
            raise ValueError("BLOCKED: actual Geargrafx reference reset probe failed")
        row = json.loads(cp.stdout)
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
