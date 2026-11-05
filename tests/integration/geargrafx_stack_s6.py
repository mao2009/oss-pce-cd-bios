#!/usr/bin/env python3
"""Issue #648 (S6 of #473): block transfers (TII/TIN/TDD/TIA/TAI) before the start and during the transfer.

Test policy T (probe tests/integration/geargrafx_stack_access_guard.cpp, method "AT" = A + T, verification-only
patch patches/geargrafx-b49ae82-stack-access-guard.patch, which adds only read-only transfer counter accessors):
  pre-start   at the instruction start of a block opcode with transfer state 0, the six operands are peeked without
              a bus read. Every source and destination byte of the transfer (address sequences of the pinned
              source: TDD decrements both, TIN keeps the destination, TIA alternates the destination, TAI
              alternates the source; length 0 = 65536) must lie in a page mapped to WRAM $F8, and no destination
              byte may be one of the instruction's own seven bytes (each byte step re-runs the opcode); else the
              whole instruction is refused before the opcode fetch (nothing changes). The stack precondition of
              TransferStart (MPR1 = $F8) is method A's check. An opcode that cannot be peeked is refused (fail
              closed).
  mid-transfer at every later step of the same transfer (transfer state 2 or 1), the opcode must be the one of
              TransferStart and no MPR may differ from its value at TransferStart, else the step is refused before
              it runs.
The guest is never resumed after a refusal. Every armed run is judged on the eight criteria of
geargrafx_stack_methods.py. Pre-start and mid-transfer results are reported separately. A mid-transfer refusal
stops INSIDE the transfer: it is BLOCKED (not safe to resume), never SAFE, even with no I/O side effect. The bytes
already transferred before the refusal are reported (counted, not hidden), with the transfer counters.

Mid-transfer MPR change paths on the pinned core: TAM cannot run inside a transfer unless the transfer rewrites its
own opcode (refused before the start by T; the mid-transfer opcode check is shown with that check disabled); an
IRQ is not taken while the transfer state is not 0 (measured here for IRQ1 / IRQ2 / TIQ: the handler starts after
TransferEnd), so an IRQ handler cannot change an MPR mid-transfer on this core; what is left is a harness direct
SetMpr (S1 #497: any mid-run direct setting is a FAIL), measured as m (destination page, before byte step 3), m1
(MPR1, before byte step 3), e (destination page, before the TransferEnd step) and the #498 tend* rows (MPR1 before
the TransferEnd step).

PR #613 (verification-only block-write-guard patch, `make block-write-guard`) is re-evaluated from the pinned
source, not re-run here. Its whole-instruction abort refuses after the opcode fetch, skips the six operands (PC + 7)
and lets the guest continue; its per-byte write guard skips a byte write and continues the transfer. On the eight
criteria the PC / transfer state then differ from the refused instruction's start and the guest runs after the
refusal, so neither would be SAFE_IN_SCOPE; T refuses before the fetch and stops.

push16 / pop ordering and latching (#644 mutants late16, popinc, nolatch, ignore, nostop) are covered by the #499
driver (make stack-access-guard) on the same probe and patch in the same make integration; not repeated here.

Not covered (UNKNOWN): device-originated RAM writes (CD/ADPCM DMA, #367 W17), ST0/ST1/ST2, an IRQ newly asserted in
the middle of a transfer step, the libretro build, real hardware. Synthetic BIOS/CD only. Not a production
mechanism; no production allowlist. #173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are
unchanged. The expectations were written from the pinned source and a local exploratory run of an earlier revision
of the probe (MinGW, supplementary); the final probe revision and this driver first run in CI.

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
# WRAM bytes changed by a complete 4-byte transfer, by opcode (TIN: one fixed byte; TIA: two alternating bytes).
# The same counts hold for the d, s, c (allowed) and w fixtures (worked out from the fixture bytes).
FULL = {"tii": 4, "tin": 1, "tdd": 4, "tia": 2, "tai": 4}
# Bytes already written when the step of byte 3 (instruction 4) is refused: two steps ran.
PARTIAL = {"tii": 2, "tin": 1, "tdd": 2, "tia": 2, "tai": 2}
IRQ_KINDS = {"q": 0x02, "q2": 0x01, "qt": 0x04}      # IRQ1 / IRQ2 / TIQ request bits
PRESTART_KINDS = ("d", "s", "c")                      # destination page / source page / range crossing into it
MID_KINDS = ("m", "m1", "e")                          # dest page / MPR1 before byte step 3; dest page before TransferEnd
STEP3, TEND = 4, 6                                    # 1 TransferStart, 2-5 byte steps, 6 TransferEnd
# Hardware page accesses of the c fixtures without T (2 of the 4 bytes reach the forbidden page).
CROSS_IO = {"tii": dict(writes=2), "tin": dict(reads=2), "tdd": dict(writes=2), "tia": dict(writes=2),
            "tai": dict(reads=2)}
SINGLE_IO = {"d": dict(writes=4), "s": dict(reads=4)}


def _e(verdict=SAFE, failing=(), index=1, pc=GUEST, **extra):
    return dict(verdict=verdict, failing=set(failing), index=index, pc=pc, **extra)


PRESTART_EXPECT = _e(reason="transfer_range", wram=0)
SELF_EXPECT = _e(reason="transfer_self", wram=0)


def mid_expect(op, kind):
    at_end = kind == "e"
    return _e(BLOCKED, {"resume_safe"}, index=TEND if at_end else STEP3, transfer_state=1 if at_end else 2,
              legit_stack_bytes=3, reason="mpr1" if kind == "m1" else "transfer_mpr_changed",
              wram=FULL[op] if at_end else PARTIAL[op], remaining=0 if at_end else 2)


# Mid-transfer opcode check, shown with the pre-start own-opcode check disabled: the first byte step rewrites the
# TII opcode at $1E00; the next step is refused (BLOCKED inside the transfer, one byte written, three remaining).
SELF_MID_CASE = "AT:xself-mut-tnoself"
SELF_MID_EXPECT = _e(BLOCKED, {"resume_safe"}, index=3, transfer_state=2, legit_stack_bytes=3,
                     reason="transfer_opcode_changed", wram=1, remaining=3)


def _resumed(row):
    return row.get("t_resumed") is True and row["instructions_after_refusal"] > 0 and row["io_after_refusal"] > 0


def _landed(row):
    return row.get("refused") is False and row["io_writes"] == 2


# T-side mutants that must be judged UNSAFE: case -> check that the forbidden side effect is visible.
MUTANTS = {
    **{f"AT:x{op}m-mut-tresume": _resumed for op in OPS},          # guest resumed after the mid-transfer refusal
    "AT:xtiid-mut-tresume": _resumed,                              # ... after the pre-start refusal
    **{f"AT:x{op}m-mut-tnomid": _landed for op in OPS},            # no mid-transfer check
    "AT:xtiic-mut-tfirst": _landed,                                # pre-start check of the first byte only
    "AT:xtddc-mut-tseq": _landed,                                  # TDD modelled as incrementing
}
# Wrong address model on the w fixtures: refused although the pinned sequence never reaches the forbidden page.
FALSE_POSITIVE_MUTANTS = ("AT:xtinw-mut-tseq", "AT:xtiaw-mut-tseq", "AT:xtaiw-mut-tseq")


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
    if "remaining" in expect:
        # Inside the transfer: the counters at the refused step's start and at the stop must agree (the step did
        # not run), and the expected part of the transfer must remain.
        if row["t_at_refusal"] != row["t_stop"] or row["t_stop"][2] != expect["remaining"]:
            raise ValueError(f"{case}: transfer counters {row['t_at_refusal']} -> {row['t_stop']}, "
                             f"want {expect['remaining']} remaining")
    return got, failing


def check_completed(row, case, wram, pc=0x1E07, s=0x04):
    """No refusal, no hardware page access, the whole transfer done in WRAM."""
    _row_ok(row, case)
    if row.get("refused") is not False or row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: transfer refused or touched the hardware page (false positive)")
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


def check_irq(row, case, op, irr):
    """IRQ pending through the transfer: no entry while the transfer state is not 0; entry after TransferEnd."""
    check_completed(row, case, FULL[op], pc=H, s=0x01)
    if row.get("handler_entry_instruction") != TEND + 1 or row.get("irr_before") != irr:
        raise ValueError(f"{case}: handler entry {row.get('handler_entry_instruction')} (IRR {row.get('irr_before')}), "
                         f"want {TEND + 1}: after TransferEnd")


def _cases():
    for method in ("A", "AT"):
        for op in OPS:
            for kind in (*PRESTART_KINDS, *MID_KINDS, "w"):
                yield f"{method}:x{op}{kind}-armed"
                yield f"{method}:x{op}{kind}-allowed"
            for kind in IRQ_KINDS:
                yield f"{method}:x{op}{kind}-allowed"
    for variant in ("armed", "allowed"):
        yield f"AT:xself-{variant}"
        yield f"AT:xlen0-{variant}"
    yield "AT:tamcode-armed"
    for op in OPS:
        for kind in ("d", "s"):
            yield f"A:x{op}{kind}-unguarded"
    for route in M.ROUTES:   # regression: every #498 route under A+T, with its allowed control
        yield f"AT:{route}-armed"
        yield f"AT:{route}-allowed"
    yield SELF_MID_CASE
    yield from MUTANTS
    yield from FALSE_POSITIVE_MUTANTS


def cases():
    return list(dict.fromkeys(_cases()))


def evaluate(rows):
    prestart, mid, irq, controls, detections = {}, {}, {}, [], []
    for op in OPS:
        for kind in PRESTART_KINDS:
            case = f"AT:x{op}{kind}-armed"
            prestart[(f"x{op}{kind}", "A+T")] = (check_refused(rows[case], case, PRESTART_EXPECT), case)
            for method in ("A", "AT"):
                check_completed(rows[f"{method}:x{op}{kind}-allowed"], f"{method}:x{op}{kind}-allowed", FULL[op])
            io = CROSS_IO[op] if kind == "c" else SINGLE_IO[kind]
            for case in (f"A:x{op}{kind}-armed", *([f"A:x{op}{kind}-unguarded"] if kind != "c" else [])):
                check_control(rows[case], case, **io)
                controls.append((case, UNSAFE))
        for method in ("A", "AT"):   # w: the range stops before the forbidden page: no false positive
            for variant in ("armed", "allowed"):
                case = f"{method}:x{op}w-{variant}"
                check_completed(rows[case], case, FULL[op])
        for kind in MID_KINDS:
            case = f"AT:x{op}{kind}-armed"
            mid[(f"x{op}{kind}", "A+T")] = (check_refused(rows[case], case, mid_expect(op, kind)), case)
            for method in ("A", "AT"):
                check_completed(rows[f"{method}:x{op}{kind}-allowed"], f"{method}:x{op}{kind}-allowed", FULL[op])
        case = f"A:x{op}m1-armed"
        mid[(f"x{op}m1", "A")] = (check_refused(rows[case], case, mid_expect(op, "m1")), case)
        check_control(rows[f"A:x{op}m-armed"], f"A:x{op}m-armed", writes=2)   # A alone: the rest lands on I/O
        controls.append((f"A:x{op}m-armed", UNSAFE))
        # e without T: TransferEnd pops only from the stack; the late destination change has no side effect here.
        check_completed(rows[f"A:x{op}e-armed"], f"A:x{op}e-armed", FULL[op])
        for kind, irr in IRQ_KINDS.items():
            for method in ("A", "AT"):
                case = f"{method}:x{op}{kind}-allowed"
                check_irq(rows[case], case, op, irr)
                irq[(op, kind, method)] = rows[case]["handler_entry_instruction"]
    for variant in ("armed", "allowed"):   # own opcode; length 0 (65536 bytes) also covers the own opcode first
        for route in ("xself", "xlen0"):
            case = f"AT:{route}-{variant}"
            prestart[(route, f"A+T {variant}")] = (check_refused(rows[case], case, SELF_EXPECT), case)
    prestart[("tamcode", "A+T")] = (check_refused(rows["AT:tamcode-armed"], "AT:tamcode-armed",
                                                  _e(index=2, pc=0x1E02, reason="transfer_unpeekable", wram=0)),
                                    "AT:tamcode-armed")
    mid[("xself", "A+T, own-opcode pre-start check off")] = (
        check_refused(rows[SELF_MID_CASE], SELF_MID_CASE, SELF_MID_EXPECT), SELF_MID_CASE)
    for (got, _), case in prestart.values():   # every SAFE pre-start row must fail when any criterion is broken
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
    for case in FALSE_POSITIVE_MUTANTS:
        _row_ok(rows[case], case)
        row = rows[case]
        if not (row.get("refused") is True and row.get("refusal_reason") == "transfer_range"
                and row["wram_bytes_changed"] == 0):
            raise ValueError(f"mutation {case}: the wrong address model did not refuse the w fixture")
        detections.append((case, "mutant (false positive of a wrong address model)", "refused"))
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
    for (route, method), ((got, failing), case) in prestart.items():
        print(f"PRESTART {route} {method}: {got}({','.join(failing) or '-'}) reason={rows[case]['refusal_reason']} "
              f"refused before the opcode fetch")
    for (route, method), ((got, failing), case) in mid.items():
        row = rows[case]
        print(f"MIDTRANSFER {route} {method}: {got}({','.join(failing)}) reason={row['refusal_reason']} "
              f"transfer_state={row['stop']['transfer_state']} bytes_already_written={row['wram_bytes_changed']} "
              f"counters(src,dst,len,count)={row['t_stop']}")
    for (op, kind, method), entry in irq.items():
        print(f"IRQ {op} {kind} {method}: handler entered at instruction {entry} (after TransferEnd)")
    print("REGRESSION A+T on the #498 routes: " + ", ".join(f"{r}={v}" for r, (v, _) in regression.items()))
    print(f"EXPECTATIONS_MET issue-648 S6 block transfers: {len(rows)} real-core runs, {len(detections)} detections, "
          f"expectations met (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only patch)")
    print("RESULT issue-648 pre-start (observed fixtures only): T refuses a TII/TIN/TDD/TIA/TAI whose source or "
          "destination range reaches a page not mapped to WRAM $F8 (per opcode, including ranges crossing a page "
          "boundary), whose destination covers its own opcode (including length 0 = 65536), or whose opcode cannot "
          "be peeked, before the opcode fetch: SAFE_IN_SCOPE on all eight criteria; ranges that stop before the "
          "forbidden page are not refused. Without T (method A only) the same transfers reach the hardware page "
          "(UNSAFE controls).")
    print("RESULT issue-648 mid-transfer: a destination page or MPR1 change before a byte step or before the "
          "TransferEnd step, and a rewritten opcode, are refused before that step (no hardware page access), but the "
          "stop is inside the transfer: BLOCKED (not safe to resume). The bytes already written are reported, not "
          "hidden. Resuming after the refusal (mutant) lets the rest of the transfer reach the hardware page "
          "(UNSAFE). An IRQ is not taken during a transfer (IRQ1/IRQ2/TIQ: entry after TransferEnd), so an IRQ "
          "handler cannot change an MPR mid-transfer on the pinned core.")
    print("NOTE push16/pop ordering and latching: covered by make stack-access-guard (#499 mutants) on the same probe "
          "and patch; not repeated here.")
    print("UNKNOWN (not covered): device-originated RAM writes (CD/ADPCM DMA, #367 W17), ST0/ST1/ST2, an IRQ newly "
          "asserted inside a transfer step, the libretro build, real hardware. PR #613's abort and per-byte guard "
          "are re-evaluated from the source only (the guest continues: not SAFE_IN_SCOPE). Not a production "
          "mechanism; no production allowlist; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL issue-648 S6: {error}", file=sys.stderr)
        raise SystemExit(1) from error
