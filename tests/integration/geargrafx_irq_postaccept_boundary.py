#!/usr/bin/env python3
"""Issue #366 post-accept IRQ boundary proof (synthetic media; pinned debugger core).

The debugger runs exactly CLI then BRA.  The latter is a real HuC6280 instruction
whose tail calls HandleIRQ; the resulting PC is observed at the handler boundary.
Only then is its fetched ST0 opcode refused, before a third debugger step.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geargrafx_api import compile_probe
from geargrafx_reference_guard import synthetic_media

REV = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
VECTORS = {"irq2": 0xFFF6, "irq1": 0xFFF8, "tiq": 0xFFFA}

def run(probe, bios, cue, case):
    p = subprocess.run([str(probe), str(bios), str(cue), case], text=True, capture_output=True, timeout=60)
    if p.returncode:
        raise ValueError(f"{case}: probe exit {p.returncode}: {p.stderr[-300:]}")
    return json.loads(p.stderr)

def check(row, irq):
    vector = VECTORS[irq]
    if not row["accepted"] or row["observed_vector"] != vector or row["classified_vector"] != vector:
        raise ValueError("IRQ acceptance/vector classification was not proven")
    if row["pc_after_accept"] != row["handler_pc"] or row["sp_before"] != 255 or row["sp_after"] != 252:
        raise ValueError("accepted IRQ did not land at handler with a 3-byte frame")
    if row["fetch_page"] != 1 or row["fetch_mpr"] != 0xF8 or row["fetch_opcode"] != 0x03:
        raise ValueError("handler opcode was not checked through its CPU fetch mapping")
    if row["handler_steps"] != 0 or row["resume_attempts"] != 3 or row["resume_refused"] != 3:
        raise ValueError("handler executed or resume was not rejected three times")
    if not row["stack_checked"] or not row["stack_ok"] or row["stack_cpu"] != [0x21FF, 0x21FE, 0x21FD] or row["stack_physical"] != [0x1FF, 0x1FE, 0x1FD]:
        raise ValueError("IRQ stack writes were not confined to the mapped allowed stack")
    if row["stack_before"] != [0xA1, 0xB2, 0xC3] or row["stack"] != [0x3E, 0x01, 0x00]:
        raise ValueError("stack sentinels/frame values do not prove all three writes")
    if row["stack_frame_layout"] != row["stack"]:
        raise ValueError("stack frame values differ from CPU-address-ordered observations")
    if row["only_stack_writes"] is not True:
        raise ValueError("WRAM changed outside the three allowed stack bytes")
    if row["vdc_ar_before"] != row["vdc_ar_after"] or row["vdc_r_before"] != row["vdc_r_after"] or row["marker"] != 0:
        raise ValueError("handler side effect landed before refusal")

def mutation(row, irq):
    # Classifier mutations additionally prove that its mandatory evidence is not ignored.
    mutations = (("accepted", False), ("fetch_opcode", 0), ("classified_vector", 0),
                 ("handler_steps", 1), ("resume_refused", 2), ("stack_checked", False),
                 ("stack_no_writes", {"stack": [0xA1, 0xB2, 0xC3]}),
                 ("stack_one_byte_missing", {"stack": [0x3E, 0xB2, 0xC3]}),
                 ("stack_frame_order_mismatch", {"stack_frame_layout": [0x00, 0x01, 0x3E]}),
                 ("wrong_physical_offset", {"stack_physical": [0x1FF, 0x1FE, 0x1FC]}),
                 ("stack_ok_false", {"stack_ok": False}),
                 ("extra_wram_write", {"only_stack_writes": False}),
                 ("stack_sentinel_missing", {"stack_before": [0, 0, 0]}))
    for field, value in mutations:
        bad = copy.deepcopy(row)
        if isinstance(value, dict):
            bad.update(value)
        else:
            bad[field] = value
        try: check(bad, irq)
        except ValueError: continue
        raise ValueError(f"classifier accepted mutation {field}")

def main():
    with tempfile.TemporaryDirectory(prefix="pce-irq-postaccept-") as tmp:
        d = Path(tmp); bios, cue = synthetic_media(d)
        probe = compile_probe(d, "geargrafx_irq_postaccept_boundary.cpp", "irq-postaccept-boundary")
        for irq in VECTORS:
            row = run(probe, bios, cue, irq + "-armed"); check(row, irq); mutation(row, irq)
            # RED: disabled entry/opcode/recheck each lets the observable ST0 execute;
            # wrong-type and resume-allow invalidate classification; no-stack-check
            # removes required stack evidence and is rejected (classifier mutation).
            for red in ("unguarded", "no-opcode", "wrong-type", "no-handler-recheck", "resume-allow", "no-stack-check"):
                bad = run(probe, bios, cue, irq + "-" + red)
                try: check(bad, irq)
                except ValueError: pass
                else: raise ValueError(f"RED mutation accepted: {irq}-{red}")
            print("PASS %s: accepted -> handler $%04X -> opcode refused; handler instructions=0; resume=3/3" % (irq, row["handler_pc"]))
            print("EVIDENCE " + json.dumps({"case": irq, "observation_kind": "REAL_CORE_EXECUTION", "core_revision": REV, "bios": "SYNTHETIC", "row": row}, sort_keys=True))
            print("MUTATION PASS %s: stack/frame and non-stack WRAM corruptions rejected by classifier" % irq)
    print("BLOCKED libretro: this proof requires the debugger instruction-step API; no libretro execution claim.")
    return 0

if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print("FAIL issue-366 post-accept boundary: " + str(e), file=sys.stderr); raise SystemExit(1)
