#!/usr/bin/env python3
"""Issue #295: warm-series ADPCM reset probe runner (measurement only, local, not wired into CI).

Runs tests/integration/geargrafx_ad_reset_warm.cpp on the pinned Geargrafx core (b49ae82a) with synthetic
BIOS/CD only. One series = one process = two trials on one instance (contract section 14: trial 1 is the
warm-up, trial 2 is the only warm trial). Records every JSON line as-is and checks only the protocol (exit
status against the series end state, echoed input hashes, pinned commit, the eight section 14.12 items).
It does not judge expected values; those belong to a separate result document (contract section 9.5 and 11).

TRANSFER_BUSY is BLOCKED and is refused here. The 25 existing trials (geargrafx_ad_reset_full.py) are a
separate series and are not read or rewritten by this script.

Usage (manual, not run in this change):
    python3 tests/integration/geargrafx_ad_reset_warm.py --repeat 5
Each series gets its own process. Outputs stay in a new temporary directory that this script never removes.
"""
import argparse
import hashlib
import json
import re
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
GUEST_BYTES = bytes([0x80, 0xFE])  # BRA * placed at WRAM $1E00 (guest $3E00)
# Contract 6.1 constants (the single definition). The C++ probe must carry the same values; main() checks it.
FRAME_SAFETY_CAP = 300             # frames per trial (probe: kFrameBudget)
OBSERVE_WAIT_FRAMES = 60           # frames allowed before PLAY_ACTIVE is observed (probe: kObserveWait)
TRIAL_WALL_SECONDS = 120           # per trial, enforced by the probe at frame boundaries (probe: kTrialSeconds)
PROCESS_WALL_SECONDS = 600         # per process, which holds two trials for warm (probe: kProcessSeconds)
GATE_IDS = ("1", "2", "3", "4", "5", "6", "7", "8")
EXIT_FAIL = 1


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_probe_constants():
    """Fail unless the C++ probe carries the contract 6.1 values (one definition, two readers)."""
    text = (Path(__file__).resolve().parent / "geargrafx_ad_reset_warm.cpp").read_text(encoding="utf-8")
    expected = {"kFrameBudget": FRAME_SAFETY_CAP, "kObserveWait": OBSERVE_WAIT_FRAMES,
                "kTrialSeconds": TRIAL_WALL_SECONDS, "kProcessSeconds": PROCESS_WALL_SECONDS}
    for name, value in expected.items():
        match = re.search(rf"const (?:unsigned|double) {name} = ([0-9.]+);", text)
        if not match or float(match.group(1)) != float(value):
            raise ValueError(f"probe constant {name} does not match contract 6.1 value {value}")


def run_series(probe, bios, cue, bios_sha, cue_sha):
    """Run one series in its own process. Returns the parsed record plus runner-side fields."""
    started = time.monotonic()
    try:
        result = subprocess.run([str(probe), str(bios), str(cue), bios_sha, cue_sha],
                                timeout=PROCESS_WALL_SECONDS, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        # External kill at the wall-clock limit. The process state is not read after this (contract 6).
        return {"exit_code": "NOT_CAPTURED", "record": "NOT_CAPTURED", "end_state": "TIMEOUT",
                "end_reason": "runner_wall_clock", "wall_s": round(time.monotonic() - started, 3)}
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    record = json.loads(lines[-1]) if lines else "NOT_CAPTURED"
    return {"exit_code": result.returncode, "record": record,
            "end_state": record["end_state"] if isinstance(record, dict) else "NOT_CAPTURED",
            "end_reason": record.get("end_reason") if isinstance(record, dict) else "NOT_CAPTURED",
            "wall_s": round(time.monotonic() - started, 3),
            "stderr_tail": result.stderr[-300:] if not lines else ""}


def check_protocol(series, bios_sha, cue_sha):
    """Protocol checks only. Returns a list of problems; empty means the record is usable."""
    problems = []
    record = series["record"]
    if not isinstance(record, dict):
        return ["no JSON record on stderr"]
    if record.get("pinned_commit") != PINNED_COMMIT:
        problems.append("pinned_commit mismatch")
    if record.get("input_sha256") != {"bios": bios_sha, "cue": cue_sha}:
        problems.append("input hash echo mismatch")
    gate = record.get("conditions_14_12")
    if not isinstance(gate, list) or [c.get("id") for c in gate] != list(GATE_IDS):
        problems.append("section 14.12 must list conditions 1 to 8 in order")
    trials = record.get("trials")
    if not isinstance(trials, list) or len(trials) != 2:
        problems.append("a series must record exactly two trials")
    else:
        pids = {t.get("evidence", {}).get("pid") for t in trials}
        if len(pids) != 1:
            problems.append("trials ran in different processes (identity evidence)")
    # The probe exits 0 only for a COMPLETED series. Other end states are recorded outcomes, not protocol errors.
    expected_exit = 0 if record.get("end_state") == "COMPLETED" else 1
    if series["exit_code"] != expected_exit:
        problems.append(f"exit {series['exit_code']} does not match end_state {record.get('end_state')}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=5,
                        help="series (processes). Contract 8 uses 5 warm series.")
    args = parser.parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    if DEPENDENCIES["geargrafx"]["revision"] != PINNED_COMMIT:
        raise ValueError("tools/dependencies.json geargrafx revision is not the contract's pinned commit")
    check_probe_constants()

    directory = Path(tempfile.mkdtemp(prefix="pce-ad-reset-warm-"))  # not removed by this script
    bios, cue = synthetic_media(directory)
    bios_sha, cue_sha = sha256_file(bios), sha256_file(cue)
    guest_sha = hashlib.sha256(GUEST_BYTES).hexdigest()
    probe = compile_probe(directory, source_name="geargrafx_ad_reset_warm.cpp",
                          target_name="ad-reset-warm-probe")

    # No session-wide cap: each process has its own external timeout (PROCESS_WALL_SECONDS, run_series).
    failures = []
    for repeat in range(args.repeat):
        series = run_series(probe, bios, cue, bios_sha, cue_sha)
        series["series"] = {"section8": "warm-series", "repeat": repeat}
        series["hashes"] = {"bios_sha256": bios_sha, "cue_sha256": cue_sha, "guest_bytes_sha256": guest_sha,
                            "pinned_commit": DEPENDENCIES["geargrafx"]["revision"]}
        problems = check_protocol(series, bios_sha, cue_sha) if series["record"] != "NOT_CAPTURED" else [
            "no record"]
        series["protocol_problems"] = problems
        print("SERIES " + json.dumps(series, sort_keys=True))
        if problems:
            failures.append(f"series#{repeat}: {'; '.join(problems)}")

    print(f"OUTPUT_DIR {directory}")
    if failures:
        print("FAIL issue-295 warm probe (protocol): " + " | ".join(failures), file=sys.stderr)
        return EXIT_FAIL
    print("PASS issue-295 warm probe: protocol checks passed (measurement only; no verdict)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL issue-295 warm probe: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from error
