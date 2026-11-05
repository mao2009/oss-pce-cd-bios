#!/usr/bin/env python3
"""Issue #653 (S7 of #473): IRQs newly asserted inside an instruction, on the pinned real core.

S5 (#647) and S6 (#648) measured IRQs that were pending BEFORE the instruction started. This driver asserts an IRQ at
a chosen instruction k and records when it becomes visible and when it is taken, with the same probe and
verification-only patch (tests/integration/geargrafx_stack_access_guard.cpp, patches/geargrafx-b49ae82-
stack-access-guard.patch). The patch adds gg_verify_clock_observer, called at every hardware clock point INSIDE an
instruction (HuC6280::ClockHardwareCycles: VDC/VCE access, PSG write, VDC stall) after the clock and before its
CheckIRQs; the cycles left after RunInstruction are clocked by the core loop without CheckIRQs. Sources of the
assert (fixture, route ni_<base>_<src>_<k>[_i]):
  t        TIQ from the timer: armed at the start of instruction k (counter 0, one master cycle to the tick), so it
           expires at the first hardware clock after that point: at a clock point inside k if k has one, otherwise
           in the core loop's clocking after k returns.
  q1 / q2  IRQ1 / IRQ2 request bit set by the harness at the start of k (before its fetch).
  q1m/q2m  IRQ1 / IRQ2 set by the harness at the first clock point inside k; if k has none, nothing is injected and
           the case is NOT_MEASURED (it is not counted as an assert).
Device-originated IRQ1 (VDC) / IRQ2 (CD) timing is modelled only by these harness request bits (a device model
could also rewrite a request bit; the timeline would show it).

Expectation model (from the pinned source, written before the first CI run; checked against a local exploratory
build, MinGW, supplementary): an assert is VISIBLE to the CheckIRQs after the next opcode fetch, or at once if it
happens at a clock point inside an instruction (that clock point runs CheckIRQs). An instruction takes the IRQ at
its end if the IRQ was visible during it, the I flag allowed it, and the transfer state is 0 at its end. So:
  - TAM has no hardware clock point: a TIQ raised in TAM's cycles is visible only at the next instruction and is
    taken at ITS end (an IRQ pending before the TAM is taken inside the TAM: the S5 hole).
  - STA to the hardware page has a clock point: an assert there is taken at the end of the same instruction.
  - block transfers: not taken while the transfer state is not 0; taken at the end of TransferEnd if visible by its
    fetch, else after the next instruction.
Judged on the eight criteria of geargrafx_stack_methods.py for the armed (refused) rows; for criterion 6 the run-start
IRR is taken together with the one request bit this fixture asserts, and only if the timeline shows that bit before
the refusal (any other IRR change still fails the criterion). Each armed row also checks whether the assert happened
before the refusal; rows where it could not (the refused instruction is the one the timer was armed at, or the
injection point is never reached) are labelled as such, not as measured asserts. Every timer case is run three
times and must give identical rows (repeatability of the fixed-timer fixture). The per-instruction timeline records
PC, IRR, the pending mask, transfer state, A/X/Y/P/S, MPR0-7 and the transfer counters.

Not covered (UNKNOWN / NOT_MEASURED): an assert inside TAM (no clock point on the pinned core), device-originated
IRQ timing, ST0/ST1/ST2, DMA, the libretro build, real hardware (#655). Synthetic BIOS/CD only. Not a production
mechanism. #173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are unchanged.

    python3 tests/integration/geargrafx_stack_irq_timing.py      (make stack-irq-timing; part of make integration)
"""
import copy
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
BITS = {"t": 0x04, "q1": 0x02, "q2": 0x01, "q1m": 0x02, "q2m": 0x01}
TRANSFER_END = 6        # 1 TransferStart, 2-5 byte steps, 6 TransferEnd
LOOP_PC = {"tam": 0x1E02, "sta": 0x1E03}   # BRA * after the instruction under test; transfers: $1E07


def parse(route):
    """ni_<base>_<src>_<k>[_i] -> (base, src, k, i_flag)."""
    f = route.split("_")[1:]
    return f[0], f[1], int(f[2]), len(f) == 4


def is_transfer(base):
    return base[:3] in OPS


def has_clock_point(base, n):
    """Hardware clock points inside instruction n (pinned source): STA to the VDC; the byte steps of a VDC
    transfer. TAM, WRAM transfers, TransferStart / TransferEnd (stack and WRAM only) have none."""
    if base == "sta":
        return n == 1
    if is_transfer(base) and base.endswith("v"):
        return 2 <= n <= 5
    return False


def ts_end_zero(base, n):
    return not is_transfer(base) or n >= TRANSFER_END


def expected(route):
    """(visible, where, entry): instruction at which the IRQ becomes visible, 'boundary' / 'mid' / None
    (NOT_MEASURED), and the first handler instruction (0 = never taken)."""
    base, src, k, i_flag = parse(route)
    if src in ("q1m", "q2m"):
        if not has_clock_point(base, k):
            return None, None, 0
        visible, where = k, "mid"
    elif src == "t":
        visible, where = (k, "mid") if has_clock_point(base, k) else (k + 1, "boundary")
    else:
        visible, where = k, "boundary"
    if i_flag:
        return visible, where, 0
    n = visible
    while not ts_end_zero(base, n):
        n += 1
    return visible, where, n + 1


def observed_assert(row, bit):
    """Where the request bit first appeared: ('mid', i) at a clock point inside i, ('boundary', i) at the start of
    instruction i, or (None, None)."""
    ni = row["ni"]
    mids = [e["i"] for e in ni["clocks"] if e["irr"] & bit]
    starts = [e["i"] for e in ni["timeline"] if e["irr"] & bit]
    first_mid = mids[0] if mids else None
    first_start = starts[0] if starts else None
    if first_mid is not None and (first_start is None or first_mid < first_start):
        return "mid", first_mid
    if first_start is not None:
        return "boundary", first_start
    return None, None


def _row_ok(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe failed (exit {row.get('exit')}: {row.get('stderr', '')[-120:]})")
    M._snapshot_fields(row, case)
    ni = row.get("ni")
    if type(ni) is not dict or ni.get("active") is not True or type(ni.get("timeline")) is not list:
        raise ValueError(f"{case}: missing #653 observation")
    if any(type(e.get("i")) is not int or type(e.get("irr")) is not int for e in ni["timeline"] + ni["clocks"]):
        raise ValueError(f"{case}: malformed timeline")
    for e in ni["timeline"]:
        if not all(type(e.get(k)) is int for k in ("pc", "pend", "ts", "a", "x", "y", "p", "s")) or \
                len(e.get("mpr", [])) != 8 or len(e.get("xfer", [])) != 3:
            raise ValueError(f"{case}: timeline entry without the CPU / MPR / transfer state")


def check_completed(row, case):
    """Allowed (not refused) run: the IRQ is taken exactly where the model says, the assert is where it says."""
    _row_ok(row, case)
    route = case.split(":")[1].split("-", 1)[0]
    base, src, k, i_flag = parse(route)
    visible, where, entry = expected(route)
    if row.get("refused") is not False:
        raise ValueError(f"{case}: refused ({row.get('refusal_reason')})")
    if row.get("handler_entry_instruction") != entry:
        raise ValueError(f"{case}: handler entered at instruction {row.get('handler_entry_instruction')}, want {entry}")
    injected = row["ni"]["injected"]
    if src != "t" and injected != (where is not None):
        raise ValueError(f"{case}: injected={injected}, want {where is not None}")
    got = observed_assert(row, BITS[src])
    if where is None:
        if got != (None, None):
            raise ValueError(f"{case}: an IRQ appeared {got} although nothing was injected")
    elif got != (where, visible):
        raise ValueError(f"{case}: assert observed {got}, want {(where, visible)}")
    want_writes = 1 if base == "sta" else 4 if is_transfer(base) and base.endswith("v") else 0
    if row["io_reads"] or row["io_writes"] != want_writes:
        raise ValueError(f"{case}: hardware page {row['io_reads']}r/{row['io_writes']}w, want 0r/{want_writes}w "
                         f"(the fixture's own VDC writes only; the IRQ pushes go to the WRAM stack)")
    stop_pc = H if entry else LOOP_PC.get(base, 0x1E07)
    if row["stop"]["pc"] != stop_pc:
        raise ValueError(f"{case}: stop PC {row['stop']['pc']:#x}, want {stop_pc:#x}")
    if base == "tam" and any(e["i"] == 1 for e in row["ni"]["clocks"]):
        raise ValueError(f"{case}: a hardware clock point inside TAM was observed")
    return entry, got


def assert_before_refusal(method, route):
    """(asserted, instruction at whose start the bit is first visible) for an armed TAM row, from the model: A
    refuses instruction 2 (MPR1 = $FF after the TAM), A+D refuses instruction 1. A harness bit set at the start of
    k is in place before that instruction's refusal; the timer armed at the start of k expires only if k runs."""
    _, src, k, _ = parse(route)
    refused_at = 1 if method == "AD" else 2
    if src == "t":
        return (k < refused_at), k + 1
    return (k <= refused_at), k


def _at_refusal(row, bit, asserted):
    """Criterion 6 reference: the run-start IRR plus the one request bit the fixture asserted before the refusal."""
    fixed = copy.deepcopy(row)
    if asserted:
        fixed["irr_before"] = row["irr_before"] | bit
    return fixed


def repeat_mismatches(first, again):
    """Cases whose repeated run differs from the first one (the rows must be identical)."""
    return sorted(case for case, row in again.items() if json.dumps(row, sort_keys=True) !=
                  json.dumps(first[case], sort_keys=True))


def check_tam_armed(row, case):
    """TAM #$02 with A = $FF (forbidden). A: refused at the next instruction start unless the IRQ was visible inside
    the TAM (then its entry pushes reach the hardware page first: UNSAFE). A+D: refused before the TAM."""
    _row_ok(row, case)
    method, rest = case.split(":")
    route = rest.split("-", 1)[0]
    _, src, _, i_flag = parse(route)
    visible, _, _ = expected(route)
    asserted, at = assert_before_refusal(method, route)
    got_assert = observed_assert(row, BITS[src])
    if got_assert != (("boundary", at) if asserted else (None, None)):
        raise ValueError(f"{case}: assert observed {got_assert}, want {('boundary', at) if asserted else 'none'}")
    if method == "AD":
        want = dict(verdict=SAFE, failing=set(), index=1, pc=GUEST)
    elif visible <= 1 and not i_flag:
        want = dict(verdict=UNSAFE, failing={"io_prevented", "resume_safe"}, index=2, pc=H, io=M.PUSH_IO)
    else:
        want = dict(verdict=SAFE, failing=set(), index=2, pc=0x1E02)
    got, failing = M.check_armed(_at_refusal(row, BITS[src], asserted), case, want)
    return got, failing, asserted


def check_tam_unguarded(row, case):
    """No guard: the IRQ is taken where the model says and its pushes reach the hardware page (MPR1 = $FF)."""
    _row_ok(row, case)
    route = case.split(":")[1].split("-", 1)[0]
    _, _, entry = expected(route)
    if row.get("refused") is not False or row.get("handler_entry_instruction") != entry:
        raise ValueError(f"{case}: entry {row.get('handler_entry_instruction')}, want {entry}")
    want = M.PUSH_IO if entry else []
    if G.io_accesses(row) != want:
        raise ValueError(f"{case}: hardware page accesses {G.io_accesses(row)}, want {want}")
    return UNSAFE if entry else "no entry"


TAM_ALLOWED = ("t_1", "t_2", "q1_1", "q2_1", "q1_2", "q2_2", "q1m_1", "t_1_i", "q1_1_i", "q2_1_i")
TAM_ARMED = ("t_1", "t_2", "q1_1", "q2_1", "q1_2", "t_1_i")
STA = ("t_1", "q1_1", "q2_1", "q1m_1", "q2m_1", "t_1_i", "q1m_1_i", "q2m_1_i")
WRAM_XFER = ("t_1", "t_3", "t_5", "t_6", "q1_3", "q2_3", "q1_6", "q1_7", "q1m_3", "t_3_i", "q2_3_i")
VDC_XFER = ("t_2", "t_5", "t_6", "q1m_3", "q2m_4", "t_3_i")
# Mutants: case -> handler entry instruction the mutant must produce (the correct model gives a different value).
MUTANTS = {
    "A:ni_sta_t_1-mut-nomidcheck": 3,     # no CheckIRQs at the clock point: taken one instruction later (correct: 2)
    "A:ni_sta_q1m_1-mut-nomidcheck": 3,
    "A:ni_sta_q1m_1-mut-noinject": 0,     # the IRQ is never injected (correct: 2)
    "A:ni_tiiv_q1m_3-mut-noinject": 0,    # (correct: 7)
    **{f"A:ni_{op}_q1_3-mut-xferirq": 4 for op in OPS},   # IRQ taken inside the transfer (correct: 7)
}
# The same core mutant under A+T: the next step runs the handler's opcode, so T refuses it inside the transfer.
T_CATCHES = tuple(f"AT:ni_{op}_q1_3-mut-xferirq" for op in OPS)
NOT_MEASURED = ("A:ni_tam_q1m_1-allowed", "AD:ni_tam_q1m_1-allowed",
                *(f"{m}:ni_{op}_q1m_3-allowed" for op in OPS for m in ("A", "AT")))


def _cases():
    for method in ("A", "AD"):
        for s in TAM_ALLOWED:
            yield f"{method}:ni_tam_{s}-allowed"
        for s in TAM_ARMED:
            yield f"{method}:ni_tam_{s}-armed"
    for s in TAM_ARMED:
        yield f"A:ni_tam_{s}-unguarded"
    for s in STA:
        yield f"A:ni_sta_{s}-allowed"
    for op in OPS:
        for method in ("A", "AT"):
            for s in WRAM_XFER:
                yield f"{method}:ni_{op}_{s}-allowed"
        for s in VDC_XFER:
            yield f"A:ni_{op}v_{s}-allowed"
    yield from MUTANTS
    yield from T_CATCHES


def cases():
    return list(dict.fromkeys(_cases()))


def evaluate(rows):
    timing, armed, controls, detections = {}, {}, [], []
    for case in cases():
        if "-mut-" in case:
            continue
        method, rest = case.split(":")
        route, variant = rest.split("-", 1)
        if variant == "allowed":
            timing[case] = check_completed(rows[case], case)
        elif variant == "armed":
            armed[case] = check_tam_armed(rows[case], case)
            if armed[case][0] == SAFE:
                route = rest.split("-", 1)[0]
                fixed = _at_refusal(rows[case], BITS[parse(route)[1]], armed[case][2])
                for name, broken in M.falsified(fixed):
                    if M.verdict(broken)[0] == SAFE:
                        raise ValueError(f"{case}: falsified {name} still judged SAFE")
                    detections.append((case, f"falsified:{name}", M.verdict(broken)[0]))
        else:
            controls.append((case, check_tam_unguarded(rows[case], case)))
    for case in NOT_MEASURED:
        if rows[case]["ni"]["injected"] or timing[case][0] != 0:
            raise ValueError(f"{case}: expected NOT_MEASURED (no clock point inside the instruction)")
    for case, entry in MUTANTS.items():
        _row_ok(rows[case], case)
        route = case.split(":")[1].split("-", 1)[0]
        correct = expected(route)[2]
        got = rows[case].get("handler_entry_instruction")
        if got != entry or got == correct:
            raise ValueError(f"mutation {case}: handler entry {got}, want the mutant's {entry} (correct model {correct})")
        detections.append((case, "mutant", f"entry {got} != {correct}"))
    for case in T_CATCHES:
        _row_ok(rows[case], case)
        row = rows[case]
        if not (row.get("refused") is True and row.get("refusal_reason") == "transfer_opcode_changed"
                and row["io_reads"] + row["io_writes"] == 0 and row["instructions_after_refusal"] == 0
                and row["refusal_instruction_start"]["transfer_state"] == 2):
            raise ValueError(f"{case}: T did not refuse the step after a mid-transfer IRQ entry")
        detections.append((case, "core mutant caught by T", "refused inside the transfer (BLOCKED)"))
    return timing, armed, controls, detections


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-irq-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
        timer_cases = [c for c in cases() if parse(c.split(":")[1].split("-", 1)[0])[1] == "t"]
        repeats = [{c: G.run_case(probe, bios, cue, c) for c in timer_cases} for _ in range(2)]
    timing, armed, controls, detections = evaluate(rows)
    for again in repeats:
        mismatch = repeat_mismatches(rows, again)
        if mismatch:
            raise ValueError(f"fixed-timer fixture not repeatable: {mismatch[:5]}")
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    for case, (entry, where) in timing.items():
        place = (f"raised at a clock point inside instruction {where[1]}" if where[0] == "mid"
                 else f"visible from the start of instruction {where[1]}") if where[0] else ""
        status = "NOT_MEASURED (no hardware clock point inside the instruction)" if case in NOT_MEASURED else (
            f"{place}; handler entered at instruction {entry or 'never (I = 1)'}")
        print(f"TIMING {case}: {status}")
    for case, (got, failing, asserted) in armed.items():
        note = "IRQ asserted before the refusal" if asserted else (
            "no assert before the refusal (the refused instruction is where it would have been asserted): control")
        print(f"ARMED {case}: {got}({','.join(failing) or '-'}) {note}")
    for case, result in controls:
        print(f"CONTROL {case}: {result}")
    print(f"REPEATABLE issue-653: {len(timer_cases)} fixed-timer cases run 3 times each, identical rows")
    print(f"EXPECTATIONS_MET issue-653 IRQ assert/accept timing: {len(rows) + 2 * len(timer_cases)} real-core runs, "
          f"{len(detections)} "
          f"detections, expectations met (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only "
          "patch)")
    print("RESULT issue-653 (observed fixtures only): TAM has no hardware clock point on the pinned core, so a TIQ "
          "raised in its cycles becomes visible only at the next instruction and is taken after it; with A=$FF method A "
          "refuses that next instruction (SAFE_IN_SCOPE) and A+D refuses the TAM itself. An IRQ asserted before the "
          "TAM is taken inside it (S5 hole: UNSAFE under A, SAFE under A+D). An assert at a clock point inside an "
          "instruction (STA to the VDC) is taken at the end of that same instruction. During TII/TIN/TDD/TIA/TAI an "
          "IRQ asserted in the measured steps (TransferStart, byte steps 3 and 5 for WRAM transfers; byte steps "
          "2-5 at VDC clock points) is taken only after TransferEnd, on the pinned core; a core that took it "
          "inside the transfer (mutant) is refused by T at the next step.")
    print("NOT_MEASURED / UNKNOWN: an assert inside TAM itself (no clock point), device-originated IRQ1/IRQ2 timing "
          "(modelled by harness request bits), ST0/ST1/ST2, DMA, the libretro build, real hardware (#655). Not a "
          "production mechanism; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL issue-653 IRQ timing: {error}", file=sys.stderr)
        raise SystemExit(1) from error
