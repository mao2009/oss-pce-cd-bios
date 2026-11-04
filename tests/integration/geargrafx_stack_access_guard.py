#!/usr/bin/env python3
"""Issue #499 (S3 of #473, method C): stack push/pop refused before the side effect, on the pinned real core.

A VERIFICATION-ONLY patch (patches/geargrafx-b49ae82-stack-access-guard.patch, against
b49ae82a012eade566d72e9d61c9d95a5caa4da3) makes HuC6280::StackPush8/16 and StackPop8/16 ask a guard BEFORE
any stack memory access and BEFORE S changes, and observes every CPU access of the hardware page through
an observation-only hook in Memory::Read/Write. A refusal latches, performs no access, leaves S, and asks
the debugger run loop to stop after the current instruction; the probe never resumes the guest. The
probe (geargrafx_stack_access_guard.cpp) runs the core with RunToVBlank (debugger build, no stepping).

The patched source tree is staged in a temporary directory and every core object is rebuilt from it
(the hooks are inline-header code). The stock tree is not modified. The GPL-3.0 core is linked locally and
never distributed. Synthetic BIOS/CD only; no reference BIOS or ROM is read. This is NOT a production
safety mechanism. Nothing here changes BLOCKED_ABI, release_eligible, DEV_CALLABLE, ABI VERIFIED, the
reference JSR gate, #173, #367 or #459.

Per route (JSR PHA PHP BSR RTS RTI IRQ-entry TransferStart TransferEnd, plus BRK PLA PLP PHX PHY PLX PLY,
IRQ-in-TAM, a same-value write and a write-then-restore) the driver runs:
* ``allowed``   (POSITIVE) MPR1 = $F8: the guard allows; the bytes land in WRAM; no I/O page access.
* ``armed``     (NEGATIVE) MPR1 = $FF: the guard refuses before the access. Two separate judgements:
    - side effect: no I/O page access at all, VDC/stack WRAM/destination unchanged, S unchanged at the
      refusal, the guest not resumed. Any failure here is UNSAFE.
    - internal state: the CPU state at the stop is compared with the state at the start of the refused
      instruction. The refused instruction still runs to its end (a refused pop yields 0, a refused JSR
      still loads its target, TransferStart has already set its transfer state). Only a stop where
      nothing but PC moved over the fetched bytes is PASS; any other change is BLOCKED (side effect
      prevented, stop not consistent). Changes are never assumed to be reversible.
* ``unguarded`` (CONTROL) null guard, MPR1 = $FF: the I/O page accesses must be observed.
* mutants (``mut-late16`` / ``mut-popinc`` / ``mut-nolatch`` / ``mut-ignore`` / ``mut-nostop``): patch mutants switched on at
  run time; the armed classifier must reject every one. Every unguarded control is also fed to the armed
  classifier and must be rejected.

The expected route verdicts and stop states below were written from the pinned source (huc6280_inline.h,
huc6280_opcodes*.{h,cpp}) BEFORE the first real-core run of this driver.

Run through the Makefile (``make stack-access-guard``; part of ``make integration``, which
``make test-real-core`` runs in CI), or directly:
    python3 tests/integration/geargrafx_stack_access_guard.py
"""
import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
PATCH = Path(__file__).resolve().parent / "patches" / "geargrafx-b49ae82-stack-access-guard.patch"
PROBE_SOURCE = "geargrafx_stack_access_guard.cpp"
FRAMES = 2
GUEST = 0x1E00
HANDLER = 0x1E40
WRAM_MPR = 0xF8
IO_MPR = 0xFF
PUSH8, PUSH16, POP8, POP16 = 0, 1, 2, 3
READ, WRITE = 0, 1
REGISTERS = ("a", "x", "y", "p", "s", "transfer_state")
PASS, BLOCKED, UNSAFE, NOT_MEASURED = "PASS", "BLOCKED", "UNSAFE", "NOT_MEASURED"

# Stack window = WRAM $0100..$0104 (CPU $2100..$2104 when MPR1 = $F8).
#   allowed: stop state, window after, guard calls
#   armed:   first refused op, guard calls / latched calls, fields changed at the stop, stop PC, verdict
#   unguarded: I/O page accesses (op, address) in order
ROUTES = {
    "jsr": dict(allowed=dict(pc=0x1E10, s=0x02, window=[0, 0, 0, 0x02, 0x1E], calls=1),
                armed=dict(op=PUSH16, calls=1, latched=0, changed={"pc"}, pc=0x1E10, verdict=BLOCKED),
                io=[(WRITE, 0x2104), (WRITE, 0x2103)]),
    "pha": dict(allowed=dict(pc=0x1E01, s=0x03, window=[0, 0, 0, 0, 0x5A], calls=1),
                armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                io=[(WRITE, 0x2104)]),
    "php": dict(allowed=dict(pc=0x1E01, s=0x03, window=[0, 0, 0, 0, 0x14], calls=1),
                armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                io=[(WRITE, 0x2104)]),
    "phx": dict(allowed=dict(pc=0x1E01, s=0x03, window=[0, 0, 0, 0, 0x34], calls=1),
                armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                io=[(WRITE, 0x2104)]),
    "phy": dict(allowed=dict(pc=0x1E01, s=0x03, window=[0, 0, 0, 0, 0x07], calls=1),
                armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                io=[(WRITE, 0x2104)]),
    "bsr": dict(allowed=dict(pc=0x1E10, s=0x02, window=[0, 0, 0, 0x01, 0x1E], calls=1),
                armed=dict(op=PUSH16, calls=1, latched=0, changed={"pc"}, pc=0x1E10, verdict=BLOCKED),
                io=[(WRITE, 0x2104), (WRITE, 0x2103)]),
    "brk": dict(allowed=dict(pc=HANDLER, s=0x01, p=0x04, window=[0, 0, 0x14, 0x02, 0x1E], calls=2),
                armed=dict(op=PUSH16, calls=2, latched=1, changed={"pc"}, pc=HANDLER, verdict=BLOCKED),
                io=[(WRITE, 0x2104), (WRITE, 0x2103), (WRITE, 0x2102)]),
    "rts": dict(allowed=dict(pc=0x1E10, s=0x03, window=[0, 0, 0x0F, 0x1E, 0], calls=1),
                armed=dict(op=POP16, calls=1, latched=0, changed={"pc"}, pc=0x0001, verdict=BLOCKED),
                io=[(READ, 0x2102), (READ, 0x2103)]),
    "rti": dict(allowed=dict(pc=0x1E10, s=0x04, p=0x04, window=[0, 0, 0x04, 0x10, 0x1E], calls=2),
                armed=dict(op=POP8, calls=2, latched=1, changed={"pc", "p"}, pc=0x0000, verdict=BLOCKED),
                io=[(READ, 0x2102), (READ, 0x2103), (READ, 0x2104)]),
    "pla": dict(allowed=dict(pc=0x1E01, s=0x02, a=0x66, window=[0, 0, 0x66, 0, 0], calls=1),
                armed=dict(op=POP8, calls=1, latched=0, changed={"pc", "a", "p"}, pc=0x1E01, verdict=BLOCKED),
                io=[(READ, 0x2102)]),
    "plp": dict(allowed=dict(pc=0x1E01, s=0x02, p=0x05, window=[0, 0, 0x05, 0, 0], calls=1),
                armed=dict(op=POP8, calls=1, latched=0, changed={"pc", "p"}, pc=0x1E01, verdict=BLOCKED),
                io=[(READ, 0x2102)]),
    "plx": dict(allowed=dict(pc=0x1E01, s=0x02, x=0x66, window=[0, 0, 0x66, 0, 0], calls=1),
                armed=dict(op=POP8, calls=1, latched=0, changed={"pc", "x", "p"}, pc=0x1E01, verdict=BLOCKED),
                io=[(READ, 0x2102)]),
    "ply": dict(allowed=dict(pc=0x1E01, s=0x02, y=0x66, window=[0, 0, 0x66, 0, 0], calls=1),
                armed=dict(op=POP8, calls=1, latched=0, changed={"pc", "y", "p"}, pc=0x1E01, verdict=BLOCKED),
                io=[(READ, 0x2102)]),
    "irq": dict(allowed=dict(pc=HANDLER, s=0x01, p=0x04, window=[0, 0, 0x00, 0x01, 0x1E], calls=2),
                armed=dict(op=PUSH16, calls=2, latched=1, changed={"pc", "p"}, pc=HANDLER, verdict=BLOCKED,
                           instruction_pc=0x1E01),
                io=[(WRITE, 0x2104), (WRITE, 0x2103), (WRITE, 0x2102)]),
    "irqtam": dict(allowed=dict(pc=HANDLER, s=0x01, p=0x04, window=[0, 0, 0x00, 0x02, 0x1E], calls=2),
                   armed=dict(op=PUSH16, calls=2, latched=1, changed={"pc", "p", "mpr"}, pc=HANDLER,
                              verdict=BLOCKED, stop_mpr1=IO_MPR),
                   io=[(WRITE, 0x2104), (WRITE, 0x2103), (WRITE, 0x2102)]),
    "tstart": dict(allowed=dict(pc=0x1E07, s=0x04, a=0x12, x=0x34, y=0x07, window=[0, 0, 0x34, 0x12, 0x07],
                                calls=6, dst=0x5A),
                   # TransferStart sets the transfer state to 2 BEFORE its first push (pinned source).
                   armed=dict(op=PUSH8, calls=3, latched=2, changed={"transfer_state"}, pc=GUEST, verdict=BLOCKED,
                              refusal_transfer_state=2, stop_transfer_state=2),
                   io=[(WRITE, 0x2104), (WRITE, 0x2103), (WRITE, 0x2102),
                       (READ, 0x2102), (READ, 0x2103), (READ, 0x2104)]),
    "tend": dict(allowed=dict(pc=0x1E07, s=0x04, a=0x12, x=0x34, y=0x07, window=[0, 0, 0x34, 0x12, 0x07],
                              calls=6, dst=0x5A),
                 # TransferEnd sets the transfer state to 0 BEFORE its first pop (pinned source); the refused
                 # instruction started with state 1.
                 armed=dict(op=POP8, calls=6, latched=2, changed={"pc", "transfer_state", "a", "x", "y"},
                            pc=0x1E07, verdict=BLOCKED, refusal_transfer_state=0, instruction_transfer_state=1,
                            stop_transfer_state=0,
                            window=[0, 0, 0x34, 0x12, 0x07], stack_bytes_changed=3, dst=0x5A),
                 io=[(READ, 0x2102), (READ, 0x2103), (READ, 0x2104)]),
    "phasame": dict(allowed=dict(pc=0x1E01, s=0x03, window=[0, 0, 0, 0, 0x05], calls=1),
                    armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                    io=[(WRITE, 0x2104)], state_invisible=True),
    # ST0 #$00 restores VDC AR through HuC6202::WriteFromCPU, which is NOT the Memory::Write path, so the
    # observer never sees it (by design: only the PHA write to $2104 is the logged event). In the allowed
    # run the ST0 still writes AR; "no I/O page access" there means no CPU Memory::Read/Write of bank $FF.
    "pharestore": dict(allowed=dict(pc=0x1E03, s=0x03, window=[0, 0, 0, 0, 0x07], calls=1),
                       armed=dict(op=PUSH8, calls=1, latched=0, changed={"pc"}, pc=0x1E01, verdict=PASS),
                       io=[(WRITE, 0x2104)], state_invisible=True),
}
# Nine routes required by #499, then the extra ones.
REQUIRED_ROUTES = ("jsr", "pha", "php", "bsr", "rts", "rti", "irq", "tstart", "tend")
EXTRA_ROUTES = ("brk", "pla", "plp", "phx", "phy", "plx", "ply", "irqtam", "phasame", "pharestore")
FETCHED = {"pha": 1, "php": 1, "phx": 1, "phy": 1, "phasame": 1, "pharestore": 1}  # bytes fetched before a push8
MUTANTS = {
    "mut-late16": ("jsr", "bsr", "brk", "irq", "irqtam"),            # push16 partial write
    "mut-popinc": ("rts", "rti", "pla", "plp", "plx", "ply", "tend"),  # pop moves S before the check
    "mut-nolatch": ("jsr", "rti", "irq", "tstart"),                  # no latch, no stop: guest resumed
    "mut-ignore": ("pha", "rts", "tstart"),                          # refusal computed but not enforced
    "mut-nostop": ("pha", "jsr", "tstart"),                          # latch kept, stop request removed
}
# Internal-state labels. PASS routes keep every register except PC, which has moved past the refused
# instruction: the refused push is dropped, so a re-run would have to rewind PC to the instruction start.
STATE_PASS = "REGISTERS_UNCHANGED_PC_ADVANCED"
STATE_BLOCKED = "CHANGED"


def _int(row, *path):
    value = row
    for key in path:
        if type(value) is not dict or key not in value:
            raise ValueError(f"missing {'.'.join(path)}")
        value = value[key]
    if type(value) is not int:
        raise ValueError(f"non-integer {'.'.join(path)}: {value!r}")
    return value


def _snapshot(row, name):
    snap = row.get(name)
    if type(snap) is not dict:
        raise ValueError(f"missing snapshot {name}")
    for key in ("pc", *REGISTERS):
        _int(row, name, key)
    mpr = snap.get("mpr")
    if type(mpr) is not list or len(mpr) != 8 or not all(type(v) is int for v in mpr):
        raise ValueError(f"malformed {name}.mpr")
    return snap


def _pair(row, name):
    pair = row.get("vdc", {}).get(name)
    if type(pair) is not list or len(pair) != 2 or not all(type(v) is int for v in pair):
        raise ValueError(f"malformed vdc.{name}")
    return pair


def _events(row):
    events = row.get("events")
    if type(events) is not list or row.get("events_truncated") is not False:
        raise ValueError("event log missing or truncated")
    for event in events:
        if type(event) is not dict or event.get("kind") not in ("stack", "io"):
            raise ValueError(f"malformed event {event!r}")
    return events


def io_accesses(row):
    """Ordered (op, address) list of every observed hardware page access."""
    return [(e["op"], e["address"]) for e in _events(row) if e["kind"] == "io"]


def _common(row, case):
    if row.get("exit", 0) != 0:
        raise ValueError(f"{case}: probe refused to run (exit {row.get('exit')}): {row.get('stderr', '')[-300:]}")
    if row.get("mode") != case:
        raise ValueError(f"{case}: measured wrong mode {row.get('mode')!r}")
    for name in ("start", "stop", "refusal_instruction_start", "at_refusal"):
        _snapshot(row, name)
    for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer"):
        _pair(row, name)
    for name in ("guard_calls", "guard_refusals", "guard_latched_calls", "instructions",
                 "instructions_after_refusal", "io_reads", "io_writes", "io_after_refusal", "stack_bytes_changed"):
        _int(row, name)
    if row.get("events_truncated") is False and len(io_accesses(row)) != row["io_reads"] + row["io_writes"]:
        raise ValueError(f"{case}: I/O counters do not match the event log")
    route = case.split("-", 1)[0]
    if route not in ROUTES:
        raise ValueError(f"{case}: unknown route")
    return route, ROUTES[route]


def check_allowed(row, case):
    """POSITIVE: MPR1 = $F8, the guard allows every access, the bytes land in WRAM, no I/O page access."""
    route, spec = _common(row, case)
    want = spec["allowed"]
    if row.get("guarded") is not True or row.get("refused") is not False or row.get("latched") is not False:
        raise ValueError(f"{case}: allowed run refused or was not guarded")
    if row["guard_calls"] != want["calls"] or row["guard_refusals"] != 0:
        raise ValueError(f"{case}: guard calls/refusals {row['guard_calls']}/{row['guard_refusals']}, "
                         f"want {want['calls']}/0")
    if io_accesses(row):
        raise ValueError(f"{case}: I/O page accessed with a WRAM stack: {io_accesses(row)}")
    stop = row["stop"]
    for key in ("pc", "s", "a", "x", "y", "p"):
        if key in want and stop[key] != want[key]:
            raise ValueError(f"{case}: stop {key} {stop[key]:#x}, want {want[key]:#x}")
    if row.get("stack_window_after") != want["window"]:
        raise ValueError(f"{case}: stack window {row.get('stack_window_after')}, want {want['window']}")
    if "dst" in want and row.get("dst", [None, None])[1] != want["dst"]:
        raise ValueError(f"{case}: destination {row.get('dst')}, want {want['dst']:#x}")
    if stop["mpr"][1] != WRAM_MPR or stop["transfer_state"] != 0:
        raise ValueError(f"{case}: stop MPR1 {stop['mpr'][1]:#x} / transfer state {stop['transfer_state']}")
    return "allowed_bytes_landed_in_wram"


def side_effect_check(row, case):
    """Side-effect axis of an armed run. Raises (UNSAFE) on any I/O access or forbidden state write.

    Only observations are judged. The run's configuration fields (guarded, mutation, variant) are NOT read,
    so a mutant or a guard-less control can only be rejected through what the core actually did.
    """
    route, spec = _common(row, case)
    want = spec["armed"]
    # Counters first (they are never truncated), then the ordered event log.
    if row["io_reads"] or row["io_writes"] or row["io_after_refusal"]:
        first_io = io_accesses(row)[:6] if row.get("events_truncated") is False else "log truncated"
        raise ValueError(f"{case}: I/O page accessed ({row['io_reads']} read(s), {row['io_writes']} write(s); "
                         f"first {first_io})")
    if row["instructions_after_refusal"] != 0:
        raise ValueError(f"{case}: guest resumed for {row['instructions_after_refusal']} instruction(s) after the refusal")
    if io_accesses(row):
        raise ValueError(f"{case}: I/O page accessed: {io_accesses(row)}")
    for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer"):
        before, after = _pair(row, name)
        if before != after:
            raise ValueError(f"{case}: VDC {name} changed {before:#x}->{after:#x}")
    if row.get("refused") is not True or row.get("latched") is not True or row.get("halted") is not True:
        raise ValueError(f"{case}: no latched refusal / halt (refused={row.get('refused')}, "
                         f"latched={row.get('latched')}, halted={row.get('halted')})")
    for key in ("op", "s", "transfer_state", "mpr1", "vdc_ar"):
        _int(row, "refusal", key)
    if row["refusal"]["vdc_ar"] != _pair(row, "ar")[0]:
        raise ValueError(f"{case}: VDC AR already changed when the guard was called "
                         f"({_pair(row, 'ar')[0]:#x}->{row['refusal']['vdc_ar']:#x})")
    if _int(row, "irr_before") != _int(row, "irr_after"):
        raise ValueError(f"{case}: interrupt request register changed {row['irr_before']:#x}->{row['irr_after']:#x}")
    if row["refusal"]["op"] != want["op"] or row["refusal"]["mpr1"] != IO_MPR:
        raise ValueError(f"{case}: first refusal op {row['refusal'].get('op')} MPR1 {row['refusal'].get('mpr1')}, "
                         f"want {want['op']} / {IO_MPR:#x}")
    if row["guard_calls"] != want["calls"] or row["guard_latched_calls"] != want["latched"] \
            or row["guard_refusals"] != want["latched"] + 1:
        raise ValueError(f"{case}: guard calls/latched/refusals {row['guard_calls']}/{row['guard_latched_calls']}/"
                         f"{row['guard_refusals']}, want {want['calls']}/{want['latched']}/{want['latched'] + 1}")
    first = row["refusal_instruction_start"]
    if row["refusal"].get("s") != first["s"] or row["at_refusal"]["s"] != first["s"] or row["stop"]["s"] != first["s"]:
        raise ValueError(f"{case}: S moved around the refusal (instruction start {first['s']:#x}, at refusal "
                         f"{row['at_refusal']['s']:#x}, stop {row['stop']['s']:#x})")
    if row["refusal"]["transfer_state"] != want.get("refusal_transfer_state", 0):
        raise ValueError(f"{case}: transfer state passed to the guard {row['refusal']['transfer_state']}, "
                         f"want {want.get('refusal_transfer_state', 0)}")
    if first["transfer_state"] != want.get("instruction_transfer_state", 0):
        raise ValueError(f"{case}: refused instruction started with transfer state {first['transfer_state']}")
    window = want.get("window", row.get("stack_window_before"))
    if row.get("stack_window_after") != window or row["stack_bytes_changed"] != want.get("stack_bytes_changed", 0):
        raise ValueError(f"{case}: stack WRAM changed (window {row.get('stack_window_after')}, "
                         f"{row['stack_bytes_changed']} byte(s))")
    dst = row.get("dst")
    if type(dst) is not list or dst[1] != want.get("dst", dst[0]):
        raise ValueError(f"{case}: destination byte {dst}")
    return route, want


def state_check(row, case, want):
    """Internal-state axis: fields changed between the refused instruction's start and the stop."""
    first, stop = row["refusal_instruction_start"], row["stop"]
    if "instruction_pc" in want and first["pc"] != want["instruction_pc"]:
        raise ValueError(f"{case}: refused instruction started at {first['pc']:#x}, want {want['instruction_pc']:#x}")
    changed = {key for key in REGISTERS if first[key] != stop[key]}
    if first["pc"] != stop["pc"]:
        changed.add("pc")
    if first["mpr"] != stop["mpr"]:
        changed.add("mpr")
    if changed != want["changed"]:
        raise ValueError(f"{case}: changed fields {sorted(changed)}, want {sorted(want['changed'])}")
    if stop["pc"] != want["pc"]:
        raise ValueError(f"{case}: stop PC {stop['pc']:#x}, want {want['pc']:#x}")
    if "stop_transfer_state" in want and stop["transfer_state"] != want["stop_transfer_state"]:
        raise ValueError(f"{case}: stop transfer state {stop['transfer_state']}")
    if "stop_mpr1" in want and stop["mpr"][1] != want["stop_mpr1"]:
        raise ValueError(f"{case}: stop MPR1 {stop['mpr'][1]:#x}")
    route = case.split("-", 1)[0]
    consistent = changed == {"pc"} and route in FETCHED and stop["pc"] == first["pc"] + FETCHED[route]
    verdict = PASS if consistent else BLOCKED
    if verdict != want["verdict"]:
        raise ValueError(f"{case}: route verdict {verdict}, expected {want['verdict']}")
    return verdict, sorted(changed)


def check_armed(row, case):
    route, want = side_effect_check(row, case)
    verdict, changed = state_check(row, case, want)
    return verdict, changed


def check_unguarded(row, case):
    """CONTROL: null guard; the stack accesses must reach the I/O page (observed in order)."""
    route, spec = _common(row, case)
    if row.get("guarded") is not False or row["guard_calls"] != 0 or row.get("refused") is not False:
        raise ValueError(f"{case}: unguarded run consulted a guard")
    seen = io_accesses(row)
    if seen != spec["io"]:
        raise ValueError(f"{case}: I/O page accesses {seen}, want {spec['io']}")
    if spec.get("state_invisible"):
        ar_before, ar_after = _pair(row, "ar")
        if ar_before != ar_after:
            raise ValueError(f"{case}: VDC AR {ar_before:#x}->{ar_after:#x}; the case must be invisible in the final state")
    return "io_page_accessed_without_guard"


def state_only_classifier_accepts(row):
    """A deliberately weak classifier (final VDC/stack state only, no event log). Used to show that the
    same-value write and the write-then-restore cases need the event log."""
    for name in ("ar", "r0", "r1", "r7", "sr", "read_buffer"):
        before, after = _pair(row, name)
        if before != after:
            return False
    return row.get("stack_bytes_changed") == 0


def must_reject(row, case, label):
    try:
        check_armed(row, case)
    except ValueError as error:
        return str(error)
    raise ValueError(f"mutation NOT detected: {label} ({case}) was accepted by the armed classifier")


def falsified_rows(row, case):
    """Falsified copies of an accepted armed row: each must be rejected (proves the checks can fail)."""
    route = case.split("-", 1)[0]
    first = row["refusal_instruction_start"]
    out = []

    def put(name, update):
        broken = copy.deepcopy(row)
        update(broken)
        out.append((name, broken))

    put("io_write_added", lambda r: (r["events"].append({"seq": 999, "kind": "io", "op": WRITE, "address": 0x2104,
                                                          "value": 0, "decision": -1, "latched": False,
                                                          "mpr1": IO_MPR, "instruction": 1}),
                                     r.update(io_writes=r["io_writes"] + 1)))
    put("io_read_added", lambda r: (r["events"].append({"seq": 999, "kind": "io", "op": READ, "address": 0x2104,
                                                         "value": 0, "decision": -1, "latched": False,
                                                         "mpr1": IO_MPR, "instruction": 1}),
                                    r.update(io_reads=r["io_reads"] + 1)))
    put("vdc_ar_changed", lambda r: r["vdc"]["ar"].__setitem__(1, r["vdc"]["ar"][0] ^ 1))
    put("vdc_status_cleared", lambda r: r["vdc"]["sr"].__setitem__(1, r["vdc"]["sr"][0] ^ 1))
    put("s_moved", lambda r: r["stop"].__setitem__("s", (r["stop"]["s"] + 1) & 0xFF))
    put("s_moved_at_refusal", lambda r: r["at_refusal"].__setitem__("s", (r["at_refusal"]["s"] - 1) & 0xFF))
    put("resumed", lambda r: r.update(instructions_after_refusal=1))
    put("not_halted", lambda r: r.update(halted=False))
    put("not_latched", lambda r: r.update(latched=False))
    put("stack_wram_written", lambda r: r.update(stack_bytes_changed=r["stack_bytes_changed"] + 1))
    put("wrong_first_op", lambda r: r["refusal"].update(op=(r["refusal"]["op"] + 1) % 4))
    put("allowed_not_refused", lambda r: r["refusal"].update(mpr1=WRAM_MPR))
    put("extra_register_change", lambda r: r["stop"].__setitem__("a", first["a"] ^ 0xFF if "a" not in
                                                                    ROUTES[route]["armed"]["changed"] else first["a"]))
    put("transfer_state_changed", lambda r: r["stop"].__setitem__(
        "transfer_state", (r["stop"]["transfer_state"] + 1) % 3))
    put("events_truncated", lambda r: r.update(events_truncated=True))
    put("irr_changed", lambda r: r.update(irr_after=r["irr_before"] ^ 1))
    put("vdc_ar_written_before_the_guard", lambda r: r["refusal"].update(vdc_ar=r["refusal"]["vdc_ar"] ^ 1))
    return out


def build_probes(directory):
    """Stage the pinned tree, apply the verification patch, rebuild every object, link the probe."""
    from geargrafx_api import build_command  # (also puts tools/ on sys.path)
    from environment import dependency_dir
    source = dependency_dir("geargrafx").with_name(dependency_dir("geargrafx").name + "-debugger")
    staged = directory / "patched-core"
    shutil.copytree(source, staged, symlinks=True)
    build_command(["patch", "-p1", "--batch", "--forward", "-d", str(staged), "-i", str(PATCH)])
    make_dir = staged / "platforms/libretro"
    objects = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                             "--eval", "probe-objects:;@echo $(OBJECTS)", "probe-objects"]).stdout.split()
    if not objects:
        raise ValueError("BLOCKED: no core objects listed; run make setup")
    for obj in staged.rglob("*.o"):
        obj.unlink()  # never reuse stock objects: every object is rebuilt from the patched tree
    # Debugger-build flags, as tools/setup.py build_geargrafx(debugger=True) passes them (same as
    # geargrafx_irq_entry_guard.py). GG_DISABLE_DISASSEMBLER is absent, so the stop request is available.
    includes = build_command(["make", "-s", "--no-print-directory", "-C", str(make_dir),
                              "--eval", "print-includes:;@echo $(INCLUDES)", "print-includes"]).stdout.strip()
    flags = ("-O2 -fPIC -fno-exceptions -D__LIBRETRO__ -DZ7_ST -DZSTD_DISABLE_ASM "
             "-DGG_DISABLE_VGMRECORDER -DGG_ENABLE_CDROM_CUEBIN_READAHEAD -pthread " + includes)
    cflags, cxxflags = flags, flags + " -fno-rtti"
    build_command(["make", "-s", "-C", str(make_dir), "-j", str(min(4, os.cpu_count() or 2)),
                   f"CFLAGS={cflags}", f"CXXFLAGS={cxxflags}", *objects])
    objs = [(make_dir / p).resolve() for p in objects]
    if not all(p.is_file() for p in objs):
        raise ValueError("BLOCKED: patched core objects missing after rebuild")
    probe = directory / "stack-access-guard-probe"
    build_command(["g++", *shlex.split(cxxflags), "-std=c++11", "-c", str(Path(__file__).with_name(PROBE_SOURCE)),
                   "-o", str(directory / "probe.o")], cwd=str(make_dir))
    build_command(["g++", str(directory / "probe.o"), *map(str, objs), "-pthread", "-lm", "-o", str(probe)])
    return probe


def run_case(probe, bios, cue, case):
    result = subprocess.run([str(probe), str(bios), str(cue), case, str(FRAMES)], capture_output=True, text=True,
                            timeout=120, check=False)
    if result.returncode:
        return {"mode": case, "exit": result.returncode, "stderr": result.stderr[-400:]}
    try:
        row = json.loads(result.stderr)
    except ValueError as error:
        raise ValueError(f"{case}: probe emitted no JSON observation") from error
    if type(row) is not dict:
        raise ValueError(f"{case}: observation is not an object")
    row["exit"] = 0
    return row


def evaluate(rows):
    """Classify every row; returns (table, mutation results). Raises ValueError on any unexpected result."""
    table, mutations = {}, []
    for route in (*REQUIRED_ROUTES, *EXTRA_ROUTES):
        allowed = check_allowed(rows[f"{route}-allowed"], f"{route}-allowed")
        verdict, changed = check_armed(rows[f"{route}-armed"], f"{route}-armed")
        control = check_unguarded(rows[f"{route}-unguarded"], f"{route}-unguarded")
        reason = must_reject(rows[f"{route}-unguarded"], f"{route}-unguarded", "guard disabled (null hook)")
        mutations.append((f"{route}-unguarded", "guard disabled", reason))
        if ROUTES[route].get("state_invisible"):
            if not state_only_classifier_accepts(rows[f"{route}-unguarded"]):
                raise ValueError(f"{route}-unguarded: the final state shows the write; the case does not test the log")
            mutations.append((f"{route}-unguarded", "state-only classifier would accept",
                              "rejected only through the event log"))
        for name, broken in falsified_rows(rows[f"{route}-armed"], f"{route}-armed"):
            mutations.append((f"{route}-armed", f"falsified:{name}", must_reject(broken, f"{route}-armed", name)))
        table[route] = dict(allowed=allowed, side_effect="PREVENTED",
                            state=STATE_PASS if verdict == PASS else STATE_BLOCKED, changed=changed, verdict=verdict,
                            control=control)
    for mutant, routes in MUTANTS.items():
        for route in routes:
            case = f"{route}-{mutant}"
            mutations.append((case, mutant, must_reject(rows[case], case, mutant)))
    return table, mutations


def all_cases():
    for route in (*REQUIRED_ROUTES, *EXTRA_ROUTES):
        for variant in ("allowed", "armed", "unguarded"):
            yield f"{route}-{variant}"
    for mutant, routes in MUTANTS.items():
        for route in routes:
            yield f"{route}-{mutant}"


def main():
    from geargrafx_reference_guard import synthetic_media
    with tempfile.TemporaryDirectory(prefix="pce-stack-access-guard-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = build_probes(directory)
        rows = {case: run_case(probe, bios, cue, case) for case in all_cases()}
    table, mutations = evaluate(rows)
    for case, row in rows.items():
        print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_EXECUTION",
                                        "core_revision": PINNED_CORE_REVISION, "bios": "SYNTHETIC",
                                        "hook": "VERIFICATION_ONLY_PATCH", "row": row}, sort_keys=True))
    for case, label, reason in mutations:
        print(f"MUTATION-DETECTED {case} [{label}]: {reason}")
    for route, result in table.items():
        print(f"ROUTE {route}: verdict={result['verdict']} side_effect={result['side_effect']} "
              f"state={result['state']} changed={','.join(result['changed'])} allowed={result['allowed']} "
              f"control={result['control']}")
    print(f"PASS issue-499 stack access pre-guard: {len(rows)} real-core runs, {len(mutations)} mutations detected, "
          f"0 undetected (pinned core {PINNED_CORE_REVISION}, synthetic BIOS/CD, verification-only patch)")
    blocked = [route for route, result in table.items() if result["verdict"] != PASS]
    print(f"BLOCKED issue-499 consistent stop: {','.join(blocked)}. The side effect is refused before it happens on "
          "every route, but the refused instruction still finishes (a refused pop yields 0, a refused JSR/BSR/BRK/IRQ "
          "entry still loads the new PC, TransferStart has already set its transfer state, TransferEnd has already "
          "cleared it), so the CPU state at the stop is not the pre-instruction state. Not a production mechanism; "
          "#459/#367/#173 stay BLOCKED; no gate changes.")
    print(f"NOTE issue-499 PASS routes: state={STATE_PASS} means every register except PC is unchanged and PC is past "
          "the refused instruction; the refused push is dropped, so a re-run would have to rewind PC to the "
          "instruction start. This is not a restored pre-instruction state.")
    print("LIMIT: test policy (MPR1 = $F8 only) lives in the probe; debugger build only (libretro build NOT_MEASURED); "
          "harness stops after the refused instruction and never resumes; refused pops return 0; the run loop still "
          "clocks the hardware (VDC/timer/PSG/CD/ADPCM) for the refused instruction's full cycle count before the stop, "
          "so device time advances (the stop snapshot is taken after that); MMIO observation is the CPU "
          "Memory::Read/Write path only (ST0/ST1/ST2 WriteFromCPU and device-originated writes are not observed); "
          "NMI not applicable on the pinned core; no reference BIOS; no hardware claim.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-499 stack access guard: {error}", file=sys.stderr)
        raise SystemExit(1) from error
