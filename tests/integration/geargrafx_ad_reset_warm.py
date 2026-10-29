#!/usr/bin/env python3
"""Issue #295: warm-series ADPCM reset probe runner (measurement only, local, not wired into CI).

Runs tests/integration/geargrafx_ad_reset_warm.cpp on the pinned Geargrafx core (b49ae82a) with synthetic
BIOS/CD only. One series = one process = two trials on one instance (contract section 14).

Window management and the fail-closed checks of the control lines are in warm_runner_window.py
(design phase2-timeout-design.md). This runner gives the probe a nonce (argv[5]), runs it through
run_series() with the contract 6.1 windows (trial 120 s from START, process 600 s from Popen), and checks the
window result against the probe's JSON line on stderr. A window TIMEOUT is decided here, never by the probe.

Scratch layout (phase 2): warm_runner_window.py lives in the parent directory of this file. In the repository the
window module is placed next to this file.

Usage (manual, not run in this change):
    python3 tests/integration/geargrafx_ad_reset_warm.py --repeat 5
Each series gets its own process. Outputs stay in a new temporary directory that this script never removes.
"""
import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path


import warm_runner_window as w  # noqa: E402

PINNED_COMMIT = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
GUEST_BYTES = bytes([0x80, 0xFE])  # BRA * placed at WRAM $1E00 (guest $3E00)
# Contract 6.1 constants (one definition). The probe carries FRAME_SAFETY_CAP and OBSERVE_WAIT_FRAMES; the two
# wall-clock limits are enforced here, outside the probe, and are not in the probe at all.
FRAME_SAFETY_CAP = 300             # frames per trial (probe: kFrameBudget)
OBSERVE_WAIT_FRAMES = 60           # frames allowed before PLAY_ACTIVE is observed (probe: kObserveWait)
TRIAL_WALL_SECONDS = 120           # per trial, from the START 1 / START 2 receive time (window module)
PROCESS_WALL_SECONDS = 600         # per process, from Popen (window module)
GATE_IDS = ("1", "2", "3", "4", "5", "6", "7", "8")
REASON_B = "dynamic_mpr_detection_not_implemented"
NONCE_BYTES = 16                   # token_hex(16): 32 characters of [0-9a-f], within the 8..64 rule
EXIT_FAIL = 1


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_nonce():
    return secrets.token_hex(NONCE_BYTES)


def check_probe_constants(probe_source):
    """Fail unless the probe carries the contract 6.1 values it still owns, and no wall-clock constant at all."""
    text = Path(probe_source).read_text(encoding="utf-8")
    expected = {"kFrameBudget": FRAME_SAFETY_CAP, "kObserveWait": OBSERVE_WAIT_FRAMES}
    for name, value in expected.items():
        match = re.search(rf"const (?:unsigned|double) {name} = ([0-9.]+);", text)
        if not match or float(match.group(1)) != float(value):
            raise ValueError(f"probe constant {name} does not match contract 6.1 value {value}")
    if re.search(r"\bk(?:Trial|Process)Seconds\b", text):
        raise ValueError("the probe still carries a wall-clock constant; the windows are external (contract 6.1)")


def check_window_limits():
    """The window module's contract limits must equal the contract 6.1 values held here."""
    limits = w.CONTRACT_LIMITS
    if limits.trial_window != TRIAL_WALL_SECONDS or limits.process_window != PROCESS_WALL_SECONDS:
        raise ValueError("warm_runner_window.CONTRACT_LIMITS does not match contract 6.1")


def probe_command(probe, bios, cue, bios_sha, cue_sha, nonce):
    """argv: bios cue bios_sha256 cue_sha256 nonce (the nonce is the probe's argv[5])."""
    return [str(probe), str(bios), str(cue), bios_sha, cue_sha, nonce]


def run_series(command, nonce, *, limits=w.CONTRACT_LIMITS, clock=time.monotonic, popen=None):
    """Run one series in its own process through the window machine. Returns the SERIES record (dict).

    The record keeps the keys of the earlier runner (exit_code, record, end_state, end_reason, wall_s,
    stderr_tail). end_state is the window result and is authoritative; the probe JSON is checked against it.
    stderr_tail is "": the window machine reads stderr line by line and keeps only the last JSON line."""
    started = time.monotonic()
    kwargs = {"clock": clock, "limits": limits}
    if popen is not None:
        kwargs["popen"] = popen
    win = w.run_series_with_windows(command, nonce, **kwargs)
    record = "NOT_CAPTURED"
    if win.json_text is not None:
        try:
            record = json.loads(win.json_text)
        except ValueError:
            record = "NOT_CAPTURED"
    if isinstance(record, dict) and record.get("end_state") == win.series_state:
        end_reason = record.get("end_reason")
    else:
        end_reason = win.series_reason
    return {"exit_code": win.exit_code, "record": record, "end_state": win.series_state,
            "end_reason": end_reason, "wall_s": round(time.monotonic() - started, 3),
            "stderr_tail": "", "nonce": nonce, "window": win.to_dict()}


def check_gate_record(record):
    """Option (b) JSON: series and both trials BLOCKED with the reason token (contract 14.13)."""
    problems = []
    if record.get("end_state") != "BLOCKED":
        problems.append("gate JSON end_state is not BLOCKED")
    if record.get("end_reason") != REASON_B:
        problems.append("gate JSON series end_reason is not " + REASON_B)
    trials = record.get("trials")
    if not isinstance(trials, list) or len(trials) != 2 or not all(isinstance(t, dict) for t in trials):
        problems.append("gate JSON must record exactly two trials")
        return problems
    for t in trials:
        if t.get("end_state") != "BLOCKED" or t.get("end_reason") != REASON_B:
            problems.append(f"gate JSON trial {t.get('index')} is not BLOCKED with {REASON_B}")
    return problems


def check_protocol(series, bios_sha, cue_sha):
    """Checks the window result against the probe JSON. Returns a list of problems; empty means usable.

    A window TIMEOUT, or the (b) gate BLOCKED, has no probe JSON by design (the process is killed, or the probe
    stops at the gate before any trial). Any other series must have a JSON record that agrees with the control
    lines: same end state, same two trials, same pinned commit and input hashes, the eight 14.12 items."""
    problems = []
    state = series["end_state"]
    win = series["window"]
    if win["errors"]:
        problems.extend("window: " + e for e in win["errors"])
    expected_exit = 0 if state == "COMPLETED" else 1
    if series["exit_code"] != expected_exit:
        problems.append(f"exit {series['exit_code']} does not match window state {state}")
    record = series["record"]
    if state == "TIMEOUT":
        if record != "NOT_CAPTURED":
            problems.append("a JSON record was kept for a window TIMEOUT")
        return problems
    if not isinstance(record, dict):
        problems.append("no JSON record on stderr")
        return problems
    if state == "BLOCKED" and win["series_reason"] == REASON_B:
        return problems + check_gate_record(record)
    if record.get("pinned_commit") != PINNED_COMMIT:
        problems.append("pinned_commit mismatch")
    if record.get("input_sha256") != {"bios": bios_sha, "cue": cue_sha}:
        problems.append("input hash echo mismatch")
    gate = record.get("conditions_14_12")
    if not isinstance(gate, list) or [c.get("id") for c in gate] != list(GATE_IDS):
        problems.append("section 14.12 must list conditions 1 to 8 in order")
    trials = record.get("trials")
    if not isinstance(trials, list) or len(trials) != 2 or not all(isinstance(t, dict) for t in trials):
        problems.append("a series must record exactly two trials")
    else:
        pids = [t.get("evidence", {}).get("pid") if isinstance(t.get("evidence"), dict) else None for t in trials]
        if not all(isinstance(p, int) and not isinstance(p, bool) for p in pids):
            problems.append("trial evidence pid must be an integer")
        elif len(set(pids)) != 1:
            problems.append("trials ran in different processes (identity evidence)")
        for t in trials:
            n = t.get("index")
            if n in (1, 2) and win["trials"].get(str(n), {}).get("state") != t.get("end_state"):
                problems.append(f"JSON trial {n} state does not match its control line")
    if record.get("end_state") != state:
        problems.append(f"JSON end_state {record.get('end_state')} does not match window state {state}")
    return problems


def finish_series(series, bios_sha, cue_sha):
    """Attach the protocol check. A JSON or control-line disagreement makes the series ABORTED (fail-closed); the
    window result is kept in window_state and window_end_reason so the disagreement stays visible."""
    series["protocol_problems"] = check_protocol(series, bios_sha, cue_sha)
    if series["protocol_problems"]:
        series["window_state"] = series["end_state"]
        series["window_end_reason"] = series["end_reason"]
        series["end_state"], series["end_reason"] = "ABORTED", "protocol_mismatch"
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=5,
                        help="series (processes). Contract 8 uses 5 warm series.")
    args = parser.parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    # The repository tools are needed only by main(); importing this module for tests does not need them.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from environment import DEPENDENCIES  # noqa: E402
    from geargrafx_api import compile_probe  # noqa: E402
    from geargrafx_reference_guard import synthetic_media  # noqa: E402

    if DEPENDENCIES["geargrafx"]["revision"] != PINNED_COMMIT:
        raise ValueError("tools/dependencies.json geargrafx revision is not the contract's pinned commit")
    check_window_limits()
    check_probe_constants(Path(__file__).resolve().with_name("geargrafx_ad_reset_warm.cpp"))

    directory = Path(tempfile.mkdtemp(prefix="pce-ad-reset-warm-"))  # not removed by this script
    bios, cue = synthetic_media(directory)
    bios_sha, cue_sha = sha256_file(bios), sha256_file(cue)
    guest_sha = hashlib.sha256(GUEST_BYTES).hexdigest()
    probe = compile_probe(directory, source_name="geargrafx_ad_reset_warm.cpp",
                          target_name="ad-reset-warm-probe")

    # No session-wide cap: every process has its own windows (run_series, contract 6.1).
    failures = []
    for repeat in range(args.repeat):
        nonce = new_nonce()
        series = run_series(probe_command(probe, bios, cue, bios_sha, cue_sha, nonce), nonce)
        series["series"] = {"section8": "warm-series", "repeat": repeat}
        series["hashes"] = {"bios_sha256": bios_sha, "cue_sha256": cue_sha, "guest_bytes_sha256": guest_sha,
                            "pinned_commit": DEPENDENCIES["geargrafx"]["revision"]}
        finish_series(series, bios_sha, cue_sha)
        print("SERIES " + json.dumps(series, sort_keys=True))
        if series["protocol_problems"]:
            failures.append(f"series#{repeat}: {'; '.join(series['protocol_problems'])}")

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
