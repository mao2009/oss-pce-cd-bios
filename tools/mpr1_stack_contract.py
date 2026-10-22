"""Issue #497: MPR1 stack-bank contract for the #473 verification harness. Pure stdlib, no emulator, no ROM.

Contract (see docs/497-mpr1-stack-contract.md):
* Only MPR1 = $F8 (WRAM) is an allowed stack bank. Every other value is DENY (fail-closed).
* The reset value of MPR1 is UNDETERMINED and never trusted; the harness must set MPR1 = $F8 itself
  (a recorded direct SetMpr at instruction index 0, before the first guest instruction) and check it.
  Any recorded init at instruction index other than 0 is FAIL.
* TAM (guest instruction) and direct SetMpr (host side) are distinct paths. A direct SetMpr mid-run is DENY.
* An IRQ assert inside the same RunInstruction as an MPR1 change is UNMEASURED: the per-instruction MPR
  comparison cannot see it, so it is kept as a hole, never PASS.
* IRQ state is three-valued: True (observed asserted), False (observed not asserted) and IRQ_UNKNOWN
  (not observed / missing). An unobserved IRQ state is missing evidence; it is never read as "no IRQ"
  and it never becomes PASS.

Verdicts: PASS, FAIL, BLOCKED (no evidence), UNMEASURED (known hole). Synthetic inputs only.

Input policy (fail-closed; the same policy is written in docs/497-mpr1-stack-contract.md):
* A byte field (an MPR1 value) must be a real int in 0..255. bool is not a byte value.
* An instruction index must be a real int >= 0. bool is not an index.
* A wrong type (str, bool, None, list, float, ...) is BLOCKED: the evidence cannot be read.
* A readable number that contradicts the contract (out of the byte range, not $F8, not index 0) is FAIL.
* The measured reset value is informational only: it never changes a verdict, and an unreadable reset
  value does not turn a valid recorded init into a failure.
"""

PASS, FAIL, BLOCKED, UNMEASURED = "PASS", "FAIL", "BLOCKED", "UNMEASURED"

ALLOWED_STACK_MPR1 = frozenset({0xF8})   # WRAM bank only. Do not widen without a new contract.
# $F7 is listed as denied by fail-closed default only; its real behaviour is not verified in this repo.
KNOWN_DENIED_MPR1 = {0xFF: "I/O page (denied)", 0xF7: "not in allow table (bank type unverified here)"}
RESET_MPR1 = None                        # UNDETERMINED: reset value is not part of the contract.
HARNESS_INIT_MPR1 = 0xF8
IRQ_SAME_INSTRUCTION_STATUS = UNMEASURED

# Three-valued IRQ observation. IRQ_UNKNOWN is the explicit "not observed" value (None):
# the harness did not observe the IRQ state, so there is no evidence either way. It is never
# interchangeable with False, and it never yields PASS.
IRQ_UNKNOWN = None

# MPR1 is a one-byte register. An index is a non-negative instruction number.
MPR1_MIN, MPR1_MAX = 0x00, 0xFF
INDEX_MIN = 0

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

# Internal classification of one IRQ observation.
_IRQ_TRUE = "TRUE"
_IRQ_FALSE = "FALSE"
_IRQ_UNKNOWN_STATE = "UNKNOWN"   # not observed
_IRQ_INVALID = "INVALID"         # not an observation at all (wrong type)


def _is_byte(value):
    """True only for a real int in 0..255. bool is rejected on purpose: bool is not a byte value."""
    return type(value) is int and MPR1_MIN <= value <= MPR1_MAX


def _is_index(value):
    """True only for a real non-negative int. bool is rejected on purpose: bool is not an index."""
    return type(value) is int and value >= INDEX_MIN


def _byte_text(value):
    """Format a value for a detail message without ever raising (a str/None must not break a verdict)."""
    if type(value) is int:
        return "%02X" % value if MPR1_MIN <= value <= MPR1_MAX else str(value)
    return repr(value)


def _addr_text(value):
    """Format an address for a detail message without ever raising."""
    if type(value) is int and value >= 0:
        return "%04X" % value
    return str(value)


def _irq_state(value):
    """Classify one IRQ observation: TRUE / FALSE / UNKNOWN (not observed) / INVALID (unreadable type)."""
    if value is IRQ_UNKNOWN:
        return _IRQ_UNKNOWN_STATE
    if type(value) is bool:
        return _IRQ_TRUE if value else _IRQ_FALSE
    return _IRQ_INVALID


def check_stack_bank(mpr1):
    """Verdict for a value of MPR1 used as the stack bank. Only $F8 passes.

    A wrong type (str, bool, None, ...) is BLOCKED: the value cannot be read as MPR1 evidence.
    A readable int outside 0..255 is FAIL: it cannot be a legal MPR1 byte, so it is denied.
    """
    if type(mpr1) is not int:
        return BLOCKED, "no MPR1 evidence: %s is not an MPR1 byte value" % (repr(mpr1),)
    if not _is_byte(mpr1):
        return FAIL, "MPR1=%s is outside the byte range $%02X-$%02X" % (
            _byte_text(mpr1), MPR1_MIN, MPR1_MAX)
    if mpr1 in ALLOWED_STACK_MPR1:
        return PASS, "WRAM $F8 allowed as stack bank"
    return FAIL, "MPR1=%02X denied: %s" % (mpr1, KNOWN_DENIED_MPR1.get(mpr1, "not in allow table"))


def check_initial_state(measured_reset, recorded_init_value, instruction_index):
    """Initial state before the first guest instruction.

    The measured reset value is informational only: it never changes the verdict. A reset value that
    cannot be read (wrong type) does not turn a valid recorded init into a failure, because the reset
    value is not trusted in the first place.

    PASS requires the harness-recorded init write of $F8 made at instruction index 0 (before any guest
    instruction). Same rule as DIRECT_SETMPR.

    Verdicts: no recorded init -> BLOCKED; an unreadable init value or index -> BLOCKED; a readable
    number that breaks the contract (index other than 0, a value other than $F8) -> FAIL.
    """
    if recorded_init_value is None:
        return BLOCKED, "harness init write not recorded"
    if type(recorded_init_value) is not int:
        return BLOCKED, "harness init write is not an MPR1 byte value: %s" % (repr(recorded_init_value),)
    if type(instruction_index) is not int:
        return BLOCKED, "instruction index is not a number: %s" % (repr(instruction_index),)
    if instruction_index != 0:
        return FAIL, "harness init recorded at instruction index %s, allowed only at index 0" % (
            instruction_index,)
    if not _is_byte(recorded_init_value):
        return FAIL, "harness init MPR1=%s is outside the byte range $%02X-$%02X" % (
            _byte_text(recorded_init_value), MPR1_MIN, MPR1_MAX)
    if recorded_init_value != HARNESS_INIT_MPR1:
        return FAIL, "harness init MPR1=%02X, contract requires $F8" % recorded_init_value
    if measured_reset == HARNESS_INIT_MPR1:
        return PASS, "reset value already $F8, init still recorded"
    return PASS, "reset value not trusted (measured %s), overwritten by recorded init" % (
        "unknown" if measured_reset is None else _byte_text(measured_reset))


def check_stack_address(cpu_address):
    """Verdict for a CPU stack address. Only $2100-$21FF is the CPU stack page; $0100-$01FF is a test label."""
    if type(cpu_address) is not int:
        return BLOCKED, "no stack address evidence: %s is not an address" % (repr(cpu_address),)
    if CPU_STACK_PAGE[0] <= cpu_address <= CPU_STACK_PAGE[1]:
        return PASS, "CPU stack page $2100-$21FF"
    if TEST_LABEL_PAGE[0] <= cpu_address <= TEST_LABEL_PAGE[1]:
        return FAIL, "%s is the test label page $0100-$01FF, not the CPU stack page $2100-$21FF" % (
            _addr_text(cpu_address),)
    return FAIL, "%s outside the CPU stack page $2100-$21FF" % (_addr_text(cpu_address),)


def check_mpr1_write(path, value, instruction_index):
    """Verdict for one write to MPR1 by a given path at a given instruction index.

    An unreadable value or index is BLOCKED; a readable number that breaks the contract is FAIL.
    """
    if path not in PATHS:
        return BLOCKED, "unknown path"
    if path == PATH_RESET:
        return BLOCKED, "reset value is not a contract input; harness must set MPR1 first"
    if type(instruction_index) is not int:
        return BLOCKED, "instruction index is not a number: %s" % (repr(instruction_index),)
    if not _is_index(instruction_index):
        return FAIL, "instruction index %d is invalid: it must be a non-negative integer" % instruction_index
    if path == PATH_DIRECT_SETMPR:
        if type(value) is not int:
            return BLOCKED, "direct SetMpr value is not an MPR1 byte value: %s" % (repr(value),)
        if not _is_byte(value):
            return FAIL, "direct SetMpr value %s is outside the byte range $%02X-$%02X" % (
                _byte_text(value), MPR1_MIN, MPR1_MAX)
        if instruction_index == 0 and value == HARNESS_INIT_MPR1:
            return PASS, "harness init before first guest instruction"
        return FAIL, "direct SetMpr allowed only as init $F8 at index 0"
    # TAM: guest instruction. Checked at instruction boundary only.
    verdict, detail = check_stack_bank(value)
    return verdict, "TAM: " + detail


def judge_irq_window(mpr1_changed_in_instruction, irq_asserted_in_instruction,
                     irq_pending_at_start=IRQ_UNKNOWN):
    """IRQ condition for one instruction window. An MPR1 change in an instruction while an IRQ is
    asserted or already pending is the hole.

    Three-valued IRQ input. True = observed asserted, False = observed not asserted,
    IRQ_UNKNOWN (None) = not observed (missing evidence). Both IRQ arguments default to IRQ_UNKNOWN,
    so an unobserved state can never be silently read as "no IRQ": it is judged BLOCKED, never PASS.

    irq_pending_at_start covers an IRQ that was pending at the instruction boundary before the TAM: the
    HandleIRQ after the TAM can push to $21xx under the new MPR1, which the boundary comparison cannot see.

    Verdicts:
    * UNMEASURED - an IRQ overlap was observed (asserted or pending) together with an MPR1 change:
      known hole, the per-instruction MPR comparison cannot see the push.
    * BLOCKED - the IRQ state was not observed, or an input was not readable: no evidence.
    * PASS - the MPR1 did not change in this instruction (the push target stays the MPR1 that was
      judged at the instruction boundary), or the MPR1 changed with both IRQ observations explicitly
      negative (no new assert and no pending IRQ).
    """
    if type(mpr1_changed_in_instruction) is not bool:
        return BLOCKED, "mpr1-changed flag is not an observation: %s" % (repr(mpr1_changed_in_instruction),)
    asserted = _irq_state(irq_asserted_in_instruction)
    pending = _irq_state(irq_pending_at_start)
    if asserted == _IRQ_INVALID:
        return BLOCKED, "IRQ asserted state is not an observation: %s" % (repr(irq_asserted_in_instruction),)
    if pending == _IRQ_INVALID:
        return BLOCKED, "IRQ pending state is not an observation: %s" % (repr(irq_pending_at_start),)
    if not mpr1_changed_in_instruction:
        if asserted == _IRQ_UNKNOWN_STATE or pending == _IRQ_UNKNOWN_STATE:
            # No MPR1 change in this instruction, but the IRQ state was not observed: the missing
            # observation is not a negative observation, so this window is not certified safe.
            return BLOCKED, ("IRQ state not observed (asserted=%s, pending=%s): missing evidence, "
                             "not a negative observation" % (
                                 "UNKNOWN" if asserted == _IRQ_UNKNOWN_STATE else "observed",
                                 "UNKNOWN" if pending == _IRQ_UNKNOWN_STATE else "observed"))
        return PASS, ("no MPR1 change in this instruction; the push target stays the MPR1 judged at "
                      "the instruction boundary")
    if asserted == _IRQ_TRUE or pending == _IRQ_TRUE:
        return UNMEASURED, "IRQ push (asserted or pending at instruction start) after an MPR1 change is not observed"
    if asserted == _IRQ_UNKNOWN_STATE or pending == _IRQ_UNKNOWN_STATE:
        return BLOCKED, ("IRQ state not observed (asserted=%s, pending=%s): missing evidence, "
                         "not a negative observation" % (
                             "UNKNOWN" if asserted == _IRQ_UNKNOWN_STATE else "observed",
                             "UNKNOWN" if pending == _IRQ_UNKNOWN_STATE else "observed"))
    return PASS, "MPR1 change observed with no IRQ assert and no pending IRQ at the instruction start"
