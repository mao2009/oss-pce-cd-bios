#!/usr/bin/env python3
"""Stack safety methods on the pinned real core, judged on eight separate criteria (Issue #498, S2 of #473).

Method A (#473 S0): refuse an instruction BEFORE it executes when the stack page (MPR1) is not the
allowed WRAM bank at the instruction start. The refusal point is the verification-only pre-instruction
hook of patches/geargrafx-b49ae82-stack-access-guard.patch (gg_verify_pre_instruction returning false:
nothing is fetched or changed, the debugger run loop is asked to stop, and the probe never resumes).
The I/O observer of the same patch records every CPU access of the hardware page in order.

This measures the S2 paths that docs/498-mpr1-tam-irq-measurement.md listed as NOT_RUN (PR #517 review
MAJOR: only the TAM path was measured): harness direct SetMpr at instruction 0 and mid-run, a CASE_IO-like
JSR (TAM to $FF then JSR; the #481 fixture itself is not reused) and PHA, the five block transfer opcodes
(armed under A they are refused before decode, so the opcode itself runs only in the controls and in the
mid-transfer cases tend / tendtin / tendtia / tendtdd / tendtai), VDC reads, any hardware page access (not
only VDC value changes), same-value writes, non-WRAM stack banks ($80 established; $F7 refusal only), and the
order/addresses of the IRQ-in-TAM pushes. The "irq" route under A refuses the CLI, so it does not exercise an
IRQ entry; the IRQ entry under A is irqtam.

Every armed run is judged on eight independent criteria (the stop is compared with the start of the
refused instruction):
  1 forbidden I/O side effect prevented   2 forbidden RAM write prevented   3 A/X/Y preserved
  4 PC preserved   5 S/P/MPR preserved   6 IRQ state (IRR, I flag) preserved   7 transfer state preserved
  8 safe to resume: 3-7 hold, the stop is outside a block transfer, and nothing ran after the refusal.
Verdict: UNSAFE if 1 or 2 fails or the guest ran after the refusal; SAFE_IN_SCOPE if all eight hold;
BLOCKED otherwise; UNKNOWN if no refusal was observed. SAFE_IN_SCOPE covers only what is observed here:
ST0/ST1/ST2 (WriteFromCPU), device-originated writes, the hardware clocking that still follows a refused
instruction in method C, the libretro build and real hardware are NOT covered and stay UNKNOWN.

Synthetic BIOS/CD only; no reference BIOS. Not a production mechanism. #173/#367/#459, BLOCKED_ABI,
release_eligible, DEV_CALLABLE and ABI VERIFIED are unchanged. The expectations below were written from the
pinned source before the first real-core run of this driver.

    python3 tests/integration/geargrafx_stack_methods.py      (make stack-methods; part of make integration)
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_stack_access_guard as G  # noqa: E402  (probe build/run helpers and row parsing)

METHODS = ("A",)   # S2 (#498). S4 (#500) compares A, C and A+C.
SAFE, BLOCKED, UNSAFE, UNKNOWN = "SAFE_IN_SCOPE", "BLOCKED", "UNSAFE", "UNKNOWN"
CRITERIA = ("io_prevented", "ram_prevented", "registers_preserved", "pc_preserved", "s_p_mpr_preserved",
            "irq_preserved", "transfer_preserved", "resume_safe")
H = G.HANDLER
W, R = G.WRITE, G.READ
PUSH_IO = [(W, 0x2104), (W, 0x2103), (W, 0x2102)]
POP_IO = [(R, 0x2102), (R, 0x2103), (R, 0x2104)]
TRANSFER_IO = PUSH_IO + POP_IO
BLOCK_OPS = ("tstart", "tin", "tia", "tdd", "tai")

# allowed: stop state of the positive run (MPR1 = $F8 throughout).
# unguarded: ordered hardware page accesses with a null guard (stack page = $FF).
MID_TRANSFER = ("tend", "tendtin", "tendtia", "tendtdd", "tendtai")   # MPR1 set before the TransferEnd step
ALLOWED = {
    **{r: dict(pc=G.ROUTES[r]["allowed"]["pc"], s=G.ROUTES[r]["allowed"]["s"], window=G.ROUTES[r]["allowed"]["window"])
       for r in G.ROUTES},
    **{op: dict(pc=0x1E07, s=0x04, window=[0, 0, 0x34, 0x12, 0x07]) for op in ("tin", "tia", "tdd", "tai")},
    **{op: dict(pc=0x1E07, s=0x04, window=[0, 0, 0x34, 0x12, 0x07]) for op in MID_TRANSFER[1:]},
    "flippha": dict(pc=0x1E02, s=0x03, window=[0, 0, 0, 0, 0x5A]),
    "tamjsr": dict(pc=0x1E10, s=0x02, window=[0, 0, 0, 0x04, 0x1E]),
}
UNGUARDED_IO = {
    **{r: G.ROUTES[r]["io"] for r in G.ROUTES},
    **{op: TRANSFER_IO for op in ("tin", "tia", "tdd", "tai")},
    **{op: POP_IO for op in MID_TRANSFER[1:]},
    "flippha": [(W, 0x2104)],
    "tamjsr": [(W, 0x2104), (W, 0x2103)],
}
ROUTES = tuple(ALLOWED)
# What a row does and does not exercise (printed with the results).
NOTES = {
    "irq": "method A refuses the CLI at instruction 1, so no IRQ is taken; IRQ-entry behaviour under A is shown "
           "only by irqtam",
    **{op: "armed under A: refused before the opcode is fetched (opcode not exercised); the opcode runs in the "
           "allowed/unguarded controls and in tend" + op for op in ("tin", "tia", "tdd", "tai")},
    "tstart": "armed under A: refused before the opcode is fetched; the mid-transfer case is tend",
    "tamjsr": "CASE_IO-like (TAM to $FF then JSR); the #481 CASE_IO fixture itself is not reused",
}

# Method A, armed (stack page $FF): expected verdict, failing criteria, refused instruction (index, PC).
# Index counts RunInstruction calls from 1. A block transfer is 3 calls: TransferStart, 1 byte, TransferEnd.
EXPECT_A = {r: dict(verdict=SAFE, failing=set(), index=1, pc=G.GUEST) for r in ROUTES}
EXPECT_A["flippha"] = dict(verdict=SAFE, failing=set(), index=2, pc=0x1E01)      # direct SetMpr before the PHA
EXPECT_A["tamjsr"] = dict(verdict=SAFE, failing=set(), index=2, pc=0x1E02)       # TAM ran; JSR refused
for _route in MID_TRANSFER:                                                      # stop inside the block transfer
    EXPECT_A[_route] = dict(verdict=BLOCKED, failing={"resume_safe"}, index=3, pc=G.GUEST, transfer_state=1,
                            legit_stack_bytes=3, dst=0x5A)
EXPECT_A["irqtam"] = dict(verdict=UNSAFE, failing={"io_prevented", "resume_safe"}, index=2, pc=H,
                          io=PUSH_IO)    # the IRQ pushes of the TAM's own RunInstruction land; then refused
# Non-WRAM stack banks (S1 #497: refused). $80 = CD-ROM RAM, $F7 = backup RAM.
BANK_ROUTES = ("pha", "jsr")
BANK_VARIANTS = {"ram": "cdram_bytes_changed", "bram": "bram_bytes_changed"}
UNGUARDED_RAM_BYTES = {"pha": 1, "jsr": 2}   # CD-ROM RAM bytes written by the pushes without a guard
# Real-core mutants of the refusal point: mutant -> (routes, expected verdict, criterion that must fail).
MUTANTS = {
    "mut-aignore": (("pha", "jsr", "tstart"), UNSAFE, "io_prevented"),    # refusal ignored: instruction runs
    "mut-alate": (("pha", "jsr", "tstart"), BLOCKED, "pc_preserved"),     # refusal after the opcode fetch
}


def _snapshot_fields(row, case=None):
    """Row integrity (not evidence): shape, the requested mode, and I/O counters matching the event log."""
    if case is not None and row.get("mode") != case:
        raise ValueError(f"{case}: measured wrong mode {row.get('mode')!r}")
    for name in ("start", "stop", "refusal_instruction_start"):
        G._snapshot(row, name)
    for name in ("io_reads", "io_writes", "instructions_after_refusal", "stack_bytes_changed",
                 "cdram_bytes_changed", "bram_bytes_changed", "irr_before", "irr_after"):
        G._int(row, name)
    for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer"):
        G._pair(row, name)
    dst = row.get("dst")
    if type(dst) is not list or len(dst) != 2 or not all(type(v) is int for v in dst):
        raise ValueError(f"{case}: malformed dst")
    if row.get("events_truncated") is False and len(G.io_accesses(row)) != row["io_reads"] + row["io_writes"]:
        raise ValueError(f"{case}: I/O counters do not match the event log")


def criteria(row, legit_stack_bytes=0, legit_dst=False):
    """The eight criteria of an armed run, from observations only (configuration fields are not read).

    Reference points: criteria 3-7 compare the stop with the start of the refused instruction; the IRR part
    of criterion 6 compares the run start with the run end. On an UNSAFE row (criterion 1 or 2 failed) the
    other criteria are informational: e.g. for IRQ-in-TAM under A the refused instruction is the handler's
    first one, which starts after the IRQ entry already set I.
    """
    _snapshot_fields(row)
    first, stop = row["refusal_instruction_start"], row["stop"]
    io = row["io_reads"] + row["io_writes"]
    vdc_same = all(G._pair(row, n)[0] == G._pair(row, n)[1] for n in ("ar", "r0", "r1", "r7", "sr", "read_buffer"))
    result = {
        "io_prevented": io == 0 and vdc_same,
        # RAM: the three stack banks and the block transfer destination (legitimate only where expected).
        "ram_prevented": row["cdram_bytes_changed"] == 0 and row["bram_bytes_changed"] == 0
                         and row["stack_bytes_changed"] == legit_stack_bytes
                         and (legit_dst or row["dst"][0] == row["dst"][1]),
        "registers_preserved": all(first[k] == stop[k] for k in ("a", "x", "y")),
        "pc_preserved": first["pc"] == stop["pc"],
        "s_p_mpr_preserved": first["s"] == stop["s"] and first["p"] == stop["p"] and first["mpr"] == stop["mpr"],
        "irq_preserved": row["irr_before"] == row["irr_after"] and (first["p"] & 0x04) == (stop["p"] & 0x04),
        "transfer_preserved": first["transfer_state"] == stop["transfer_state"],
    }
    # Resuming is never done; this judges whether the stop would be an exact point to resume from: no side
    # effect at all, nothing changed, outside a block transfer, and nothing ran after the refusal.
    # (instructions_after_refusal is the evidence that nothing ran; "halted" only restates the probe latch.)
    result["resume_safe"] = (row.get("refused") is True and row["instructions_after_refusal"] == 0
                             and stop["transfer_state"] == 0 and all(result[k] for k in CRITERIA[:7]))
    return result


def verdict(row, legit_stack_bytes=0, legit_dst=False):
    c = criteria(row, legit_stack_bytes, legit_dst)
    if not (c["io_prevented"] and c["ram_prevented"]) or row["instructions_after_refusal"] != 0:
        return UNSAFE, c
    if row.get("refused") is not True:
        return UNKNOWN, c
    return (SAFE if all(c.values()) else BLOCKED), c


def check_allowed(row, case, route):
    _snapshot_fields(row, case)
    want = ALLOWED[route]
    if row.get("exit", 0) != 0 or row.get("refused") is not False:
        raise ValueError(f"{case}: allowed run refused or failed ({row.get('exit')}, {row.get('refused')})")
    if row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: hardware page accessed with a WRAM stack")
    if row["stop"]["pc"] != want["pc"] or row["stop"]["s"] != want["s"]:
        raise ValueError(f"{case}: stop PC/S {row['stop']['pc']:#x}/{row['stop']['s']:#x}, "
                         f"want {want['pc']:#x}/{want['s']:#x}")
    if row.get("stack_window_after") != want["window"]:
        raise ValueError(f"{case}: stack window {row.get('stack_window_after')}, want {want['window']}")
    return "allowed_bytes_landed_in_wram"


def check_unguarded(row, case, route):
    _snapshot_fields(row, case)
    if row.get("exit", 0) != 0 or row.get("refused") is not False or row.get("guard_calls") != 0:
        raise ValueError(f"{case}: control consulted a guard or failed")
    if G.io_accesses(row) != UNGUARDED_IO[route]:
        raise ValueError(f"{case}: hardware page accesses {G.io_accesses(row)}, want {UNGUARDED_IO[route]}")
    return "io_page_accessed_without_guard"


def check_unguarded_bank(row, case, route, bank):
    _snapshot_fields(row, case)
    if row.get("exit", 0) != 0 or row.get("refused") is not False:
        raise ValueError(f"{case}: control refused or failed")
    if row["io_reads"] or row["io_writes"]:
        raise ValueError(f"{case}: hardware page accessed with a RAM stack bank")
    if bank == "ram":
        if row.get("cdram_present") is not True or row["cdram_bytes_changed"] != UNGUARDED_RAM_BYTES[route]:
            raise ValueError(f"{case}: CD-ROM RAM bytes changed {row['cdram_bytes_changed']}, "
                             f"want {UNGUARDED_RAM_BYTES[route]}")
        return "cdrom_ram_written_without_guard"
    # $F7: whether backup RAM accepts the write is NOT asserted (not established beforehand); reported only, so
    # the $F7 rows show a refusal but no established forbidden-write evidence (unlike $80).
    return f"NOT_ASSERTED backup_ram_present={row.get('bram_present')} bytes_changed={row['bram_bytes_changed']}"


def check_armed(row, case, expect):
    """Verdict and criteria must equal the expectation; refusal must be method A's, at the expected point."""
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe failed (exit {row.get('exit')})")
    _snapshot_fields(row, case)
    got, c = verdict(row, expect.get("legit_stack_bytes", 0), "dst" in expect)
    failing = {k for k, ok in c.items() if not ok}
    if got != expect["verdict"] or failing != expect["failing"]:
        raise ValueError(f"{case}: verdict {got} failing {sorted(failing)}, want {expect['verdict']} "
                         f"{sorted(expect['failing'])}")
    refusal = row.get("refusal", {})
    if refusal.get("kind") != expect.get("kind", "instruction"):
        raise ValueError(f"{case}: first refusal kind {refusal.get('kind')!r}, want {expect.get('kind', 'instruction')!r}")
    first = row["refusal_instruction_start"]
    if first["pc"] != expect["pc"] or first["transfer_state"] != expect.get("transfer_state", 0):
        raise ValueError(f"{case}: refused instruction at {first['pc']:#x} (transfer state {first['transfer_state']}), "
                         f"want {expect['pc']:#x}")
    if row.get("instructions") != expect["index"]:
        raise ValueError(f"{case}: {row.get('instructions')} instruction start(s), want {expect['index']}")
    if "io" in expect and G.io_accesses(row) != expect["io"]:
        raise ValueError(f"{case}: hardware page accesses {G.io_accesses(row)}, want {expect['io']}")
    if "dst" in expect and row.get("dst", [None, None])[1] != expect["dst"]:
        raise ValueError(f"{case}: destination {row.get('dst')}")
    return got, sorted(failing)


def falsified(row):
    """Each criterion must be able to fail: falsified copies of a SAFE row must not stay SAFE."""
    first = row["refusal_instruction_start"]

    def io(r, op):   # a consistent falsification: counter and event log together
        r["events"] = list(r.get("events", [])) + [{"seq": 999, "kind": "io", "op": op, "address": 0x2104, "value": 0,
                                                     "decision": -1, "latched": False, "mpr1": 0xFF, "instruction": 1}]
        r["io_writes" if op == W else "io_reads"] += 1

    changes = {
        "io_write": lambda r: io(r, W),
        "io_read": lambda r: io(r, R),
        "vdc_status": lambda r: r["vdc"]["sr"].__setitem__(1, r["vdc"]["sr"][0] ^ 1),
        "cdram_write": lambda r: r.update(cdram_bytes_changed=1),
        "bram_write": lambda r: r.update(bram_bytes_changed=1),
        "wram_stack_write": lambda r: r.update(stack_bytes_changed=r["stack_bytes_changed"] + 1),
        "a": lambda r: r["stop"].__setitem__("a", first["a"] ^ 1),
        "x": lambda r: r["stop"].__setitem__("x", first["x"] ^ 1),
        "y": lambda r: r["stop"].__setitem__("y", first["y"] ^ 1),
        "pc": lambda r: r["stop"].__setitem__("pc", first["pc"] + 1),
        "s": lambda r: r["stop"].__setitem__("s", (first["s"] - 1) & 0xFF),
        "p": lambda r: r["stop"].__setitem__("p", first["p"] ^ 0x02),
        "mpr": lambda r: r["stop"].__setitem__("mpr", [m ^ 1 for m in first["mpr"]]),
        "irr": lambda r: r.update(irr_after=r["irr_before"] ^ 1),
        "i_flag": lambda r: r["stop"].__setitem__("p", first["p"] ^ 0x04),
        "transfer_state": lambda r: r["stop"].__setitem__("transfer_state", first["transfer_state"] + 1),
        "resumed": lambda r: r.update(instructions_after_refusal=1),
        "dst_write": lambda r: r["dst"].__setitem__(1, r["dst"][0] ^ 1),
        "no_refusal": lambda r: r.update(refused=False),
    }
    for name, change in changes.items():
        broken = copy.deepcopy(row)
        change(broken)
        yield name, broken


def cases():
    for method in METHODS:
        for route in ROUTES:
            for variant in ("allowed", "armed", "unguarded"):
                yield f"{method}:{route}-{variant}"
        for route in BANK_ROUTES:
            for bank in BANK_VARIANTS:
                yield f"{method}:{route}-armed{bank}"
                yield f"{method}:{route}-unguarded{bank}"
        for mutant, (routes, _, _) in MUTANTS.items():
            for route in routes:
                yield f"{method}:{route}-{mutant}"


def evaluate(rows):
    table, detections, notes = {}, [], []
    for method in METHODS:
        for route in ROUTES:
            base = f"{method}:{route}"
            check_allowed(rows[f"{base}-allowed"], f"{base}-allowed", route)
            check_unguarded(rows[f"{base}-unguarded"], f"{base}-unguarded", route)
            got, failing = check_armed(rows[f"{base}-armed"], f"{base}-armed", EXPECT_A[route])
            table[(method, route)] = dict(verdict=got, failing=failing,
                                          criteria=criteria(rows[f"{base}-armed"],
                                                            EXPECT_A[route].get("legit_stack_bytes", 0),
                                                            "dst" in EXPECT_A[route]),
                                          io=G.io_accesses(rows[f"{base}-armed"]))
            control, _ = verdict(rows[f"{base}-unguarded"])
            if control != UNSAFE:
                raise ValueError(f"{base}-unguarded: guard-less control judged {control}, want UNSAFE")
            detections.append((f"{base}-unguarded", "guard disabled", control))
            if got == SAFE:
                for name, broken in falsified(rows[f"{base}-armed"]):
                    v, _ = verdict(broken)
                    if v == SAFE:
                        raise ValueError(f"{base}-armed: falsified {name} still judged SAFE")
                    detections.append((f"{base}-armed", f"falsified:{name}", v))
        for route in BANK_ROUTES:
            for bank, field in BANK_VARIANTS.items():
                base = f"{method}:{route}"
                got, failing = check_armed(rows[f"{base}-armed{bank}"], f"{base}-armed{bank}",
                                           dict(verdict=SAFE, failing=set(), index=1, pc=G.GUEST))
                if rows[f"{base}-armed{bank}"]["refusal"].get("mpr1") != (0x80 if bank == "ram" else 0xF7):
                    raise ValueError(f"{base}-armed{bank}: refusal saw MPR1 {rows[f'{base}-armed{bank}']['refusal']}")
                table[(method, f"{route}@{bank}")] = dict(verdict=got, failing=failing,
                                                          criteria=criteria(rows[f"{base}-armed{bank}"]), io=[])
                notes.append((f"{base}-unguarded{bank}",
                              check_unguarded_bank(rows[f"{base}-unguarded{bank}"], f"{base}-unguarded{bank}",
                                                   route, bank)))
        for mutant, (routes, want, must_fail) in MUTANTS.items():
            for route in routes:
                case = f"{method}:{route}-{mutant}"
                _snapshot_fields(rows[case], case)
                v, c = verdict(rows[case])
                if v != want or c[must_fail]:
                    raise ValueError(f"mutation NOT detected as expected: {case} judged {v} "
                                     f"({must_fail}={c[must_fail]}), want {want} with {must_fail} failing")
                detections.append((case, mutant, v))
    return table, detections, notes


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-methods-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = G.build_probes(directory)
        rows = {case: G.run_case(probe, bios, cue, case) for case in cases()}
    table, detections, notes = evaluate(rows)
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": G.PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, result in detections:
        print(f"DETECTED {case} [{label}]: {result}")
    for case, note in notes:
        print(f"NOTE {case}: {note}")
    for (method, route), result in table.items():
        flags = " ".join(f"{k}={'Y' if v else 'N'}" for k, v in result["criteria"].items())
        note = NOTES.get(route) if "@" not in route else (
            "refusal shown; a forbidden write to $F7 is not established" if route.endswith("@bram") else None)
        print(f"ROUTE {method} {route}: {result['verdict']} {flags} io={result['io']}"
              + (f" NOTE: {note}" if note else ""))
    print(f"EXPECTATIONS_MET issue-498 method A measurement: {len(rows)} real-core runs, {len(detections)} detections, "
          f"expectations met (pinned core {G.PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only patch)")
    print("UNSAFE issue-498 method A: IRQ-in-TAM. The TAM's own RunInstruction takes the pending IRQ after MPR1 = $FF "
          "and its three entry pushes land on the hardware page ($2104, $2103, $2102 in that order) before the next "
          "instruction boundary. Method A alone does not close this hole.")
    print("BLOCKED issue-498 method A: a mid-transfer direct SetMpr (tend, tendtin/tia/tdd/tai) is refused before the TransferEnd step "
          "without any side effect, but the stop is inside the block transfer (transfer state 1), so it is not a "
          "safe point to resume.")
    print("UNKNOWN (not covered): ST0/ST1/ST2 WriteFromCPU, device-originated writes, the libretro build, a new IRQ "
          "assert inside the TAM instruction, real hardware. NMI: not applicable on the pinned core. No gate changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-498 stack methods: {error}", file=sys.stderr)
        raise SystemExit(1) from error
