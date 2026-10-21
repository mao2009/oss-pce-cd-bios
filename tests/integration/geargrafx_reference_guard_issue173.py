#!/usr/bin/env python3
"""Issue #173 real-core negatives for the in-core reference-JSR guard.

Reuses the guarded observer probe (`geargrafx_reference_jsr.cpp`) and the synthetic
assets of `geargrafx_reference_guard.py`. Synthetic BIOS and CD only; no private
reference BIOS or ROM is read.

Two classes not covered by the existing guard driver are exercised:

* ``tam-mmio``: a guest maps the Hardware Page with ``TAM`` (MPR0 = $FF) and then
  stores to $1804. The per-step MPR monitor must refuse before the store executes.
  Control: the same store, unguarded (``mmio1804``), lands ($1804 readback = $02).
* ``irq-entry``: a timer IRQ is taken while the guard is armed (guest ``CLI``). The
  monitor must refuse at the handler entry before the handler's first instruction.
  Control: ``irq-entry-masked`` (no ``CLI``) never takes the IRQ.

Verdicts use exact field values (stop reason, stop PC, executed count, MPR, device
readback, RAM diff). Exit 0 = PASS, 1 = FAIL. Use ``--evidence PATH`` to save the
raw observations as JSON lines.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402

BUDGET = 20000
TAM_STOP_PC = 0x3E04   # right after TAM, before STA
IRQ_HANDLER_PC = 0x3F80
MMIO_ADDR = 0x1804
STACK_RANGE = (0x2100, 0x2200)


def run(probe, bios, cue, mode):
    command = [str(probe), str(bios), str(cue), "0x1e", str(BUDGET), "0x2000", "0x4000",
               mode, hex(MMIO_ADDR)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        return {"mode": mode, "exit": result.returncode, "stderr": result.stderr[-400:]}
    row = json.loads(result.stderr)
    row["exit"] = 0
    return row


def check_tam_mmio(row):
    f = []
    if row.get("status") != "rejected":
        f.append(f"status={row.get('status')}")
    if row.get("reason") != "mpr_changed_during_execution[mpr0]":
        f.append(f"reason={row.get('reason')}")
    if row.get("stop_pc") != TAM_STOP_PC:
        f.append(f"stop_pc={row.get('stop_pc')}")
    if row.get("instructions_executed") != 2:
        f.append(f"instructions_executed={row.get('instructions_executed')} (want 2: LDA+TAM)")
    if row["before"]["mpr"][0] != 0x00 or row["after"]["mpr"][0] != 0xFF:
        f.append("MPR0 not 0x00->0xFF")
    if row.get("mmio_rst_before") != 0x00 or row.get("mmio_rst_after") != 0x00:
        f.append(f"$1804 changed: before={row.get('mmio_rst_before')} after={row.get('mmio_rst_after')}")
    if row.get("ram_diff") != []:
        f.append(f"ram_diff={row.get('ram_diff')}")
    return f


def check_mmio_control(row):
    f = []
    if row.get("mmio_rst_after") != 0x02:
        f.append(f"control store did not land: $1804 after={row.get('mmio_rst_after')} (want 2)")
    return f


def check_irq(row):
    f = []
    if row.get("status") != "rejected":
        f.append(f"status={row.get('status')}")
    if row.get("reason") != "irq_entry_refused_at_handler":
        f.append(f"reason={row.get('reason')}")
    if row.get("stop_pc") != IRQ_HANDLER_PC:
        f.append(f"stop_pc={row.get('stop_pc')} (want handler entry {IRQ_HANDLER_PC:#x})")
    if row.get("irq_vector") != IRQ_HANDLER_PC:
        f.append(f"irq_vector={row.get('irq_vector')}")
    if row["after"]["a"] != 0:
        f.append(f"handler LDA executed: A={row['after']['a']}")
    if row["before"]["mpr"] != row["after"]["mpr"]:
        f.append("MPR changed during IRQ-guard run")
    diff = row.get("ram_diff", [])
    if not diff:
        f.append("IRQ entry produced no stack push (IRQ not actually taken)")
    if any(not (STACK_RANGE[0] <= d["address"] < STACK_RANGE[1]) for d in diff):
        f.append(f"non-stack RAM changed before refusal: {diff}")
    return f


def check_irq_masked(row):
    f = []
    if row.get("reason") != "irq_not_taken_within_budget":
        f.append(f"control reason={row.get('reason')}")
    if row.get("ram_diff") != []:
        f.append(f"control ram_diff={row.get('ram_diff')}")
    return f


def main():
    evidence = None
    if "--evidence" in sys.argv:
        evidence = Path(sys.argv[sys.argv.index("--evidence") + 1])
    with tempfile.TemporaryDirectory(prefix="pce-reference-guard-i173-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_reference_jsr.cpp",
                              target_name="reference-guard-probe-issue173")
        rows = {m: run(probe, bios, cue, m)
                for m in ("tam-mmio", "mmio1804", "irq-entry", "irq-entry-masked")}
        failures = []
        for mode, row in rows.items():
            if row["exit"] != 0:
                failures.append(f"{mode}: probe exit {row['exit']}: {row.get('stderr', '')}")
        if not failures:
            failures += [f"tam-mmio: {x}" for x in check_tam_mmio(rows["tam-mmio"])]
            failures += [f"control mmio1804: {x}" for x in check_mmio_control(rows["mmio1804"])]
            failures += [f"irq-entry: {x}" for x in check_irq(rows["irq-entry"])]
            failures += [f"control irq-entry-masked: {x}" for x in check_irq_masked(rows["irq-entry-masked"])]
        if evidence:
            evidence.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows.values()),
                                encoding="utf-8")
    if failures:
        for x in failures:
            print("FAIL " + x, file=sys.stderr)
        return 1
    print("PASS issue-173 real-core negatives: dynamic TAM->Hardware Page store refused before "
          "side effect ($1804 stayed 0, control store landed); timer IRQ taken under guard refused "
          "at handler entry (no handler instruction executed, only stack push); masked control "
          "never takes IRQ")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        print(f"FAIL/BLOCKED issue-173 guard negatives: {error}", file=sys.stderr)
        raise SystemExit(1) from error
