#!/usr/bin/env python3
"""Issue #366: EXECUTE-breakpoint stop at the resolved IRQ handler entry.

Synthetic media only; pinned Geargrafx debugger build. The probe arms an
EXECUTE breakpoint on the synthetic handler, runs CLI; BRA with the IRQ
asserted, and the BRA step lands PC on the handler with the breakpoint flag
raised. The monitor refuses the next step: zero handler instructions run.
The emitted row is judged by tools/irq_entry_judge (PASS/BLOCKED/FAIL).

RED: unguarded (no breakpoint), wrong-bp (breakpoint elsewhere),
resume-allow (step despite the flag) each let ST0 execute and must not PASS.
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
    execbp_hit, stop_pc, vector_lo/hi, handler_instructions are core-observed;
    handler_writes holds the observed non-stack WRAM offsets (empty when the
    full-WRAM scan found only the 3 stack bytes); stack_writes are the fixed
    CPU frame addresses whose values the probe verified.
    """
    diff = row.get("wram_diff")
    if not isinstance(diff, list) or not all(isinstance(a, int) for a in diff):
        raise ValueError(f"wram_diff not observed: {diff!r}")
    return {
        "exit": 0, "status": "ok", "mode": "irq-entry",
        "vector": NAMES[row["case"].split("-")[0]],
        "vector_addr": row["vector"],
        "vector_lo": row["vector_lo"], "vector_hi": row["vector_hi"],
        "execbp_hit": row["execbp_hit"],
        "stop_pc": row["stop_pc"],
        "handler_instructions": row["handler_instructions"],
        "handler_writes": list(diff),
        "stack_writes": [0x21FF, 0x21FE, 0x21FD],
    }


def check(row, irq):
    vector, handler = VECTORS[irq], HANDLERS[irq]
    if row["case"] != irq + "-armed":
        raise ValueError(f"not the armed run: {row['case']}")
    if row["observed_vector"] != vector or row["vector"] != vector:
        raise ValueError("IRQ acceptance/vector classification was not proven")
    if row["stop_pc"] != handler or row["handler_pc"] != handler or row["sp_after"] != 0xFC:
        raise ValueError("accepted IRQ did not stop at the handler with a 3-byte frame")
    if (row["vector_lo"], row["vector_hi"]) != (handler & 0xFF, handler >> 8):
        raise ValueError("vector bytes do not resolve to the synthetic handler")
    if row["execbp_hit"] is not True:
        raise ValueError("execute breakpoint was not raised at the handler entry")
    if row["handler_instructions"] != 0:
        raise ValueError("handler instruction executed before the stop")
    # NOTE the stacked return bytes below are the measured Geargrafx BRA
    # observation ($3E01), not a general hardware claim (see postaccept doc).
    if row["stack"] != [0x3E, 0x01, 0x00] or row["only_stack_writes"] is not True:
        raise ValueError("stack writes were not confined to the allowed page-1 frame")
    if (row["handler_write_count"] == 0) != (row["wram_diff"] == []):
        raise ValueError("wram_diff is inconsistent with handler_write_count")
    if row.get("irr") != IRR_BITS[irq]:
        raise ValueError("IRQ request bit was not observed for the asserted vector")
    if not (row["ar_before"] == row["ar_after"] and row["r_before"] == row["r_after"]
            and row["marker"] == 0 and row["handler_write_count"] == 0):
        raise ValueError("handler side effect landed before the stop")
    if row["resume_attempts"] != 3 or row["resume_refused"] != 3:
        raise ValueError("breakpoint flag was not still raised on the 3 re-checks")
    verdict, detail = judge.judge_entry(to_judge_row(row), NAMES[irq], handler)
    if verdict != judge.PASS:
        raise ValueError(f"judge did not PASS the armed row: {detail}")


def mutation(row, irq):
    vector, handler = VECTORS[irq], HANDLERS[irq]
    name = NAMES[irq]
    # Classifier-level falsifications of the probe row must not PASS.
    for field, value in (("stop_pc", handler + 3), ("execbp_hit", False),
                         ("handler_instructions", 1), ("observed_vector", 0),
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
                bad = run(probe, bios, cue, irq + "-" + red)
                # The negative REDs must show the flag NOT raised: otherwise a
                # vacuous/sticky BreakpointHit() would degrade silently.
                if red in ("unguarded", "wrong-bp") and bad["execbp_hit"] is not False:
                    raise ValueError(f"RED {irq}-{red} raised execbp_hit")
                try:
                    check(bad, irq)
                except ValueError:
                    pass
                else:
                    raise ValueError(f"RED mutation accepted: {irq}-{red}")
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
