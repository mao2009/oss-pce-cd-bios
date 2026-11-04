#!/usr/bin/env python3
"""Issue #500 (S4 of #473): method A, method C and A+C compared under the same conditions on the real core.

Uses the verification-only patch and probe of #499 (method C: every stack push/pop refused before the
access) and #498 (method A: an instruction refused before it executes when MPR1 is not the allowed WRAM bank
at its start). The same routes, stack banks and harness MPR settings are run under each method and judged
on the eight criteria of geargrafx_stack_methods.py (I/O, RAM, A/X/Y, PC, S/P/MPR, IRQ, transfer state,
resume). The guest is never resumed after a refusal.

What this can show: which combination prevents the forbidden side effect AND stops at a consistent point,
inside the observed scope. What it cannot show: production safety. ST0/ST1/ST2 (WriteFromCPU),
device-originated writes, the libretro build and real hardware are not covered and stay UNKNOWN; the
refusal policy (MPR1 = $F8 only, S1 #497) is a test policy inside the probe. Not a production mechanism.
#173/#367/#459, BLOCKED_ABI, release_eligible, DEV_CALLABLE and ABI VERIFIED are unchanged.

The expectations for C and A+C below were written from the pinned source and the #499 / #498 measurements
before the first real-core run of this driver.

    python3 tests/integration/geargrafx_stack_methods_compare.py   (make stack-methods-compare; in make integration)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_stack_methods as M  # noqa: E402
G = M.G

METHODS = ("A", "C", "AC")
SAFE, BLOCKED, UNSAFE = M.SAFE, M.BLOCKED, M.UNSAFE
RESUME = "resume_safe"
PC, SPM, IRQ, REGS, XFER = "pc_preserved", "s_p_mpr_preserved", "irq_preserved", "registers_preserved", "transfer_preserved"


def _c(failing, index=1, pc=G.GUEST, **extra):
    return dict(verdict=BLOCKED, failing=set(failing) | {RESUME}, index=index, pc=pc, kind="stack", **extra)


# Method C: the refused instruction still finishes (pinned source; #499 measurements), so no route stops
# consistently. The failing criteria follow from what the instruction does after the refused access.
EXPECT_C = {
    **{r: _c({PC}) for r in ("pha", "php", "phx", "phy", "phasame", "pharestore", "jsr", "bsr", "brk", "rts")},
    "flippha": _c({PC}, index=2, pc=0x1E01),
    "tamjsr": _c({PC}, index=2, pc=0x1E02),
    "rti": _c({PC, SPM, IRQ}),                     # SetP(0): I cleared; PC = 0
    "pla": _c({PC, REGS, SPM}), "plx": _c({PC, REGS, SPM}), "ply": _c({PC, REGS, SPM}),   # value 0, Z set
    "plp": _c({PC, SPM, IRQ}),                     # SetP(0)
    "irq": _c({PC, SPM, IRQ}, index=2, pc=0x1E01),  # entry sets I, loads the vector
    "irqtam": _c({PC, SPM, IRQ}),                  # TAM's own MPR1 change + entry
    **{op: _c({XFER}) for op in M.BLOCK_OPS},      # transfer state 0 -> 2 before the first push
    # TransferEnd step with the harness MPR1 change (every block opcode): pops refused, values 0, state 1 -> 0.
    **{r: _c({REGS, PC, XFER}, index=3, transfer_state=1, legit_stack_bytes=3, dst=0x5A) for r in M.MID_TRANSFER},
}
# A+C: A refuses first wherever MPR1 is wrong at the instruction start, so A's results stand; on IRQ-in-TAM
# A lets the TAM run and C refuses the entry pushes inside the same RunInstruction.
EXPECT_AC = {r: dict(M.EXPECT_A[r]) for r in M.ROUTES}
EXPECT_AC["irqtam"] = _c({PC, SPM, IRQ})
EXPECT = {"A": M.EXPECT_A, "C": EXPECT_C, "AC": EXPECT_AC}
BANK_EXPECT = {"A": dict(verdict=SAFE, failing=set(), index=1, pc=G.GUEST),
               "C": _c({PC}),
               "AC": dict(verdict=SAFE, failing=set(), index=1, pc=G.GUEST)}
# Mutants on A+C: with method A's refusal ignored (mutant 6), C still refuses the push (BLOCKED, not
# UNSAFE); with C's push16 check moved (mutant 1) on IRQ-in-TAM, A cannot help and the first byte lands.
MUTANTS = {
    "AC:pha-mut-aignore": BLOCKED,
    "AC:jsr-mut-aignore": BLOCKED,
    "AC:irqtam-mut-late16": UNSAFE,
    "C:pha-mut-ignore": UNSAFE,
}


def cases():
    for method in METHODS:
        for route in M.ROUTES:
            yield f"{method}:{route}-armed"
        for route in M.BANK_ROUTES:
            for bank in M.BANK_VARIANTS:
                yield f"{method}:{route}-armed{bank}"
    for route in M.ROUTES:   # method-independent positive and null-guard controls (run once)
        yield f"AC:{route}-allowed"
        yield f"AC:{route}-unguarded"
    yield from MUTANTS


def evaluate(rows):
    table, detections = {}, []
    for route in M.ROUTES:
        M.check_allowed(rows[f"AC:{route}-allowed"], f"AC:{route}-allowed", route)
        M.check_unguarded(rows[f"AC:{route}-unguarded"], f"AC:{route}-unguarded", route)
        if M.verdict(rows[f"AC:{route}-unguarded"])[0] != UNSAFE:
            raise ValueError(f"AC:{route}-unguarded: guard-less control not judged UNSAFE")
        detections.append((f"AC:{route}-unguarded", "guard disabled", UNSAFE))
    for method in METHODS:
        for route in M.ROUTES:
            case = f"{method}:{route}-armed"
            got, failing = M.check_armed(rows[case], case, EXPECT[method][route])
            table[(route, method)] = (got, failing)
            if got == SAFE:
                for name, broken in M.falsified(rows[case]):
                    if M.verdict(broken)[0] == SAFE:
                        raise ValueError(f"{case}: falsified {name} still judged SAFE")
                    detections.append((case, f"falsified:{name}", "not SAFE"))
        for route in M.BANK_ROUTES:
            for bank in M.BANK_VARIANTS:
                case = f"{method}:{route}-armed{bank}"
                table[(f"{route}@{bank}", method)] = M.check_armed(rows[case], case, BANK_EXPECT[method])
    for case, want in MUTANTS.items():
        M._snapshot_fields(rows[case], case)
        got, _ = M.verdict(rows[case], 0)
        if got != want:
            raise ValueError(f"mutation {case}: judged {got}, want {want}")
        detections.append((case, "mutant", got))
    return table, detections


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-compare-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
    table, detections = evaluate(rows)
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    routes = sorted({route for route, _ in table}, key=lambda r: (r not in M.ROUTES, r))
    for route in routes:
        cells = "  ".join(f"{m}={table[(route, m)][0]}({','.join(table[(route, m)][1]) or '-'})" for m in METHODS)
        print(f"COMPARE {route}: {cells}")
    count = {m: {v: sum(1 for (r, mm), (vv, _) in table.items() if mm == m and vv == v) for v in (SAFE, BLOCKED, UNSAFE)}
             for m in METHODS}
    for route in routes:
        if M.NOTES.get(route):
            print(f"NOTE {route}: {M.NOTES[route]}")
    print(f"EXPECTATIONS_MET issue-500 A/C/A+C comparison: {len(rows)} real-core runs, {len(detections)} detections, expectations "
          f"met; totals {json.dumps(count, sort_keys=True)} (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD)")
    print("RESULT issue-500: A+C prevents every observed forbidden side effect (no UNSAFE). It stops consistently "
          "(SAFE_IN_SCOPE) wherever MPR1 is wrong at an instruction start. Two cases stay BLOCKED: IRQ-in-TAM (C "
          "refuses the entry pushes inside the TAM's RunInstruction, but the TAM's MPR1 change, the I flag and the "
          "vector PC are already applied) and a mid-transfer MPR1 change on every block opcode (the stop is inside "
          "the block transfer).")
    print("UNKNOWN (not covered): ST0/ST1/ST2 WriteFromCPU, device-originated writes, the libretro build, real "
          "hardware; NMI not applicable on the pinned core. Not a production mechanism; no gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-500 stack methods comparison: {error}", file=sys.stderr)
        raise SystemExit(1) from error
