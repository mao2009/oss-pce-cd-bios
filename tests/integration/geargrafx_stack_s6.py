#!/usr/bin/env python3
"""Issue #648 (S6 of #473): block transfers (TII/TIN/TDD/TIA/TAI) before the start and during the transfer.

Test policy T (probe tests/integration/geargrafx_stack_access_guard.cpp, method "AT" = A + T, verification-only
patch patches/geargrafx-b49ae82-stack-access-guard.patch, which adds only read-only transfer counter accessors):
  pre-start   at the instruction start of a block opcode with transfer state 0, the six operands are peeked without
              a bus read; every source and destination byte of the transfer (address sequences of the pinned
              source: TDD decrements both, TIN keeps the destination, TIA alternates the destination, TAI
              alternates the source; length 0 = 65536) must lie in a page mapped to WRAM $F8, else the whole
              instruction is refused before the opcode fetch (nothing changes). The stack precondition of
              TransferStart (MPR1 = $F8) is method A's check.
  mid-transfer at every later step of the same transfer (transfer state 2 or 1), no MPR may differ from its value
              at TransferStart, else the step is refused before it runs.
The guest is never resumed after a refusal. Every armed run is judged on the eight criteria of
geargrafx_stack_methods.py. Pre-start and mid-transfer results are reported separately. A mid-transfer refusal
stops INSIDE the transfer: it is BLOCKED (not safe to resume), never SAFE, even with no I/O side effect. The bytes
already transferred before the refusal are reported (counted, not hidden), with the transfer counters.

Mid-transfer MPR change paths on the pinned core: TAM cannot run inside a transfer (each byte step re-runs the
block opcode); an IRQ is not taken while the transfer state is not 0 (measured here for IRQ1 / IRQ2 / TIQ: the
handler starts after TransferEnd), so an IRQ handler cannot change an MPR mid-transfer on this core; what is left
is a harness direct SetMpr (S1 #497: any mid-run direct setting is a FAIL), measured as m (destination page) and m1
(MPR1) and, at the TransferEnd step, the #498 tend* rows.

PR #613's abort (verification-only block-write-guard patch, `make block-write-guard`) is re-evaluated from the
pinned source, not re-run here: it refuses after the opcode fetch, skips the six operands (PC + 7) and lets the guest
continue, so on the eight criteria the PC is not preserved and the guest runs after the refusal (it would not be
SAFE_IN_SCOPE); T refuses before the fetch and stops.

Not covered (UNKNOWN): device-originated RAM writes (CD/ADPCM DMA, #367 W17), ST0/ST1/ST2, an IRQ newly asserted in
the middle of a transfer step, the libretro build, real hardware. Synthetic BIOS/CD only. Not a production
mechanism; no production allowlist. #173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are
unchanged. The expectations below were written from the pinned source and a local exploratory build of the same
probe and patch (MinGW, supplementary) before the first CI run of this driver.

    python3 tests/integration/geargrafx_stack_s6.py      (make stack-s6; part of make integration)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_stack_methods as M  # noqa: E402
G = M.G

SAFE, BLOCKED, UNSAFE = M.SAFE, M.BLOCKED, M.UNSAFE
H, GUEST = G.HANDLER, G.GUEST
OPS = ("tii", "tin", "tdd", "tia", "tai")
# WRAM bytes written by a complete 4-byte transfer, by opcode (TIN: one fixed byte; TIA: two alternating bytes).
FULL = {"tii": 4, "tin": 1, "tdd": 4, "tia": 2, "tai": 4}
# Bytes already written when the step of byte 3 (instruction 4) is refused: two steps ran.
PARTIAL = {"tii": 2, "tin": 1, "tdd": 2, "tia": 2, "tai": 2}
IRQ_KINDS = {"q": 0x02, "q2": 0x01, "qt": 0x04}      # IRQ1 / IRQ2 / TIQ request bits
PRESTART_KINDS = ("d", "s")                           # forbidden destination page / source page
MID_KINDS = ("m", "m1")                               # destination page / MPR1 set before byte step 3
STEP3 = 4                                             # 1 TransferStart, 2-5 byte steps, 6 TransferEnd


def _e(verdict=SAFE, failing=(), index=1, pc=GUEST, **extra):
    return dict(verdict=verdict, failing=set(failing), index=index, pc=pc, **extra)


PRESTART_EXPECT = _e(reason="transfer_range", wram=0)
MID_EXPECT = {"m": "transfer_mpr_changed", "m1": "mpr1"}


def mid_expect(op, kind):
    return _e(BLOCKED, {"resume_safe"}, index=STEP3, transfer_state=2, legit_stack_bytes=3,
              reason=MID_EXPECT[kind], wram=PARTIAL[op])


# T-side mutants: case -> check that the forbidden side effect is visible (all judged UNSAFE).
def _resumed(row):
    return row.get("t_resumed") is True and row["instructions_after_refusal"] > 0 and row["io_after_refusal"] > 0


MUTANTS = {
    **{f"AT:x{op}m-mut-tresume": _resumed for op in OPS},          # guest resumed after the mid-transfer refusal
    "AT:xtiid-mut-tresume": _resumed,                              # ... after the pre-start refusal
    **{f"AT:x{op}m-mut-tnomid": (lambda r: r.get("refused") is False and r["io_writes"] == 2) for op in OPS},
    "AT:xcross-mut-tfirst": lambda r: r.get("refused") is False and r["io_writes"] == 2,   # first byte only
}


def _row_ok(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe failed (exit {row.get('exit')}: {row.get('stderr', '')[-120:]})")
    M._snapshot_fields(row, case)
    for name in ("wram_bytes_changed", "t_refusals", "io_after_refusal"):
        G._int(row, name)
    for name in ("t_at_refusal", "t_stop"):
        value = row.get(name)
        if type(value) is not list or len(value) != 4 or not all(type(v) is int for v in value):
            raise ValueError(f"{case}: malformed {name}")


def check_refused(row, case, expect):
    _row_ok(row, case)
    got, failing = M.check_armed(row, case, expect)
    if row.get("refusal_reason") != expect["reason"]:
        raise ValueError(f"{case}: refusal reason {row.get('refusal_reason')!r}, want {expect['reason']!r}")
    if row["wram_bytes_changed"] != expect["wram"]:
        raise ValueError(f"{case}: {row['wram_bytes_changed']} WRAM bytes changed, want {expect['wram']}")
    if expect.get("transfer_state"):
        # Inside the transfer: the counters at the refused step's start and at the stop must agree (the step did
        # not run), and half of the transfer must remain.
        if row["t_at_refusal"] != row["t_stop"] or row["t_stop"][2] != 2:
            raise ValueError(f"{case}: transfer counters {row['t_at_refusal']} -> {row['t_stop']}")
    return got, failing


def check_completed(row, case, op, wram, pc=0x1E07, s=0x04):
    """Allowed control: no refusal, no hardware page access, the whole transfer done in WRAM."""
    _row_ok(row, case)
    if row.get("refused") is not False or row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: allowed transfer refused or touched the hardware page (false positive)")
    if (row["stop"]["pc"], row["stop"]["s"], row["wram_bytes_changed"]) != (pc, s, wram):
        raise ValueError(f"{case}: stop PC/S/WRAM {row['stop']['pc']:#x}/{row['stop']['s']:#x}/"
                         f"{row['wram_bytes_changed']}, want {pc:#x}/{s:#x}/{wram}")
    if row["t_stop"][2] != 0:
        raise ValueError(f"{case}: transfer length left {row['t_stop'][2]}")


def check_control(row, case, reads=0, writes=0):
    """Method A alone (or no guard): the transfer reaches the hardware page."""
    _row_ok(row, case)
    if row.get("refused") is not False or (row["io_reads"], row["io_writes"]) != (reads, writes):
        raise ValueError(f"{case}: control I/O {row['io_reads']}r/{row['io_writes']}w, want {reads}r/{writes}w")
    if M.verdict(row, 3)[0] != UNSAFE:
        raise ValueError(f"{case}: control not judged UNSAFE")


def check_irq(row, case, irr):
    """IRQ pending through the transfer: no entry while the transfer state is not 0; entry after TransferEnd."""
    check_completed(row, case, case.split(":")[1][1:4], FULL[case.split(":")[1][1:4]], pc=H, s=0x01)
    if row.get("handler_entry_instruction") != STEP3 + 3 or row.get("irr_before") != irr:
        raise ValueError(f"{case}: handler entry {row.get('handler_entry_instruction')} (IRR {row.get('irr_before')}), "
                         f"want {STEP3 + 3}: after TransferEnd")


def _cases():
    for method in ("A", "AT"):
        for op in OPS:
            for kind in (*PRESTART_KINDS, *MID_KINDS):
                yield f"{method}:x{op}{kind}-armed"
                yield f"{method}:x{op}{kind}-allowed"
            for kind in IRQ_KINDS:
                yield f"{method}:x{op}{kind}-allowed"
        for route in ("xcross", "xlen0"):
            yield f"{method}:{route}-armed"
        yield f"{method}:xcross-allowed"
    yield "AT:xlen0-allowed"
    for op in OPS:
        for kind in PRESTART_KINDS:
            yield f"A:x{op}{kind}-unguarded"
    for route in M.ROUTES:   # regression: every #498 route under A+T, with its allowed control
        yield f"AT:{route}-armed"
        yield f"AT:{route}-allowed"
    yield from MUTANTS


def cases():
    return list(dict.fromkeys(_cases()))


def evaluate(rows):
    prestart, mid, irq, controls, detections = {}, {}, {}, [], []
    for op in OPS:
        for kind in PRESTART_KINDS:
            case = f"AT:x{op}{kind}-armed"
            prestart[(op, kind)] = check_refused(rows[case], case, PRESTART_EXPECT)
            for method in ("A", "AT"):
                check_completed(rows[f"{method}:x{op}{kind}-allowed"], f"{method}:x{op}{kind}-allowed", op, FULL[op])
            io = dict(reads=4) if kind == "s" else dict(writes=4)
            for case in (f"A:x{op}{kind}-armed", f"A:x{op}{kind}-unguarded"):
                check_control(rows[case], case, **io)
                controls.append((case, UNSAFE))
        for kind in MID_KINDS:
            case = f"AT:x{op}{kind}-armed"
            mid[(op, kind)] = check_refused(rows[case], case, mid_expect(op, kind))
            for method in ("A", "AT"):
                check_completed(rows[f"{method}:x{op}{kind}-allowed"], f"{method}:x{op}{kind}-allowed", op, FULL[op])
        mid[(op, "m1", "A")] = check_refused(rows[f"A:x{op}m1-armed"], f"A:x{op}m1-armed", mid_expect(op, "m1"))
        check_control(rows[f"A:x{op}m-armed"], f"A:x{op}m-armed", writes=2)   # A alone: the rest lands on I/O
        controls.append((f"A:x{op}m-armed", UNSAFE))
        for kind, irr in IRQ_KINDS.items():
            for method in ("A", "AT"):
                case = f"{method}:x{op}{kind}-allowed"
                check_irq(rows[case], case, irr)
                irq[(op, kind, method)] = rows[case]["handler_entry_instruction"]
    for route in ("xcross", "xlen0"):
        prestart[(route, "range")] = check_refused(rows[f"AT:{route}-armed"], f"AT:{route}-armed", PRESTART_EXPECT)
    # Length 0 (65536 bytes) covers ROM pages: refused under the test policy even with every page under test in WRAM.
    prestart[("xlen0", "allowed")] = check_refused(rows["AT:xlen0-allowed"], "AT:xlen0-allowed", PRESTART_EXPECT)
    check_completed(rows["AT:xcross-allowed"], "AT:xcross-allowed", "tii", 4)
    check_control(rows["A:xcross-armed"], "A:xcross-armed", writes=2)
    controls.append(("A:xcross-armed", UNSAFE))
    for key, (got, _) in prestart.items():   # every SAFE pre-start row must fail when any criterion is broken
        case = f"AT:x{key[0]}{key[1]}-armed" if key[0] in OPS else (
            "AT:xlen0-allowed" if key == ("xlen0", "allowed") else f"AT:{key[0]}-armed")
        if got == SAFE:
            for name, broken in M.falsified(rows[case]):
                if M.verdict(broken)[0] == SAFE:
                    raise ValueError(f"{case}: falsified {name} still judged SAFE")
                detections.append((case, f"falsified:{name}", M.verdict(broken)[0]))
    regression = {}
    for route in M.ROUTES:
        case = f"AT:{route}-armed"
        regression[route] = M.check_armed(rows[case], case, M.EXPECT_A[route])
        M.check_allowed(rows[f"AT:{route}-allowed"], f"AT:{route}-allowed", route)
    for case, visible in MUTANTS.items():
        _row_ok(rows[case], case)
        got = M.verdict(rows[case], 3)[0]
        if got != UNSAFE or not visible(rows[case]):
            raise ValueError(f"mutation {case}: judged {got} (visible={visible(rows[case])}), want UNSAFE")
        detections.append((case, "mutant", got))
    return prestart, mid, irq, regression, controls, detections


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-s6-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
    prestart, mid, irq, regression, controls, detections = evaluate(rows)
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    for case, result in controls:
        print(f"CONTROL {case}: {result} (no T: the transfer reaches the hardware page)")
    for (op, kind), (got, failing) in prestart.items():
        print(f"PRESTART {op} {kind}: A+T={got}({','.join(failing) or '-'}) refused before the opcode fetch")
    for key, (got, failing) in mid.items():
        op, kind = key[0], key[1]
        method = key[2] if len(key) > 2 else "A+T"
        row = rows[f"{'A' if method == 'A' else 'AT'}:x{op}{kind}-armed"]
        print(f"MIDTRANSFER {op} {kind} {method}: {got}({','.join(failing)}) reason={row['refusal_reason']} "
              f"bytes_already_written={row['wram_bytes_changed']} counters(src,dst,len,count)={row['t_stop']}")
    for (op, kind, method), entry in irq.items():
        print(f"IRQ {op} {kind} {method}: handler entered at instruction {entry} (after TransferEnd)")
    print("REGRESSION A+T on the #498 routes: " + ", ".join(f"{r}={v}" for r, (v, _) in regression.items()))
    print(f"EXPECTATIONS_MET issue-648 S6 block transfers: {len(rows)} real-core runs, {len(detections)} detections, "
          f"expectations met (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only patch)")
    print("RESULT issue-648 pre-start (observed fixtures only): T refuses a TII/TIN/TDD/TIA/TAI whose source or "
          "destination range reaches a page not mapped to WRAM $F8 (including a range crossing a page boundary and "
          "length 0 = 65536) before the opcode fetch: SAFE_IN_SCOPE on all eight criteria. Without T (method A only) "
          "the same transfers reach the hardware page (UNSAFE controls).")
    print("RESULT issue-648 mid-transfer: a destination page or MPR1 change before a byte step is refused before "
          "that step (no hardware page access), but the stop is inside the transfer: BLOCKED (not safe to resume). "
          "The bytes already written are reported, not hidden. Resuming after the refusal (mutant) lets the rest "
          "of the transfer reach the hardware page (UNSAFE). An IRQ is not taken during a transfer (IRQ1/IRQ2/TIQ: "
          "entry after TransferEnd), so an IRQ handler cannot change an MPR mid-transfer on the pinned core.")
    print("UNKNOWN (not covered): device-originated RAM writes (CD/ADPCM DMA, #367 W17), ST0/ST1/ST2, an IRQ newly "
          "asserted inside a transfer step, the libretro build, real hardware. PR #613's abort is re-evaluated from "
          "the source only (PC + 7 and the guest continues: not SAFE_IN_SCOPE). Not a production mechanism; no "
          "production allowlist; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL issue-648 S6: {error}", file=sys.stderr)
        raise SystemExit(1) from error
