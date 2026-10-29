#!/usr/bin/env python3
"""Issue #295: full-capture ADPCM reset probe runner (measurement only, local, not wired into CI).

Runs tests/integration/geargrafx_ad_reset_full.cpp on the pinned Geargrafx core (b49ae82a) with synthetic
BIOS/CD only. One trial per process. Records every JSON line as-is and checks only the protocol (exit
status, end state, echoed input hashes, pinned commit). It does not judge expected values; those belong
to a separate result document (contract docs/research/295-ad-reset-busy-contract.md, section 9.5 and 11).

Modes: pos-reset, play-reset-80 (PLAY_ACTIVE standard), neg-reset-playing (arm-B, not in the positive set).
TRANSFER_BUSY is BLOCKED and is refused here as well.

Usage (manual, not run in this change):
    python3 tests/integration/geargrafx_ad_reset_full.py --mode play-reset-80 --repeat 5
Each trial gets its own process. Series labels follow contract section 8: repeat 0 is "cold",
repeat 1 and later are "new-process". Warm series (two trials in one instance) are not implemented.
Outputs stay in a new temporary directory that this script never removes.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402

PINNED_COMMIT = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
MODES = ("pos-reset", "play-reset-80", "neg-reset-playing")
GUEST_BYTES = bytes([0x80, 0xFE])  # BRA * placed at WRAM $1E00 (guest $3E00)
TRIAL_TIMEOUT_S = 120              # contract 6: wall clock per trial, enforced by this runner
PROCESS_TIMEOUT_S = 600            # contract 6: wall clock for the whole invocation
EXIT_FAIL = 1


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_trial(probe, bios, cue, mode, bios_sha, cue_sha):
    """Run one trial in its own process. Returns the parsed record plus runner-side fields."""
    started = time.monotonic()
    try:
        result = subprocess.run([str(probe), str(bios), str(cue), mode, bios_sha, cue_sha],
                                timeout=TRIAL_TIMEOUT_S, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        # External kill at the wall-clock limit. The process state is not read after this (contract 6).
        return {"mode": mode, "end_state": "TIMEOUT", "end_reason": "runner_wall_clock",
                "exit_code": "NOT_CAPTURED", "record": "NOT_CAPTURED",
                "wall_s": round(time.monotonic() - started, 3)}
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    record = json.loads(lines[-1]) if lines else "NOT_CAPTURED"
    return {"mode": mode, "exit_code": result.returncode, "record": record,
            "end_state": record["end_state"] if isinstance(record, dict) else "NOT_CAPTURED",
            "wall_s": round(time.monotonic() - started, 3),
            "stderr_tail": result.stderr[-300:] if not lines else ""}


def check_protocol(trial, bios_sha, cue_sha):
    """Protocol checks only (R0-style). Returns a list of problems; empty means the record is usable."""
    problems = []
    record = trial["record"]
    if not isinstance(record, dict):
        return ["no JSON record on stderr"]
    if record.get("pinned_commit") != PINNED_COMMIT:
        problems.append("pinned_commit mismatch")
    if record.get("input_sha256") != {"bios": bios_sha, "cue": cue_sha}:
        problems.append("input hash echo mismatch")
    # The probe exits 0 only for COMPLETED. Other end states are valid, recorded outcomes (not protocol errors).
    expected_exit = 0 if record.get("end_state") == "COMPLETED" else 1
    if trial["exit_code"] != expected_exit:
        problems.append(f"exit {trial['exit_code']} does not match end_state {record.get('end_state')}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", action="append", default=None, help="one or more of " + ", ".join(MODES))
    parser.add_argument("--repeat", type=int, default=1, help="processes per mode (contract 8 series S: 5)")
    args = parser.parse_args()

    modes = args.mode or list(MODES)
    for mode in modes:
        if "busy" in mode or "transfer" in mode:
            raise ValueError(f"BLOCKED: {mode} is not implemented (TRANSFER_BUSY, contract 3.4)")
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
    if DEPENDENCIES["geargrafx"]["revision"] != PINNED_COMMIT:
        raise ValueError("tools/dependencies.json geargrafx revision is not the contract's pinned commit")
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")

    directory = Path(tempfile.mkdtemp(prefix="pce-ad-reset-full-"))  # not removed by this script
    bios, cue = synthetic_media(directory)
    bios_sha, cue_sha = sha256_file(bios), sha256_file(cue)
    guest_sha = hashlib.sha256(GUEST_BYTES).hexdigest()
    probe = compile_probe(directory, source_name="geargrafx_ad_reset_full.cpp",
                          target_name="ad-reset-full-probe")

    session_start = time.monotonic()
    failures = []
    for mode in modes:
        for repeat in range(args.repeat):
            if time.monotonic() - session_start > PROCESS_TIMEOUT_S:
                failures.append(f"{mode}#{repeat}: session wall clock exceeded")
                break
            trial = run_trial(probe, bios, cue, mode, bios_sha, cue_sha)
            trial["series"] = {"section8": "cold" if repeat == 0 else "new-process", "repeat": repeat}
            trial["hashes"] = {"bios_sha256": bios_sha, "cue_sha256": cue_sha, "guest_bytes_sha256": guest_sha,
                               "pinned_commit": DEPENDENCIES["geargrafx"]["revision"]}
            problems = check_protocol(trial, bios_sha, cue_sha) if trial["record"] != "NOT_CAPTURED" else [
                "no record"]
            trial["protocol_problems"] = problems
            print("TRIAL " + json.dumps(trial, sort_keys=True))
            if problems:
                failures.append(f"{mode}#{repeat}: {'; '.join(problems)}")

    print(f"OUTPUT_DIR {directory}")
    if failures:
        print("FAIL issue-295 full-capture probe (protocol): " + " | ".join(failures), file=sys.stderr)
        return EXIT_FAIL
    print("PASS issue-295 full-capture probe: protocol checks passed (measurement only; no verdict)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL issue-295 full-capture probe: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from error
