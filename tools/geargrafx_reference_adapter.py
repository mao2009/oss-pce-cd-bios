#!/usr/bin/env python3
"""Local Geargrafx reference reset observer; no functional API execution is enabled.

The test-ROM digest is checked by the driver but the test ROM is NOT executed.
The experimental slot-1e guest JSR is disabled until actual in-core RAM and I/O
write enforcement exists. A post-run empty ram_diff is not such enforcement.
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
    """Reject unsupported JSR execution before compilation or loading private BIOS.

    An instruction budget and a post-run RAM diff cannot prove that intermediate
    RAM writes or memory-mapped I/O side effects respected an allowlist.
    The C++ proof of concept is retained but disconnected until the core can
    enforce and independently report all writes while it executes.
    """
    if config.get("api_id") != "slot-1e":
        raise ValueError("BLOCKED: reference JSR experiment supports only slot-1e")
    budget = config.get("max_instructions")
    if type(budget) is not int or not 3 <= budget <= 1000000:
        raise ValueError("BLOCKED: invalid JSR instruction budget")
    raise ValueError("BLOCKED: slot-1e runtime RAM/I/O write guard is NOT_IMPLEMENTED")


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
