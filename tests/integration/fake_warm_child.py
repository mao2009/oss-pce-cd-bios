#!/usr/bin/env python3
"""Simulated warm-probe child for the Phase 2 window tests (no Geargrafx, no ROM, no WSL).

Each mode is a schedule: a list of (t, stream, text) in "units" (seconds at scale 1.0), a return code, and the unit
at which the child exits if it has not been killed (`hang`). The same schedule drives the fake-clock tests
(test_warm_window.py feeds it to WarmMachine) and the real-time run (this script prints it at `--scale` seconds
per unit, flushing every line). A stream "close" closes stdout and stderr at that time.

Control lines: "@@WARM <nonce> <seq> START|END|FINAL ..." on stdout (seq on every line, design section 6).
The only control line without a seq is the gate line "@@WARM <nonce> FINAL BLOCKED dynamic_mpr_detection_not_implemented".

Usage: python fake_warm_child.py MODE NONCE [--scale S] [--noise N]
"""
import argparse
import json
import sys
import time

REASON_B = "dynamic_mpr_detection_not_implemented"
FAKE_PINNED = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
FAKE_BIOS_SHA = "a" * 64          # the integration tests pass these as the input hashes
FAKE_CUE_SHA = "b" * 64
NORMAL_JSON = {
    "end_state": "COMPLETED",
    "end_reason": "normal_observation_complete",
    "pinned_commit": FAKE_PINNED,
    "input_sha256": {"bios": FAKE_BIOS_SHA, "cue": FAKE_CUE_SHA},
    "conditions_14_12": [{"id": str(i), "status": "PASS"} for i in range(1, 9)],
    "trials": [{"index": 1, "end_state": "COMPLETED", "evidence": {"pid": 1}},
               {"index": 2, "end_state": "COMPLETED", "evidence": {"pid": 1}}],
}
TRIAL_TIMEOUT_HANG = 10.0    # 10 x the trial window (1.0 unit)
PROCESS_TIMEOUT_HANG = 30.0  # 10 x the process window (3.0 units)


def ctl(nonce, seq, body):
    return f"@@WARM {nonce} {seq} {body}"


def jtext(obj):
    return json.dumps(obj, sort_keys=True)


def gate_json(reason=REASON_B):
    """The probe's JSON under contract 14.13 option (b): series and both trials BLOCKED with the reason token."""
    trials = [{"index": i, "role": r, "end_state": "BLOCKED", "end_reason": reason,
               "evidence": {"pid": 1}} for i, r in ((1, "identity-and-first-trial"), (2, "second-trial"))]
    return {"end_state": "BLOCKED", "end_reason": reason, "series": "warm", "pinned_commit": FAKE_PINNED,
            "input_sha256": {"bios": FAKE_BIOS_SHA, "cue": FAKE_CUE_SHA}, "trials": trials}


def normal_events(nonce, start_seq=1):
    s = start_seq
    return [
        (0.0, "out", ctl(nonce, s, "START 1")),
        (0.1, "out", ctl(nonce, s + 1, "END 1 COMPLETED")),
        (0.2, "out", ctl(nonce, s + 2, "START 2")),
        (0.3, "out", ctl(nonce, s + 3, "END 2 COMPLETED")),
        (0.4, "out", ctl(nonce, s + 4, "FINAL COMPLETED")),
        (0.4, "err", jtext(NORMAL_JSON)),
    ]


def schedule(mode, nonce, noise=3000):
    """Return (events, rc, hang). events are (t_units, stream, text) and must be in time order."""
    n = nonce
    if mode == "normal":
        return normal_events(n), 0, 0.4
    if mode == "trial1_timeout":
        return [(0.0, "out", ctl(n, 1, "START 1"))], 0, TRIAL_TIMEOUT_HANG
    if mode in ("trial2_timeout", "trial2_timeout_after_trial1_completed"):
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 COMPLETED")),
                (0.2, "out", ctl(n, 3, "START 2"))], 0, TRIAL_TIMEOUT_HANG
    if mode == "process_timeout":
        return [], 0, PROCESS_TIMEOUT_HANG
    if mode == "process_timeout_after_end2":
        # both trials ended, no FINAL: the process window must end the series, not ABORTED
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 COMPLETED")),
                (0.2, "out", ctl(n, 3, "START 2")),
                (0.3, "out", ctl(n, 4, "END 2 COMPLETED"))], 0, PROCESS_TIMEOUT_HANG
    if mode == "close_fds_hang":
        # both pipes are closed (EOF on both readers), then the child sleeps: only the process window can end it
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 COMPLETED")),
                (0.2, "close", "")], 0, PROCESS_TIMEOUT_HANG
    if mode == "exit3":
        return [], 3, 0.1
    if mode == "midway":
        return [(0.0, "out", ctl(n, 1, "START 1"))], 0, 0.1
    if mode == "seq_missing":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 3, "END 1 COMPLETED"))], 0, 0.2
    if mode == "seq_dup":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 1, "END 1 COMPLETED"))], 0, 0.2
    if mode == "malformed":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", f"@@WARM {n} x END 1 COMPLETED")], 0, 0.2
    if mode == "order_swap":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 COMPLETED")),
                (0.2, "out", ctl(n, 3, "END 2 COMPLETED")),
                (0.3, "out", ctl(n, 4, "START 2")),
                (0.4, "out", ctl(n, 5, "FINAL COMPLETED")),
                (0.4, "err", jtext(NORMAL_JSON))], 0, 0.4
    if mode == "no_start_marker":
        return [(0.1, "err", jtext(NORMAL_JSON))], 0, 0.2
    if mode == "state_mismatch":
        bad = {"end_state": "COMPLETED",
               "trials": [{"index": 1, "end_state": "BUDGET_EXCEEDED"}, {"index": 2, "end_state": "COMPLETED"}]}
        return normal_events(n)[:-1] + [(0.4, "err", jtext(bad))], 0, 0.4
    if mode == "json_duplicate":
        return normal_events(n) + [(0.4, "err", jtext(NORMAL_JSON))], 0, 0.4
    if mode == "late_end_after_kill":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (1.5, "out", ctl(n, 2, "END 1 COMPLETED"))], 0, TRIAL_TIMEOUT_HANG
    if mode == "noise_flood":
        block_out = "\n".join(f"probe log line {i} ................................" for i in range(noise))
        block_err = "\n".join(f"stderr log line {i} ................................" for i in range(noise))
        return [(0.0, "out", block_out), (0.0, "err", block_err)] + normal_events(n), 0, 0.4
    if mode == "nonce_wrong":
        return [(0.0, "out", ctl("bad-nonce", 1, "START 1")),
                (0.1, "out", ctl("bad-nonce", 2, "END 1 COMPLETED")),
                (0.2, "out", ctl("bad-nonce", 3, "START 2")),
                (0.3, "out", ctl("bad-nonce", 4, "END 2 COMPLETED")),
                (0.4, "out", ctl("bad-nonce", 5, "FINAL COMPLETED")),
                (0.4, "err", jtext(NORMAL_JSON))], 0, 0.4
    if mode == "final_after_extra":
        return normal_events(n) + [(0.5, "out", ctl(n, 6, "END 2 COMPLETED"))], 0, 0.5
    if mode == "final_no_json":
        return normal_events(n)[:-1], 0, 0.4
    if mode == "rc0_no_final":
        return normal_events(n)[:-2], 0, 0.4
    if mode == "unknown_end_state":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 WEIRD"))], 0, 0.2
    if mode == "trial_number_mismatch":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 2 COMPLETED"))], 0, 0.2
    if mode == "b_final_only":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED {REASON_B}"),
                (0.0, "err", jtext(gate_json()))], 1, 0.1
    if mode == "b_final_no_json":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED {REASON_B}")], 1, 0.1
    if mode == "b_json_wrong_reason":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED {REASON_B}"),
                (0.0, "err", jtext(gate_json(reason="something_else")))], 1, 0.1
    if mode == "pid_string":
        bad = json.loads(jtext(NORMAL_JSON))
        bad["trials"][0]["evidence"]["pid"] = "1"
        return normal_events(n)[:-1] + [(0.4, "err", jtext(bad))], 0, 0.4
    if mode == "b_with_start":
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", f"@@WARM {n} FINAL BLOCKED {REASON_B}")], 1, 0.2
    if mode == "b_bad_reason":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED something_else")], 1, 0.1
    if mode == "b_no_reason":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED")], 1, 0.1
    if mode == "b_then_end":
        return [(0.0, "out", f"@@WARM {n} FINAL BLOCKED {REASON_B}"),
                (0.1, "out", ctl(n, 1, "START 1"))], 1, 0.2
    if mode == "trial2_window_cut_by_process":
        # START 2 at 2.5 units: its window would end at 3.5, the process window ends at 3.0
        return [(0.0, "out", ctl(n, 1, "START 1")),
                (0.1, "out", ctl(n, 2, "END 1 COMPLETED")),
                (2.5, "out", ctl(n, 3, "START 2"))], 0, PROCESS_TIMEOUT_HANG
    raise ValueError(f"unknown mode {mode!r}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("nonce")
    parser.add_argument("--scale", type=float, default=1.0, help="seconds per schedule unit")
    parser.add_argument("--noise", type=int, default=3000)
    args = parser.parse_args(argv)
    events, rc, hang = schedule(args.mode, args.nonce, args.noise)
    start = time.monotonic()
    for t, stream, text in sorted(events, key=lambda e: e[0]):
        delay = start + t * args.scale - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if stream == "close":
            sys.stdout.close()
            sys.stderr.close()
            continue
        out = sys.stdout if stream == "out" else sys.stderr
        out.write(text if text.endswith("\n") else text + "\n")
        out.flush()
    delay = start + hang * args.scale - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return rc


if __name__ == "__main__":
    sys.exit(main())
