#!/usr/bin/env python3
"""Issue #737 H4/H5 E1-E3: host-side write-before-block gate (HOST_FIXTURE, synthetic only).

E1 ST free-run opcode gate, E2 IRQ first-instruction gate, E3 stack/MPR allowlist + fail-closed
wrapper. This module is a pure-stdlib judge over synthetic dict rows: it reads no BIOS, no ROM and
no emulator source, runs no core, and never executes a genuine callee. It does not lift any safety
gate: tools/reference_jsr_gate.py stays BLOCKED, release_eligible stays false, jsr_count stays 0.
A PASS here means only that one synthetic pre-block check land saw no deny condition; it is not
ALLOWED and grants no execution. Missing/unknown evidence is BLOCKED, never PASS.

Pinned Geargrafx b49ae82a012eade566d72e9d61c9d95a5caa4da3 is the only accepted real-core
provenance, and only with bios=SYNTHETIC. GPL verification patches are never in product.

  python3 tools/h45_preblock_gate.py --check   # self-check, exits 3 (BLOCKED convention)
"""
import json
import sys

PINNED_CORE_REVISION = "b49ae82a012eade566d72e9d61c9d95a5caa4da3"
ST_OPCODES = frozenset({0x03, 0x13, 0x23})
ST_LABELS = {0x03: "ST0", 0x13: "ST1", 0x23: "ST2"}
VECTORS = {"IRQ2": 0xFFF6, "IRQ1": 0xFFF8, "TIQ": 0xFFFA}
FORBIDDEN_MMIO = frozenset({0x1802, 0x1804, 0x1402, 0x1403})
ALLOWED_MPR1 = frozenset({0xF8})
CPU_STACK_PAGE = (0x2100, 0x21FF)
IRQ_STACK_WINDOW = (0x2000, 0x4000)
APPROVED_PC_LOW = 0x1E00
APPROVED_PC_HIGH = 0x4000
BUDGET_MIN = 4
BUDGET_MAX = 1000000
PROVENANCE_KINDS = ("REAL_CORE_EXECUTION", "HOST_FIXTURE")
DENY_FLAG_KEYS = ("same_value_write", "temp_write_restore", "gg_testing", "libretro",
                  "block_move_outside_allowlist", "device_dma_touches_ram")
LIBRETRO_REASON = ("libretro build: no execute breakpoint and no PC/trace API "
                   "(GG_DISABLE_DISASSEMBLER); entry-stop not observable, not PASS")
EXIT_BLOCKED = 3

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
DANGEROUS_OPCODES = ST_OPCODES | {0x20}
HALT_REGISTER_KEYS = ("PC", "A", "X", "Y", "P", "S")


def _halt_registers_ok(halt, pc):
    if type(halt) is not dict:
        return False
    for key in HALT_REGISTER_KEYS:
        if key not in halt or not _is_int(halt[key]):
            return False
    if halt["PC"] != pc:
        return False
    if not CPU_STACK_PAGE[0] <= halt["S"] <= CPU_STACK_PAGE[1]:
        return False
    return True


class GateRefused(ValueError):
    """The H4/H5 pre-block gate did not return PASS for the synthetic row (fail closed)."""


def _is_int(value):
    return type(value) is int


def _is_byte(value):
    return _is_int(value) and 0 <= value <= 0xFF


def _is_addr(value):
    return _is_int(value) and 0 <= value <= 0xFFFF


def _int(row, key):
    value = row.get(key)
    return value if _is_int(value) else None


def judge_st_free_run(first_opcode, peek_ok, screened, executed_before_refusal, vdc_changed,
                      halt_equal=None):
    """E1: free-run ST opcode gate. Peek PC opcode before RunInstruction; first insn screened.

    peek_ok must be True (side-effect-free TryPeek); a peek failure is BLOCKED (fail closed).
    screened must be True. executed_before_refusal must be 0 when the opcode is ST, and
    vdc_changed must be False with halt_equal True for PASS. Any other type is BLOCKED.
    """
    if screened is not True:
        return BLOCKED, "first-insn opcode not screened: missing evidence, not PASS"
    if peek_ok is not True:
        return BLOCKED, "TryPeek failed / unsafe fetch: fail closed before step"
    if not _is_byte(first_opcode):
        return BLOCKED, f"first opcode not a byte: {first_opcode!r}"
    if not _is_int(executed_before_refusal) or executed_before_refusal < 0:
        return BLOCKED, f"executed_before_refusal not a non-negative int: {executed_before_refusal!r}"
    if type(vdc_changed) is not bool:
        return BLOCKED, f"vdc_changed not a bool observation: {vdc_changed!r}"
    if halt_equal is not None and type(halt_equal) is not bool:
        return BLOCKED, f"halt_equal not a bool observation: {halt_equal!r}"
    if first_opcode in ST_OPCODES:
        label = ST_LABELS[first_opcode]
        if executed_before_refusal != 0:
            return FAIL, f"{label}: executed {executed_before_refusal} before refusal, ST ran"
        if vdc_changed is not False:
            return FAIL, f"{label}: VDC side effect landed before refusal"
        if halt_equal is False:
            return FAIL, f"{label}: halt state not equal after refusal"
        if halt_equal is not True:
            return BLOCKED, f"{label}: halt equality not observed: missing evidence, not PASS"
        return PASS, f"{label} refused before side effect (0 executed, VDC/hal halt equal)"
    if vdc_changed is not False:
        return FAIL, "non-ST first insn with VDC change: side effect landed"
    if halt_equal is False:
        return FAIL, "non-ST first insn: halt state not equal"
    if halt_equal is not True:
        return BLOCKED, "halt equality not observed: missing evidence, not PASS"
    return PASS, "non-ST first opcode passed free-run screen"


def judge_irq_first_instruction(row, vector, expected_handler, build="debugger"):
    """E2: IRQ first-instruction stop. Debugger only; libretro is BLOCKED, never PASS."""
    if build == "libretro":
        return BLOCKED, LIBRETRO_REASON
    if build != "debugger":
        return BLOCKED, f"unknown build {build!r}"
    if vector not in VECTORS:
        return BLOCKED, f"unknown vector {vector!r}"
    if not isinstance(row, dict):
        return BLOCKED, "no valid observation: row is not a dict"
    if row.get("exit", 0) != 0 or row.get("status") != "ok" or row.get("mode") != "irq-entry":
        return BLOCKED, f"no valid observation (exit={row.get('exit')}, status={row.get('status')})"
    if row.get("vector") != vector:
        return BLOCKED, f"observation is for {row.get('vector')!r}, expected {vector!r}"
    if _int(row, "vector_addr") != VECTORS[vector]:
        return BLOCKED, "vector address not observed or does not match expected vector"
    stop_pc, executed = _int(row, "stop_pc"), _int(row, "handler_instructions")
    vec_lo, vec_hi = _int(row, "vector_lo"), _int(row, "vector_hi")
    if None in (stop_pc, executed, vec_lo, vec_hi):
        return BLOCKED, "stop_pc / handler_instructions / vector bytes not observed"
    if row.get("execbp_hit") is not True:
        return FAIL, "execute breakpoint at the handler was not raised"
    resolved = (vec_hi << 8) | vec_lo
    if resolved != expected_handler:
        return FAIL, f"{vector} vector resolved to {resolved:#06x}, expected {expected_handler:#06x}"
    if stop_pc != expected_handler:
        return FAIL, f"{vector} stopped at {stop_pc:#06x}, expected handler entry {expected_handler:#06x}"
    if executed != 0:
        return FAIL, f"{vector}: {executed} handler instruction(s) executed before the stop"
    handler_writes = row.get("handler_writes")
    if type(handler_writes) is not list:
        return BLOCKED, "handler writes not observed"
    if handler_writes:
        return FAIL, f"{vector}: handler body wrote memory before the stop"
    writes = row.get("stack_writes")
    if type(writes) is not list or not all(_is_int(a) for a in writes):
        return BLOCKED, "stack writes not observed"
    if len(writes) != 3:
        return FAIL, f"{vector}: {len(writes)} stack writes, expected 3"
    for address in writes:
        if not IRQ_STACK_WINDOW[0] <= address < IRQ_STACK_WINDOW[1]:
            return FAIL, f"{vector}: stack write {address:#06x} outside page 1"
    pending = row.get("pending", None)
    p_i = row.get("p_i", None)
    if type(pending) is not bool or type(p_i) is not bool:
        return BLOCKED, "IRQ pending/I not observed: missing evidence, not PASS"
    if pending is True and p_i is False:
        return FAIL, f"{vector}: IRQ pending with I=0 would be taken, not first-insn stopped"
    if "halt_equal" in row:
        if row["halt_equal"] is False:
            return FAIL, f"{vector}: halt state not equal after stop"
        if row["halt_equal"] is not True:
            return BLOCKED, f"{vector}: halt equality not observed: missing evidence, not PASS"
    return PASS, f"{vector} stopped at handler {expected_handler:#06x} with 0 instructions"


def judge_stack_mpr(mpr, mpr1_init_value, mpr1_init_index, alias_detected, irq_asserted,
                    irq_pending, stack_writes, mpr1_changed=False):
    """E3: stack/MPR pre-block. Only $F8, MPR0-6 != $FF, no alias, IRQ observed, stack in page."""
    if type(mpr) is not list or len(mpr) != 8 or not all(_is_byte(v) for v in mpr):
        return BLOCKED, f"mpr must be 8 bytes: {mpr!r}"
    for index in range(7):
        if mpr[index] == 0xFF:
            return FAIL, f"MPR{index}=$FF forbidden"
    if mpr[1] not in ALLOWED_MPR1:
        return FAIL, f"MPR1=${mpr[1]:02X} denied: only $F8 allowed"
    if type(mpr1_init_value) is not int:
        return BLOCKED, f"mpr1_init_value not an int: {mpr1_init_value!r}"
    if type(mpr1_init_index) is not int:
        return BLOCKED, f"mpr1_init_index not an int: {mpr1_init_index!r}"
    if mpr1_init_index != 0:
        return FAIL, f"harness init at index {mpr1_init_index}, allowed only at 0"
    if not _is_byte(mpr1_init_value) or mpr1_init_value != 0xF8:
        return FAIL, f"harness init MPR1=${mpr1_init_value!r}, contract requires $F8"
    if alias_detected is True:
        return FAIL, "physical-bank alias detected"
    if alias_detected is not False:
        return BLOCKED, f"alias not observed as False: {alias_detected!r}"
    if type(mpr1_changed) is not bool:
        return BLOCKED, f"mpr1_changed not a bool: {mpr1_changed!r}"
    if type(irq_asserted) is not bool or irq_asserted is None:
        return BLOCKED, "IRQ asserted not observed: missing evidence, not PASS"
    if type(irq_pending) is not bool or irq_pending is None:
        return BLOCKED, "IRQ pending not observed: missing evidence, not PASS"
    if mpr1_changed and (irq_asserted is True or irq_pending is True):
        return BLOCKED, "IRQ-in-TAM hole: MPR1 change with IRQ overlap is UNMEASURED, never PASS"
    if type(stack_writes) is not list:
        return BLOCKED, "stack_writes not observed"
    if not all(_is_int(a) for a in stack_writes):
        return BLOCKED, "stack_writes contains a non-integer"
    for address in stack_writes:
        if not CPU_STACK_PAGE[0] <= address <= CPU_STACK_PAGE[1]:
            return FAIL, f"stack write {address:#06x} outside $2100-$21FF"
    return PASS, "MPR1=$F8, MPR0-6 != $FF, no alias, IRQ observed, stack in $2100-$21FF"


def judge_allowlist(snapshot):
    """Unified E1-E3 allowlist. ALL must hold for PASS; execution stays BLOCKED either way."""
    if not isinstance(snapshot, dict):
        return BLOCKED, "snapshot is not a dict"
    prov = snapshot.get("provenance")
    if not isinstance(prov, dict) or prov.get("kind") not in PROVENANCE_KINDS:
        return BLOCKED, "provenance kind missing/unknown"
    if prov["kind"] == "REAL_CORE_EXECUTION":
        if prov.get("core_revision") != PINNED_CORE_REVISION:
            return BLOCKED, "real-core revision must be the pinned revision"
        if prov.get("bios") != "SYNTHETIC":
            return BLOCKED, "real-core observation must use a synthetic BIOS"
        if prov.get("real_core_claim") is not True:
            return BLOCKED, "real_core_claim must be true for REAL_CORE_EXECUTION"
        run_id = snapshot.get("run_id")
        if type(run_id) is not str or not run_id or run_id != prov.get("run_id"):
            return BLOCKED, "harness run-id missing or mismatched: missing evidence, not PASS"
        if not _halt_registers_ok(snapshot.get("halt_registers"), snapshot.get("pc")):
            return BLOCKED, "halt registers missing or inconsistent with snapshot PC/stack: missing evidence, not PASS"
    else:
        if prov.get("real_core_claim") is not False:
            return BLOCKED, "HOST_FIXTURE must not claim real-core evidence"
        if prov.get("core_revision") is not None:
            return BLOCKED, "HOST_FIXTURE must not carry a core revision"
    jsr_count = snapshot.get("jsr_count")
    if not _is_int(jsr_count):
        return BLOCKED, f"jsr_count not an int: {jsr_count!r}"
    if jsr_count != 0:
        return FAIL, f"jsr_count={jsr_count}: genuine callee ran, never allowed"
    if snapshot.get("execution") != "BLOCKED":
        return FAIL, f"execution must be BLOCKED, got {snapshot.get('execution')!r}"
    if snapshot.get("release_eligible") is not False:
        return FAIL, "release_eligible must be false"
    build = snapshot.get("build")
    if build == "libretro" or build == "GG_TESTING":
        return BLOCKED, LIBRETRO_REASON if build == "libretro" else "GG_TESTING build: BLOCKED"
    if build != "debugger":
        return BLOCKED, f"unknown build {build!r}"
    screened = snapshot.get("first_opcode_screened")
    verdict, detail = judge_st_free_run(
        snapshot.get("first_opcode"), snapshot.get("peek_ok"), screened,
        snapshot.get("executed_before_refusal"), snapshot.get("vdc_changed"),
        snapshot.get("halt_equal"))
    if verdict != PASS:
        return verdict, detail
    if snapshot.get("first_opcode") in ST_OPCODES:
        return BLOCKED, f"{ST_LABELS[snapshot['first_opcode']]} denied before side effect"
    seq = snapshot.get("instruction_sequence")
    if type(seq) is not list:
        return BLOCKED, "instruction sequence missing: missing evidence, not PASS"
    executed_field = snapshot.get("executed")
    if not _is_int(executed_field):
        return BLOCKED, "executed not a non-negative int"
    if len(seq) != executed_field:
        return BLOCKED, "instruction sequence length != executed: count mismatch, not PASS"
    for opcode in seq:
        if not _is_byte(opcode):
            return BLOCKED, "instruction sequence contains a non-byte"
    if seq and seq[0] != snapshot.get("first_opcode"):
        return BLOCKED, "instruction sequence first element != first_opcode"
    for opcode in seq:
        if opcode in DANGEROUS_OPCODES:
            return FAIL, f"dangerous opcode {opcode:#04x} in executed sequence (incl. 2nd+ insn)"
    mmio = snapshot.get("mmio_stores")
    if type(mmio) is not list:
        return BLOCKED, "mmio_stores not observed"
    if not all(_is_addr(a) for a in mmio):
        return BLOCKED, "mmio_stores contains a non-address"
    for address in mmio:
        if address in FORBIDDEN_MMIO:
            return BLOCKED, f"forbidden MMIO store ${address:04X}"
    verdict, detail = judge_stack_mpr(
        snapshot.get("mpr"), snapshot.get("mpr1_init_value"), snapshot.get("mpr1_init_index"),
        snapshot.get("alias_detected"), snapshot.get("irq_asserted"), snapshot.get("irq_pending"),
        snapshot.get("stack_writes"), snapshot.get("mpr1_changed", False))
    if verdict != PASS:
        return verdict, detail
    pc = snapshot.get("pc")
    approved = snapshot.get("approved_range")
    if not _is_addr(pc):
        return BLOCKED, f"pc not an address: {pc!r}"
    if approved != [APPROVED_PC_LOW, APPROVED_PC_HIGH]:
        return BLOCKED, "approved range must equal the trusted definition [0x1E00,0x4000)"
    if not approved[0] <= pc < approved[1]:
        return FAIL, f"pc {pc:#06x} outside approved range ${approved[0]:04X}..${approved[1]:04X}"
    if snapshot.get("irq_status") != "OBSERVED":
        return BLOCKED, "irq_status must be OBSERVED, UNKNOWN is never PASS"
    pending = snapshot.get("irq_pending")
    p_i = snapshot.get("p_i")
    if type(pending) is not bool or type(p_i) is not bool:
        return BLOCKED, "IRQ pending/I not observed: missing evidence, not PASS"
    if pending is True and p_i is False:
        return FAIL, "IRQ pending with I=0 would be taken"
    budget = snapshot.get("budget")
    executed = snapshot.get("executed")
    if not _is_int(budget) or not BUDGET_MIN <= budget <= BUDGET_MAX:
        return BLOCKED, f"budget not bounded 4..1000000: {budget!r}"
    if not _is_int(executed) or executed < 0:
        return BLOCKED, f"executed not a non-negative int: {executed!r}"
    if executed > budget:
        return FAIL, f"executed {executed} exceeds budget {budget}"
    rom = snapshot.get("rom_sha256")
    expected = snapshot.get("expected_sha256")
    if type(rom) is not str or type(expected) is not str:
        return BLOCKED, "rom hash missing"
    try:
        int(rom, 16)
        int(expected, 16)
    except (ValueError, TypeError):
        return BLOCKED, "rom hash not hex"
    if len(rom) != 64 or len(expected) != 64:
        return BLOCKED, "rom hash length must be 64 hex chars"
    if rom != expected:
        return BLOCKED, "ROM hash mismatch"
    deny = snapshot.get("deny_flags")
    if type(deny) is not dict:
        return BLOCKED, "deny_flags missing: missing evidence, not PASS"
    for key in DENY_FLAG_KEYS:
        if key not in deny:
            return BLOCKED, f"deny_flags missing {key}"
        if deny[key] is not False:
            return BLOCKED, f"deny flag {key} is not False"
    return PASS, "allowlisted synthetic row; execution still BLOCKED, jsr_count=0, release_eligible=false"


def require_preblock(snapshot):
    """Fail-closed wrapper: PASS is the only non-exceptional return; it never permits execution."""
    verdict, detail = judge_allowlist(snapshot)
    if verdict != PASS:
        raise GateRefused(f"BLOCKED: H4/H5 pre-block refused: {detail}")
    return {"execution": "BLOCKED", "execution_permitted": False, "jsr_count": 0,
            "release_eligible": False, "runner": "NONE", "verdict": PASS, "detail": detail}


def clean_snapshot():
    """Synthetic HOST_FIXTURE row that must PASS judge_allowlist (no deny, full evidence)."""
    rom = "a" * 64
    return {
        "provenance": {"kind": "HOST_FIXTURE", "core_revision": None, "bios": "SYNTHETIC",
                       "real_core_claim": False, "run_id": None},
        "run_id": None, "halt_registers": None,
        "instruction_sequence": [],
        "jsr_count": 0, "execution": "BLOCKED", "release_eligible": False, "build": "debugger",
        "first_opcode": 0xEA, "first_opcode_screened": True, "peek_ok": True,
        "executed_before_refusal": 0, "vdc_changed": False, "halt_equal": True,
        "mmio_stores": [], "mpr": [0x00, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        "mpr1_init_value": 0xF8, "mpr1_init_index": 0, "alias_detected": False,
        "irq_asserted": False, "irq_pending": False, "stack_writes": [],
        "mpr1_changed": False, "pc": 0x3E00, "approved_range": [APPROVED_PC_LOW, APPROVED_PC_HIGH],
        "irq_status": "OBSERVED", "p_i": True, "budget": 100, "executed": 0,
        "rom_sha256": rom, "expected_sha256": rom,
        "deny_flags": {key: False for key in DENY_FLAG_KEYS},
    }


def clean_irq_row(vector="IRQ2", handler=0x3F80):
    """Synthetic IRQ entry row that must PASS judge_irq_first_instruction."""
    return {
        "exit": 0, "status": "ok", "mode": "irq-entry", "vector": vector,
        "vector_addr": VECTORS[vector],
        "vector_lo": handler & 0xFF, "vector_hi": handler >> 8,
        "execbp_hit": True, "stop_pc": handler, "handler_instructions": 0,
        "handler_writes": [], "stack_writes": [0x21FF, 0x21FE, 0x21FD],
        "pending": False, "p_i": True, "halt_equal": True,
    }


def _self_check():
    snapshot = clean_snapshot()
    verdict, detail = judge_allowlist(snapshot)
    if verdict != PASS:
        raise ValueError(f"clean snapshot not PASS: {detail}")
    record = require_preblock(snapshot)
    if record["execution_permitted"] is not False or record["jsr_count"] != 0:
        raise ValueError("pre-block record must never permit execution")
    return "e1_st, e2_irq, e3_stack_mpr, allowlist PASS on synthetic rows; execution BLOCKED"


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run the synthetic self-check")
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("--check is required")
    try:
        summary = _self_check()
    except (ValueError, GateRefused) as error:
        print(f"BLOCKED: h45 pre-block self-check failed: {error}")
        return EXIT_BLOCKED
    print(f"BLOCKED: H4/H5 pre-block gate closed; jsr_count=0, release_eligible=false; {summary}")
    print(json.dumps({"verdict": PASS, "execution": "BLOCKED", "execution_permitted": False,
                      "jsr_count": 0, "release_eligible": False}, sort_keys=True))
    return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
