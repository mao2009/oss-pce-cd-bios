"""Issue #497: MPR1 stack-bank contract for the #473 verification harness. Pure stdlib, no emulator, no ROM.

Contract (see docs/497-mpr1-stack-contract.md):
* Only MPR1 = $F8 (WRAM) is an allowed stack bank. Every other value is DENY (fail-closed).
* The reset value of MPR1 is UNDETERMINED and never trusted; the harness must set MPR1 = $F8 itself
  (a recorded direct SetMpr at instruction index 0, before the first guest instruction) and check it.
  Any recorded init at instruction index other than 0 is FAIL.
* TAM (guest instruction) and direct SetMpr (host side) are distinct paths. A direct SetMpr mid-run is DENY.
* An IRQ assert inside the same RunInstruction as an MPR1 change is UNMEASURED: the per-instruction MPR
  comparison cannot see it, so it is kept as a hole, never PASS.

Verdicts: PASS, FAIL, BLOCKED (no evidence), UNMEASURED (known hole). Synthetic inputs only.
"""

PASS, FAIL, BLOCKED, UNMEASURED = "PASS", "FAIL", "BLOCKED", "UNMEASURED"

ALLOWED_STACK_MPR1 = frozenset({0xF8})   # WRAM bank only. Do not widen without a new contract.
# $F7 is listed as denied by fail-closed default only; its real behaviour is not verified in this repo.
KNOWN_DENIED_MPR1 = {0xFF: "I/O page (denied)", 0xF7: "not in allow table (bank type unverified here)"}
RESET_MPR1 = None                        # UNDETERMINED: reset value is not part of the contract.
HARNESS_INIT_MPR1 = 0xF8
IRQ_SAME_INSTRUCTION_STATUS = UNMEASURED

PATH_RESET = "RESET"
PATH_TAM = "TAM"
PATH_DIRECT_SETMPR = "DIRECT_SETMPR"
PATHS = (PATH_RESET, PATH_TAM, PATH_DIRECT_SETMPR)


def check_stack_bank(mpr1):
    """Verdict for a value of MPR1 used as the stack bank. Only $F8 passes."""
    if type(mpr1) is not int:
        return BLOCKED, "no MPR1 evidence"
    if mpr1 in ALLOWED_STACK_MPR1:
        return PASS, "WRAM $F8 allowed as stack bank"
    return FAIL, "MPR1=%02X denied: %s" % (mpr1, KNOWN_DENIED_MPR1.get(mpr1, "not in allow table"))


def check_initial_state(measured_reset, recorded_init_value, instruction_index):
    """Initial state before the first guest instruction.

    The measured reset value is informational only. PASS requires the harness-recorded init write
    of $F8 made at instruction index 0 (before any guest instruction). Same rule as DIRECT_SETMPR.
    """
    if recorded_init_value is None:
        return BLOCKED, "harness init write not recorded"
    if instruction_index != 0:
        return FAIL, "harness init recorded at instruction index %s, allowed only at index 0" % instruction_index
    if recorded_init_value != HARNESS_INIT_MPR1:
        return FAIL, "harness init MPR1=%02X, contract requires $F8" % recorded_init_value
    if measured_reset == HARNESS_INIT_MPR1:
        return PASS, "reset value already $F8, init still recorded"
    return PASS, "reset value not trusted (measured %s), overwritten by recorded init" % (
        "unknown" if measured_reset is None else "%02X" % measured_reset)


def check_mpr1_write(path, value, instruction_index):
    """Verdict for one write to MPR1 by a given path at a given instruction index."""
    if path not in PATHS:
        return BLOCKED, "unknown path"
    if path == PATH_RESET:
        return BLOCKED, "reset value is not a contract input; harness must set MPR1 first"
    if path == PATH_DIRECT_SETMPR:
        if instruction_index == 0 and value == HARNESS_INIT_MPR1:
            return PASS, "harness init before first guest instruction"
        return FAIL, "direct SetMpr allowed only as init $F8 at index 0"
    # TAM: guest instruction. Checked at instruction boundary only.
    verdict, detail = check_stack_bank(value)
    return verdict, "TAM: " + detail


def judge_irq_window(mpr1_changed_in_instruction, irq_asserted_in_instruction):
    """IRQ condition. A same-instruction MPR1 change plus IRQ assert is the known hole."""
    if mpr1_changed_in_instruction and irq_asserted_in_instruction:
        return UNMEASURED, "IRQ push in the same RunInstruction as an MPR1 change is not observed"
    return PASS, "no same-instruction MPR1/IRQ overlap"
