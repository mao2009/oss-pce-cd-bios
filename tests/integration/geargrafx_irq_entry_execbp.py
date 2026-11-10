#!/usr/bin/env python3
"""Issue #366: EXECUTE-breakpoint stop at the resolved IRQ handler entry.

Synthetic media only; pinned Geargrafx debugger build. The probe arms an
EXECUTE breakpoint on the synthetic handler, runs CLI; BRA with the IRQ
asserted, and the BRA step lands PC on the handler with the breakpoint flag
raised. The monitor refuses the next step: zero handler instructions run.
The emitted row is judged by tools/irq_entry_judge (PASS/BLOCKED/FAIL).

RED: unguarded (no breakpoint), wrong-bp (breakpoint elsewhere),
resume-allow (flag raised, yet one step explicitly issued). verify_red()
proves each RED by its measured side effects (acceptance, breakpoint
registration, one ST0 with VDC AR $00->$07, substantive armed rejection);
a case-name-only rejection is itself a FAIL.
libretro: BLOCKED (no execute-breakpoint / PC API).

No reference BIOS, no commercial media, no ABI change.
BLOCKED_ABI stays, release_eligible=false.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402
import irq_entry_judge as judge  # noqa: E402
from environment import DEPENDENCIES  # noqa: E402

REV = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
assert REV == DEPENDENCIES["geargrafx"]["revision"], "pinned core revision drift"
VECTORS = {"irq2": 0xFFF6, "irq1": 0xFFF8, "tiq": 0xFFFA}
NAMES = {"irq2": "IRQ2", "irq1": "IRQ1", "tiq": "TIQ"}
HANDLERS = {"irq2": 0x3F80, "irq1": 0x3FA0, "tiq": 0x3FC0}
IRR_BITS = {"irq2": 0x01, "irq1": 0x02, "tiq": 0x04}
RED_VARIANTS = ("unguarded", "wrong-bp", "resume-allow")


def run(probe, bios, cue, case):
    proc = subprocess.run([str(probe), str(bios), str(cue), case],
                          text=True, capture_output=True, timeout=60)
    if proc.returncode:
        raise ValueError(f"{case}: probe exit {proc.returncode}: {proc.stderr[-300:]}")
    return json.loads(proc.stderr)


def to_judge_row(row):
    """Map a probe row onto the tools/irq_entry_judge observation shape.

    Only exit/status/mode/vector/vector_addr are adapter-constructed labels.
    execbp_hit, stop_pc, vector_lo/hi, steps_issued are core-observed (the
    judge receives steps_issued under its handler_instructions label);
    handler_writes holds the observed non-stack WRAM offsets (empty when the
    full-WRAM scan found only the 3 stack bytes); stack_writes are the fixed
    CPU frame addresses whose values the probe verified.
    """
    diff = _need(row, "wram_diff")
    if not isinstance(diff, list) or not all(isinstance(a, int) for a in diff):
        raise ValueError(f"wram_diff not observed: {diff!r}")
    return {
        "exit": 0, "status": "ok", "mode": "irq-entry",
        "vector": NAMES[_need(row, "case").split("-")[0]],
        "vector_addr": _need(row, "vector"),
        "vector_lo": _need(row, "vector_lo"), "vector_hi": _need(row, "vector_hi"),
        "execbp_hit": _need(row, "execbp_hit"),
        "stop_pc": _need(row, "stop_pc"),
        # Single monitor-issued step count, presented under the judge label.
        "handler_instructions": _need(row, "steps_issued"),
        "handler_writes": list(diff),
        "stack_writes": [0x21FF, 0x21FE, 0x21FD],
    }


def _need(row, field):
    if field not in row:
        raise ValueError(f"observation missing {field}: RED evidence dropped")
    return row[field]


def check(row, irq):
    """Armed acceptance: stop at the handler with zero handler execution.

    "steps_issued" is the monitor-issued post-accept step count (0 here by
    construction, cross-checked by pc_final); the execution evidence is
    core-observed: stop_pc==handler, breakpoint flag raised, VDC/marker/WRAM
    unchanged, IRQ stack frame present. Never PASS on the counter alone.
    Every load-bearing field goes through _need(): a dropped field FAILs
    with ValueError, never KeyError.
    """
    vector, handler = VECTORS[irq], HANDLERS[irq]
    if _need(row, "case") != irq + "-armed":
        raise ValueError(f"not the armed run: {row.get('case')}")
    if _need(row, "observed_vector") != vector or _need(row, "vector") != vector:
        raise ValueError("IRQ acceptance/vector classification was not proven")
    if _need(row, "stop_pc") != handler or _need(row, "handler_pc") != handler \
            or _need(row, "sp_after") != 0xFC:
        raise ValueError("accepted IRQ did not stop at the handler with a 3-byte frame")
    if (_need(row, "vector_lo"), _need(row, "vector_hi")) != (handler & 0xFF, handler >> 8):
        raise ValueError("vector bytes do not resolve to the synthetic handler")
    if _need(row, "execbp_hit") is not True:
        raise ValueError("execute breakpoint was not raised at the handler entry")
    if _need(row, "bp_target") != handler:
        raise ValueError("breakpoint was not armed on the handler entry")
    if row.get("handler_instructions") is not None:
        raise ValueError("legacy handler_instructions field: use steps_issued")
    if _need(row, "steps_issued") != 0 or _need(row, "pc_final") != handler:
        raise ValueError("a post-accept step was issued or PC moved off the handler")
    # NOTE the stacked return bytes below are the measured Geargrafx BRA
    # observation ($3E01), not a general hardware claim (see postaccept doc).
    if _need(row, "stack") != [0x3E, 0x01, 0x00] or _need(row, "only_stack_writes") is not True:
        raise ValueError("stack writes were not confined to the allowed page-1 frame")
    if (_need(row, "handler_write_count") == 0) != (_need(row, "wram_diff") == []):
        raise ValueError("wram_diff is inconsistent with handler_write_count")
    if _need(row, "irr") != IRR_BITS[irq]:
        raise ValueError("IRQ request bit was not observed for the asserted vector")
    if not (_need(row, "ar_before") == _need(row, "ar_after")
            and _need(row, "r_before") == _need(row, "r_after")
            and _need(row, "marker") == 0 and _need(row, "handler_write_count") == 0):
        raise ValueError("handler side effect landed before the stop")
    if _need(row, "resume_attempts") != 3 or _need(row, "resume_refused") != 3:
        raise ValueError("breakpoint flag was not still raised on the 3 re-checks")
    verdict, detail = judge.judge_entry(to_judge_row(row), NAMES[irq], handler)
    if verdict != judge.PASS:
        raise ValueError(f"judge did not PASS the armed row: {detail}")


def verify_red(row, irq, red):
    """RED observation proof: succeeds only on the measured RED side effects.

    Each RED must show IRQ acceptance at the right handler, the expected
    breakpoint (non-)registration, exactly one executed ST0 (VDC AR $00->$07
    plus PC handler->handler+2), and substantive (not case-name) rejection by
    the armed acceptance. A normally-stopped, side-effect-free, PC-changed, or
    evidence-dropped RED row FAILs here.
    """
    vector, handler = VECTORS[irq], HANDLERS[irq]
    if _need(row, "case") != irq + "-" + red:
        raise ValueError(f"not the {red} run: {row.get('case')}")
    if _need(row, "observed_vector") != vector or _need(row, "vector") != vector:
        raise ValueError(f"RED {red}: IRQ acceptance/vector resolution not observed")
    if (_need(row, "vector_lo"), _need(row, "vector_hi")) != (handler & 0xFF, handler >> 8):
        raise ValueError(f"RED {red}: vector bytes do not resolve to the handler")
    if _need(row, "stop_pc") != handler or _need(row, "sp_after") != 0xFC:
        raise ValueError(f"RED {red}: IRQ did not land on the handler with a frame")
    if _need(row, "irr") != IRR_BITS[irq]:
        raise ValueError(f"RED {red}: IRQ request bit not observed")
    # Breakpoint registration per RED: omitted / misplaced / correctly armed.
    bp = _need(row, "bp_target")
    if red == "unguarded":
        if bp != 0:
            raise ValueError("RED unguarded: a breakpoint was registered")
        if _need(row, "execbp_hit") is not False:
            raise ValueError("RED unguarded: breakpoint flag raised without a breakpoint")
    elif red == "wrong-bp":
        others = [h for k, h in HANDLERS.items() if k != irq]
        if bp == handler or bp not in others:
            raise ValueError("RED wrong-bp: misplaced breakpoint registration not observed")
        if _need(row, "execbp_hit") is not False:
            raise ValueError("RED wrong-bp: flag raised at the correct handler")
    elif red == "resume-allow":
        if bp != handler:
            raise ValueError("RED resume-allow: breakpoint not armed on the handler")
        if _need(row, "execbp_hit") is not True:
            raise ValueError("RED resume-allow: breakpoint flag not raised")
    # Exactly one ST0 #$07 executed: VDC AR transition plus 2-byte PC advance.
    # The counter alone is never evidence; both observations are required.
    # (The probe reports the monitor-issued count as steps_issued; the shared
    # judge receives it under its handler_instructions label via to_judge_row.)
    if _need(row, "steps_issued") != 1:
        raise ValueError(f"RED {red}: no single post-accept step observed")
    if _need(row, "pc_final") != handler + 2:
        raise ValueError(f"RED {red}: PC did not advance one 2-byte ST0")
    if _need(row, "ar_before") != 0 or _need(row, "ar_after") != 7:
        raise ValueError(f"RED {red}: ST0 VDC side effect $00->$07 not observed")
    if _need(row, "marker") != 0:
        raise ValueError(f"RED {red}: unexpected second-instruction store observed")
    # Substantive rejection by the armed acceptance (case label normalized so
    # the case-name gate cannot be the reason).
    armed_view = copy.deepcopy(row)
    armed_view["case"] = irq + "-armed"
    try:
        check(armed_view, irq)
    except ValueError as exc:
        if "not the armed run" in str(exc):
            raise ValueError("RED rejected only by case name: observation unchecked")
    else:
        raise ValueError(f"RED {red} was accepted by the armed judgement")


def mutation(row, irq):
    vector, handler = VECTORS[irq], HANDLERS[irq]
    name = NAMES[irq]
    # Classifier-level falsifications of the probe row must not PASS.
    for field, value in (("stop_pc", handler + 3), ("execbp_hit", False),
                         ("steps_issued", 1), ("pc_final", handler + 2),
                         ("bp_target", 0), ("ar_after", 7),
                         ("observed_vector", 0),
                         ("only_stack_writes", False), ("resume_refused", 2),
                         ("vector_lo", (handler + 1) & 0xFF),
                         ("stack", [0x3E, 0xB2, 0xC3]),
                         ("wram_diff", [0x100]), ("marker", 0x77),
                         ("irr", 0x00)):
        bad = copy.deepcopy(row)
        bad[field] = value
        # Keep count/diff consistent so the falsification targets the verdict,
        # not the consistency guard (the guard itself is covered by wram_diff).
        if field == "wram_diff":
            bad["handler_write_count"] = len(value)
        try:
            check(bad, irq)
        except ValueError:
            continue
        raise ValueError(f"classifier accepted mutation {field}")
    # The shared judge still rejects every falsified judge row (stop-removal proof).
    if judge.mutation_self_check(judge.base_row(name, handler), handler, name) != 13:
        raise ValueError("judge mutation self-check did not reject all 13")
    # libretro stays BLOCKED, never PASS.
    verdict, _ = judge.judge_entry(to_judge_row(row), name, handler, build="libretro")
    if verdict != judge.BLOCKED:
        raise ValueError("libretro run was not BLOCKED")


def main():
    with tempfile.TemporaryDirectory(prefix="pce-irq-entry-execbp-") as tmp:
        tmpdir = Path(tmp)
        bios, cue = synthetic_media(tmpdir)
        probe = compile_probe(tmpdir, "geargrafx_irq_entry_execbp.cpp", "irq-entry-execbp")
        for irq in VECTORS:
            row = run(probe, bios, cue, irq + "-armed")
            check(row, irq)
            mutation(row, irq)
            for red in RED_VARIANTS:
                verify_red(run(probe, bios, cue, irq + "-" + red), irq, red)
            print(f"PASS {irq}: execbp stop at handler ${HANDLERS[irq]:04X}, "
                  f"handler instructions=0, flag still raised on 3/3 re-checks")
            print("EVIDENCE " + json.dumps({
                "case": irq, "observation_kind": "REAL_CORE_EXECUTION",
                "core_revision": REV, "bios": "SYNTHETIC", "row": row}, sort_keys=True))
            print(f"MUTATION PASS {irq}: classifier + judge falsifications rejected")
    print("BLOCKED libretro: this proof needs the debugger execute-breakpoint API; "
          "no libretro execution claim.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"FAIL issue-366 entry execbp: {exc}", file=sys.stderr)
        raise SystemExit(1)
