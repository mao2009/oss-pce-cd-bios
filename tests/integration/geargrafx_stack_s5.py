#!/usr/bin/env python3
"""Issue #647 (S5 of #473): decode-time TAM refusal (D) and IRQ deferral (E), compared on the pinned real core.

The #498/#500 measurements left one UNSAFE/BLOCKED hole for method A: IRQ-in-TAM. The TAM's own RunInstruction
maps MPR1 to a forbidden bank and, in the same call, takes a pending IRQ whose entry pushes land on the hardware
page. Two independent test policies are measured here, each on top of method A, on the same fixtures and with the
same observations (probe tests/integration/geargrafx_stack_access_guard.cpp, verification-only patch
patches/geargrafx-b49ae82-stack-access-guard.patch):

  D  decode-time refusal (probe-side, in the pre-instruction hook): the opcode and TAM operand are peeked without
     a bus read (Memory::TryPeek); a TAM whose operand has bit 1 (MPR1) set while A is not $F8 is refused before
     anything is fetched or changed. An opcode that cannot be peeked without a bus side effect (the code page
     mapped to the hardware page) is refused as well (fail closed; route tamcode).
  E  IRQ deferral (core side, gg_verify_defer_irq in the patch): when the instruction about to run is a TAM, the
     IRQ check at the end of that RunInstruction is skipped, so the IRQ stays pending; method A then judges the
     next instruction start.

Neither is a production mechanism; the success of one is not counted for the other. E is only measured together
with A (E alone refuses nothing). The eight criteria and verdicts are those of geargrafx_stack_methods.py
(SAFE_IN_SCOPE / BLOCKED / UNSAFE / UNKNOWN). Covered here:
  - all 256 TAM operand bitmasks with A = $F8 and A = $FF under A+D and under A (comparison), and A = $80/$F7/$00
    for multi-MPR masks under A+D (multiple MPRs changed by one TAM);
  - IRQ1 / IRQ2 / TIQ pending at the TAM, with I = 0 and I = 1, under A, A+D, A+E (and A+D+C, A+E+C);
  - TMA positive (TMA #$02 reads $F8, TAM #$02 allowed) and negative (TMA #$80 reads $00, TAM #$02 refused);
  - D's fail-closed branch: TAM #$01 with A = $FF maps the code page to the hardware page (tamcode);
  - every #498 route under A+D and A+E (regression, no false positive in the allowed controls);
  - probe-side mutants (D off, D checking bit 0, E off) and falsified rows.
NOT covered (UNKNOWN, never PASS): an IRQ newly asserted while the TAM instruction executes (every IRQ here is
pending before the TAM starts), the TAM's m_mpr_buffer (TMA #$00 reads it; not observed), ST0/ST1/ST2, device-originated writes, the libretro
build, real hardware. NMI is not applicable on the pinned core.

Synthetic BIOS/CD only; no reference BIOS. #173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI
VERIFIED are unchanged. The expectations below were written from the pinned source and a local exploratory build of
the same probe and patch (MinGW, supplementary) before the first CI run of this driver.

    python3 tests/integration/geargrafx_stack_s5.py      (make stack-s5; part of make integration)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_stack_methods as M  # noqa: E402
G = M.G

SAFE, BLOCKED, UNSAFE, UNKNOWN = M.SAFE, M.BLOCKED, M.UNSAFE, M.UNKNOWN
H, GUEST = G.HANDLER, G.GUEST
ALLOWED_BANK = 0xF8
TAM_NEXT = 0x1E02      # the instruction after a 2-byte TAM
# IRQ routes: TAM #$02 with the request pending; name -> (source, I flag at the start).
IRQ_ROUTES = {"irqtam": ("IRQ1", 0), "irqtam2": ("IRQ2", 0), "irqtamt": ("TIQ", 0),
              "irqtam1i": ("IRQ1", 1), "irqtam2i": ("IRQ2", 1), "irqtamti": ("TIQ", 1)}
IRR_BIT = {"IRQ1": 0x02, "IRQ2": 0x01, "TIQ": 0x04}
TMA_ROUTES = ("tmapos", "tmaneg")
METHODS = ("A", "AD", "AE")
WITH_C = ("ADC", "AEC")        # C added: must change nothing on the IRQ routes (A or D/E refuses first)


def _e(verdict=SAFE, failing=(), index=1, pc=GUEST, **extra):
    return dict(verdict=verdict, failing=set(failing), index=index, pc=pc, **extra)


def irq_expect(method, route):
    """Armed (stack bank $FF) expectation for an IRQ route."""
    _, i_flag = IRQ_ROUTES[route]
    if method.startswith("AD"):
        return _e(index=1, pc=GUEST, mpr1=ALLOWED_BANK, reason="decode_tam", entered=False)
    if method.startswith("AE") or i_flag:
        # TAM runs (MPR1 = $FF is applied and stays visible); no entry; A refuses the next instruction start.
        return _e(index=2, pc=TAM_NEXT, mpr1=0xFF, reason="mpr1", entered=False)
    # A alone, I = 0: the entry of the TAM's own RunInstruction pushes onto the hardware page (#498).
    return _e(UNSAFE, {"io_prevented", "resume_safe"}, index=2, pc=H, io=M.PUSH_IO, reason="mpr1", entered=True)


# Every #498 route under A+D and A+E: the A results stand except where the TAM is now handled earlier.
EXPECT = {"A": {r: dict(M.EXPECT_A[r]) for r in M.ROUTES}}
EXPECT["AD"] = {**EXPECT["A"], "irqtam": irq_expect("AD", "irqtam"), "tamjsr": _e(index=1, pc=GUEST)}
EXPECT["AE"] = {**EXPECT["A"], "irqtam": irq_expect("AE", "irqtam")}
EXPECT_TMA = {"A": _e(index=3, pc=0x1E04, mpr1=0x00, reason="mpr1"),
              "AD": _e(index=2, pc=TAM_NEXT, mpr1=ALLOWED_BANK, reason="decode_tam"),
              "AE": _e(index=3, pc=0x1E04, mpr1=0x00, reason="mpr1")}
# Allowed controls of the IRQ routes: handler entry instruction (normal-operation impact of each policy).
HANDLER_ENTRY = {"A": 2, "AD": 2, "AE": 3}
SWEEP_METHODS = {"AD": (0xF8, 0xFF), "A": (0xFF,)}
SWEEP_EXTRA = [(mask, a) for mask in (0x02, 0x03, 0x06, 0xFD, 0xFF) for a in (0x80, 0xF7, 0x00)]


def tam_forbidden(mask, a):
    return bool(mask & 0x02) and a != ALLOWED_BANK


def _row_ok(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe failed (exit {row.get('exit')}: {row.get('stderr', '')[-120:]})")
    M._snapshot_fields(row, case)


def check_policy_row(row, case, expect):
    """Verdict, failing criteria and refusal point (M.check_armed), plus the S5 observations."""
    got, failing = M.check_armed(row, case, expect)
    if "reason" in expect and row.get("refusal_reason") != expect["reason"]:
        raise ValueError(f"{case}: refusal reason {row.get('refusal_reason')!r}, want {expect['reason']!r}")
    if "mpr1" in expect and row["stop"]["mpr"][1] != expect["mpr1"]:
        raise ValueError(f"{case}: MPR1 at the stop {row['stop']['mpr'][1]:#x}, want {expect['mpr1']:#x}")
    if "mpr0" in expect and row["stop"]["mpr"][0] != expect["mpr0"]:
        raise ValueError(f"{case}: MPR0 at the stop {row['stop']['mpr'][0]:#x}, want {expect['mpr0']:#x}")
    if expect.get("reason") == "decode_unpeekable" and row.get("decode_refusals") != 1:
        raise ValueError(f"{case}: fail-closed refusal not counted as one decode refusal")
    if expect.get("reason") == "decode_tam" and (row.get("decode_refusals") != 1 or row["stop"]["mpr"] != row["start"]["mpr"]):
        raise ValueError(f"{case}: D refusal without exactly one decode refusal and unchanged MPRs")
    if "entered" in expect and bool(row.get("handler_entry_instruction")) != expect["entered"]:
        raise ValueError(f"{case}: handler entry {row.get('handler_entry_instruction')}, want entered={expect['entered']}")
    return got, failing


def check_irq_allowed(row, case, method, route):
    _row_ok(row, case)
    _, i_flag = IRQ_ROUTES[route]
    want_pc, want_s, want_entry = (TAM_NEXT, 0x04, 0) if i_flag else (H, 0x01, HANDLER_ENTRY[method])
    if row.get("refused") is not False or row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: allowed IRQ control refused or touched the hardware page")
    if (row["stop"]["pc"], row["stop"]["s"], row.get("handler_entry_instruction")) != (want_pc, want_s, want_entry):
        raise ValueError(f"{case}: stop PC/S/entry {row['stop']['pc']:#x}/{row['stop']['s']:#x}/"
                         f"{row.get('handler_entry_instruction')}, want {want_pc:#x}/{want_s:#x}/{want_entry}")
    if method.startswith("AE") and row.get("irq_deferrals", 0) < 1:
        raise ValueError(f"{case}: E did not defer in the allowed control")
    return want_entry


def check_irq_unguarded(row, case, route):
    _row_ok(row, case)
    _, i_flag = IRQ_ROUTES[route]
    want = [] if i_flag else M.PUSH_IO
    if row.get("refused") is not False or row.get("guard_calls") != 0 or G.io_accesses(row) != want:
        raise ValueError(f"{case}: control I/O {G.io_accesses(row)}, want {want}")
    if row.get("irr_before") != IRR_BIT[IRQ_ROUTES[route][0]]:
        raise ValueError(f"{case}: IRR {row.get('irr_before')}, want the {IRQ_ROUTES[route][0]} bit")
    return "entry pushes reach the hardware page" if want else "I = 1: no entry"


def check_no_refusal(row, case, pc, mpr1=ALLOWED_BANK):
    _row_ok(row, case)
    if row.get("refused") is not False or row["io_reads"] or row["io_writes"] or row.get("decode_refusals"):
        raise ValueError(f"{case}: allowed operation refused or touched the hardware page (false positive)")
    if row["stop"]["pc"] != pc or row["stop"]["mpr"][1] != mpr1:
        raise ValueError(f"{case}: stop PC/MPR1 {row['stop']['pc']:#x}/{row['stop']['mpr'][1]:#x}")


def check_sweep(row, case, method, mask, a):
    """One TAM bitmask: refused before any change iff it maps MPR1 to a forbidden bank (D), else applied exactly."""
    _row_ok(row, case)
    start, stop = row["start"]["mpr"], row["stop"]["mpr"]
    applied = [a if mask >> i & 1 else start[i] for i in range(8)]
    if row["io_reads"] or row["io_writes"] or row["instructions_after_refusal"]:
        raise ValueError(f"{case}: hardware page accessed or ran after the refusal")
    if tam_forbidden(mask, a):
        if method == "AD":
            want = dict(refused=True, reason="decode_tam", instructions=1, pc=GUEST, mpr=start)
        else:   # A: TAM runs, the next instruction start is refused
            want = dict(refused=True, reason="mpr1", instructions=2, pc=TAM_NEXT, mpr=applied)
        if M.verdict(row)[0] != SAFE:
            raise ValueError(f"{case}: forbidden TAM not SAFE_IN_SCOPE ({M.verdict(row)[0]})")
    else:
        want = dict(refused=False, reason="none", instructions=2, pc=TAM_NEXT, mpr=applied)
        if row.get("budget_stop") is not True:
            raise ValueError(f"{case}: allowed TAM did not reach the harness budget stop")
    got = dict(refused=row.get("refused"), reason=row.get("refusal_reason"), instructions=row.get("instructions"),
               pc=row["stop"]["pc"], mpr=stop)
    if got != want:
        raise ValueError(f"{case}: {got}, want {want}")
    return "refused" if want["refused"] else "applied"


def _io_landed(row):
    io = G.io_accesses(row)
    return bool(io) and io[0] == (G.WRITE, 0x2104)


# Probe-side mutants of D and E: case -> (expected verdict, check that the mutant really is visible).
MUTANTS = {
    "AD:irqtam-mut-doff": (UNSAFE, lambda r: r.get("decode_refusals") == 0 and _io_landed(r)),
    "AD:irqtam-mut-dbit": (UNSAFE, lambda r: r.get("decode_refusals") == 0 and _io_landed(r)),
    "AE:irqtam-mut-eoff": (UNSAFE, lambda r: r.get("irq_deferrals") == 0 and _io_landed(r)),
}
# The wrong-bit mutant on allowed / forbidden sweep cells: check_sweep must reject both rows, and the row
# must show why: a false positive (D refuses an allowed TAM) / a missed refusal (A refuses after the TAM).
SWEEP_MUTANTS = {
    "AD:tam01ff-mut-dbit": ((0x01, 0xFF), lambda r: r.get("refused") is True
                            and r.get("refusal_reason") == "decode_tam" and r.get("instructions") == 1),
    "AD:tam02ff-mut-dbit": ((0x02, 0xFF), lambda r: r.get("refused") is True
                            and r.get("refusal_reason") == "mpr1" and r["stop"]["mpr"][1] == 0xFF),
}


def sweep_cases():
    for method, values in SWEEP_METHODS.items():
        for a in values:
            for mask in range(256):
                yield f"{method}:tam{mask:02x}{a:02x}-armed", method, mask, a
    for mask, a in SWEEP_EXTRA:
        yield f"AD:tam{mask:02x}{a:02x}-armed", "AD", mask, a


def cases():
    return list(dict.fromkeys(_cases()))   # irqtam is both a #498 route and an IRQ route: run once


def _cases():
    for method in METHODS:
        for route in M.ROUTES:
            yield f"{method}:{route}-armed"
            if method != "A":
                yield f"{method}:{route}-allowed"
        for route in M.BANK_ROUTES:
            for bank in M.BANK_VARIANTS:
                if method != "A":
                    yield f"{method}:{route}-armed{bank}"
        for route in IRQ_ROUTES:
            yield f"{method}:{route}-armed"
            yield f"{method}:{route}-allowed"
        for route in TMA_ROUTES:
            yield f"{method}:{route}-armed"
            yield f"{method}:{route}-allowed"
    yield "AD:tamcode-armed"
    yield "A:tamcode-armed"
    for method in WITH_C:
        for route in IRQ_ROUTES:
            yield f"{method}:{route}-armed"
    for route in IRQ_ROUTES:
        yield f"A:{route}-unguarded"
    for case, *_ in sweep_cases():
        yield case
    yield from MUTANTS
    yield from SWEEP_MUTANTS


def evaluate(rows):
    table, detections, sweep = {}, [], {}
    for method in METHODS:
        for route in M.ROUTES:
            case = f"{method}:{route}-armed"
            got, failing = check_policy_row(rows[case], case, EXPECT[method][route])
            table[(route, method)] = (got, failing)
            if method != "A":
                M.check_allowed(rows[f"{method}:{route}-allowed"], f"{method}:{route}-allowed", route)
        for route in M.BANK_ROUTES:
            for bank in M.BANK_VARIANTS:
                if method != "A":
                    case = f"{method}:{route}-armed{bank}"
                    table[(f"{route}@{bank}", method)] = M.check_armed(rows[case], case, _e())
        for route in IRQ_ROUTES:
            case = f"{method}:{route}-armed"
            got, failing = check_policy_row(rows[case], case, irq_expect(method, route))
            table[(route, method)] = (got, failing)
            check_irq_allowed(rows[f"{method}:{route}-allowed"], f"{method}:{route}-allowed", method, route)
            if got == SAFE:
                for name, broken in M.falsified(rows[case]):
                    v = M.verdict(broken)[0]
                    if v == SAFE:
                        raise ValueError(f"{case}: falsified {name} still judged SAFE")
                    detections.append((case, f"falsified:{name}", v))
        check_no_refusal(rows[f"{method}:tmapos-armed"], f"{method}:tmapos-armed", 0x1E04)
        check_no_refusal(rows[f"{method}:tmapos-allowed"], f"{method}:tmapos-allowed", 0x1E04)
        for variant in ("armed", "allowed"):   # MPR1 -> $00 is forbidden whatever the stack bank
            case = f"{method}:tmaneg-{variant}"
            table[(f"tmaneg/{variant}", method)] = check_policy_row(rows[case], case, EXPECT_TMA[method])
    case = "AD:tamcode-armed"   # D fail closed: the opcode at $1E02 is on the hardware page, never fetched
    table[("tamcode", "AD")] = check_policy_row(rows[case], case, _e(index=2, pc=TAM_NEXT, mpr0=0xFF,
                                                                     reason="decode_unpeekable"))
    _row_ok(rows["A:tamcode-armed"], "A:tamcode-armed")
    if rows["A:tamcode-armed"].get("refused") is not False or not rows["A:tamcode-armed"]["io_reads"]:
        raise ValueError("A:tamcode-armed: expected method A (MPR1 only) to let the code run from the hardware page")
    detections.append(("A:tamcode-armed", "control: MPR0 outside the S1 contract",
                       f"not refused, {rows['A:tamcode-armed']['io_reads']} hardware page reads"))
    for method in WITH_C:
        for route in IRQ_ROUTES:
            case = f"{method}:{route}-armed"
            table[(route, method)] = check_policy_row(rows[case], case, irq_expect(method, route))
            if rows[case].get("guard_calls") != 0:   # A or D refuses before any stack access
                raise ValueError(f"{case}: the C guard was consulted; the row would say something about C")
    for route in IRQ_ROUTES:
        case = f"A:{route}-unguarded"
        result = check_irq_unguarded(rows[case], case, route)
        if result.startswith("entry"):
            if M.verdict(rows[case])[0] != UNSAFE:
                raise ValueError(f"{case}: guard-less control not judged UNSAFE")
            detections.append((case, "guard disabled", UNSAFE))
    for case, method, mask, a in sweep_cases():
        result = check_sweep(rows[case], case, method, mask, a)
        sweep[(method, a, result)] = sweep.get((method, a, result), 0) + 1
    for case, (want, visible) in MUTANTS.items():
        _row_ok(rows[case], case)
        got = M.verdict(rows[case])[0]
        if got != want or not visible(rows[case]):
            raise ValueError(f"mutation {case}: judged {got} (visible={visible(rows[case])}), want {want}")
        detections.append((case, "mutant", got))
    for case, ((mask, a), why) in SWEEP_MUTANTS.items():
        _row_ok(rows[case], case)   # a failed probe run is not a detection
        if not why(rows[case]):
            raise ValueError(f"mutation {case}: the row does not show the wrong-bit decode")
        try:
            check_sweep(rows[case], case, "AD", mask, a)
        except ValueError as error:
            detections.append((case, "mutant (sweep check)", f"rejected: {error}"[:160]))
        else:
            raise ValueError(f"mutation {case}: the sweep check accepted the wrong-bit decode")
    return table, detections, sweep


def comparison(rows):
    """D vs E on the same IRQ-in-TAM fixtures (I = 0), from the rows; item 8 is a static description."""
    out = {}
    for method in ("AD", "AE"):
        group = [rows[f"{method}:{r}-armed"] for r, (_, i) in IRQ_ROUTES.items() if i == 0]
        out[method] = {
            "1_side_effect_blocked_before": all(r["io_reads"] + r["io_writes"] == 0 for r in group),
            "2a_a_x_y_s_p_at_stop_equal_run_start": all(
                all(r["stop"][k] == r["start"][k] for k in ("a", "x", "y", "s", "p")) for r in group),
            "2b_pc_at_stop_equals_run_start": all(r["stop"]["pc"] == r["start"]["pc"] for r in group),
            "3_mpr_unchanged_from_run_start": all(r["stop"]["mpr"] == r["start"]["mpr"] for r in group),
            "4_irq_still_pending_not_entered": all(r["irr_before"] == r["irr_after"] != 0
                                                   and not r.get("handler_entry_instruction") for r in group),
            "5_transfer_state_zero": all(r["stop"]["transfer_state"] == 0 for r in group),
            "6_no_instruction_after_refusal": all(r["instructions_after_refusal"] == 0 for r in group),
            "7_handler_entry_instruction_in_allowed_run": sorted({rows[f"{method}:{r}-allowed"][
                "handler_entry_instruction"] for r, (_, i) in IRQ_ROUTES.items() if i == 0}),
            "refused_instruction": sorted({r["refusal_instruction_start"]["pc"] for r in group}),
        }
    out["AD"]["8_implementation"] = ("probe-side only: peek opcode + operand without a bus read in the existing "
                                     "pre-instruction hook; needs the operand decode of TAM (one opcode); what is "
                                     "peekable is specific to this core (Memory::TryPeek: not the hardware page, "
                                     "not mapper banks below $80 except SF2 / Arcade Card)")
    out["AE"]["8_implementation"] = ("core change: a flag consulted in RunInstruction's IRQ check; the TAM's MPR "
                                     "change is applied; the IRQ is deferred by one instruction per TAM and on "
                                     "every instruction whose opcode cannot be peeked (could starve IRQs while "
                                     "code runs from a non-peekable bank; not measured)")
    return out


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-s5-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
    table, detections, sweep = evaluate(rows)
    sweep_names = {c for c, *_ in sweep_cases()}
    for case, row in rows.items():
        if case in sweep_names:   # compact: the full rows of 800 sweep runs would only repeat the summary
            row = {k: row.get(k) for k in ("mode", "refused", "refusal_reason", "instructions", "io_reads", "io_writes",
                                           "budget_stop", "decode_refusals")} | {"stop_mpr": row["stop"]["mpr"]}
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    routes = sorted({r for r, _ in table}, key=lambda r: (r not in M.ROUTES, r))
    for route in routes:
        cells = "  ".join(f"{m}={table[(route, m)][0]}({','.join(table[(route, m)][1]) or '-'})"
                          for m in (*METHODS, *WITH_C) if (route, m) in table)
        print(f"COMPARE {route}: {cells}")
    for (method, a, result), n in sorted(sweep.items()):
        print(f"SWEEP {method} A=${a:02X}: {n} masks {result}")
    print(f"SWEEP_EXTRA A+D A in $80/$F7/$00, masks {[f'${m:02X}' for m, _ in SWEEP_EXTRA[::3]]}: checked")
    for method, items in comparison(rows).items():
        print(f"D_VS_E {method}: " + json.dumps(items, sort_keys=True))
    count = {m: {v: sum(1 for (_, mm), (vv, _) in table.items() if mm == m and vv == v) for v in (SAFE, BLOCKED, UNSAFE)}
             for m in (*METHODS, *WITH_C)}
    print(f"EXPECTATIONS_MET issue-647 S5 decode-time refusal / IRQ deferral: {len(rows)} real-core runs, "
          f"{len(detections)} detections, expectations met; totals {json.dumps(count, sort_keys=True)} "
          f"(pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only patch)")
    print("RESULT issue-647 (observed fixtures only): A+D and A+E each close the observed IRQ-in-TAM hole "
          "(IRQ1/IRQ2/TIQ with I = 0: UNSAFE under A, SAFE_IN_SCOPE under A+D and A+E, no hardware page access; "
          "with I = 1 all three are SAFE_IN_SCOPE, a regression check). D refuses the TAM itself, so MPR1 "
          "stays $F8 and the IRQ stays pending; E lets the TAM run (MPR1 = $FF is applied and visible), keeps the IRQ "
          "pending and A refuses the next instruction. The mid-transfer rows stay BLOCKED under both (#648). "
          "The bitmask sweep judges the TAM only (the harness stops before instruction 2), for the measured A "
          "values ($F8/$FF on all masks; $80/$F7/$00 on five).")
    print("UNKNOWN (not covered): an IRQ newly asserted while the TAM executes (all IRQs here are pending before the "
          "TAM), the TAM's m_mpr_buffer (read back by TMA #$00; not observed), IRQ starvation by E on non-peekable "
          "code, ST0/ST1/ST2, device-originated writes, the libretro build, real "
          "hardware; NMI not applicable. Not a production mechanism; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL issue-647 S5: {error}", file=sys.stderr)
        raise SystemExit(1) from error
