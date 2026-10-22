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

# Stack address notation. Two different things; never mix them.
#   CPU_STACK_PAGE: the address the 65C02-style S register points into on the CPU bus ($2100-$21FF via the
#                   hardware page; the pinned core's real stack).
#   TEST_LABEL_PAGE: the $0100-$01FF label used in the synthetic test ROM / WRAM-offset records. Not a CPU address.
CPU_STACK_PAGE = (0x2100, 0x21FF)
TEST_LABEL_PAGE = (0x0100, 0x01FF)

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


def check_stack_address(cpu_address):
    """Verdict for a CPU stack address. Only $2100-$21FF is the CPU stack page; $0100-$01FF is a test label."""
    if type(cpu_address) is not int:
        return BLOCKED, "no stack address evidence"
    if CPU_STACK_PAGE[0] <= cpu_address <= CPU_STACK_PAGE[1]:
        return PASS, "CPU stack page $2100-$21FF"
    if TEST_LABEL_PAGE[0] <= cpu_address <= TEST_LABEL_PAGE[1]:
        return FAIL, "%04X is the test label page $0100-$01FF, not the CPU stack page $2100-$21FF" % cpu_address
    return FAIL, "%04X outside the CPU stack page $2100-$21FF" % cpu_address


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


def judge_irq_window(mpr1_changed_in_instruction, irq_asserted_in_instruction, irq_pending_at_start=False):
    """IRQ condition. An MPR1 change in an instruction while an IRQ is asserted or already pending is the hole.

    irq_pending_at_start covers an IRQ that was pending at the instruction boundary before the TAM: the
    HandleIRQ after the TAM can push to $21xx under the new MPR1, which the boundary comparison cannot see.
    """
    if mpr1_changed_in_instruction and (irq_asserted_in_instruction or irq_pending_at_start):
        return UNMEASURED, "IRQ push (asserted or pending at instruction start) after an MPR1 change is not observed"
    return PASS, "no MPR1 change overlapping an IRQ"
