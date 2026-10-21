"""Real Geargrafx run of MA_MUL16U (slot $41, Issue #448) through the DEV ROM entry $E0C3.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, SP=sp, D=1, C=1, V=0 on
purpose (the routine must still multiply in binary and restore the caller's flags), Y=$A5, writes a sentinel $C3
above the caller's SP ($21(sp+1)..$21FF, the caller's stack data), pushes the four operand bytes (multiplicand lo,
hi, multiplier lo, hi), JSRs $E0C3, then pops the four product bytes into $2200-$2203 (bits 0-7 first), stores P at
$2204, Y at $2205 and SP after at $2206, and halts.
The expectation is this project's own spec (src/overrides/slot_41.s), NOT the reference System Card ABI, which
stays UNVERIFIED. Every 16-bit pair is a valid input, so there is no invalid-input error path to test. The
checked cases are edge values, carry-heavy values and a few mixed values, at two caller SP values (the boundary
$0A, i.e. entry SP $04, and $FF, i.e. entry SP $F9) plus $40.
Precondition: caller T (P bit 5) = 0 at entry. T=1 has no defined behaviour for the routine (src/overrides/slot_41.s
header), and no case runs the routine with T=1. The premise cannot be violated through a JSR on the pinned core: the
core latches P.T into its memory-to-memory flag and clears P.T at the start of every instruction, so a SET before the
JSR is swallowed by the JSR, and the routine's first instruction (PHP) is not one of the T-sensitive opcodes
(ADC/AND/EOR/ORA). The routine's ADC therefore always runs in register mode. P is compared with mask $EF: C,Z,I,D,V,N
must match and bit 5 (T) must be 0 on return; bit 4 (B) is ignored because PHP pushes it as 1 and PLP does not store
it. The T=0 on return is a constant of the pinned core (PHP pushes T=0), not a copy of the caller's T.
Two JSR T-latch checks (SET before the JSR) test only the pinned core's JSR: the JSR consumes the SET, so the routine is
entered with T=0. They assert that the product is correct, the halt is reached and the returned P has bit 5 = 0. They
do NOT test MA_MUL16U behaviour with T=1.
Side effects (Issue #456): the probe's OBS "loaded" (before the guest) and "cold" (halt loop) rows carry the FULL 8 KiB
work RAM ($2000-$3FFF, MPR1=$F8). Every changed byte must lie in the expected set, which for caller SP = s is:
  - $21(s-9)..$21(s): the stack bytes the routine and its call chain use. Entry SP is s-6. The routine's own pushes
    are PHP at s-6, PHY at s-7, PHA at s-8 and PHA at s-9 (X = s-10 after them); its operand/product bytes are
    s-3..s (X+7..X+10). The JSR return bytes (s-5, s-4) and the guest's PHP (s-4) also fall in this range.
  - $2200-$2206: the guest's result/store area.
  - $21(s+1)..$21FF: caller stack data above SP. The guest writes $C3 there first, so this set may change only
    by the sentinel itself; at the end every byte must still be $C3 (the routine did not overwrite it).
Any other changed byte is a failure (OutOfSet). MPR0 and MPR2-6 must equal their 'loaded' values; MPR1=$F8 and
MPR7=0 are absolute.
Mutants (Issue #466): five one-instruction edits of the DEV ROM body (CLD->NOP, PHP->PHA, PLP->PLA, ADC->SBC, ROR->ROL).
Every mutant keeps the instruction length and the stack effect, so the guest still reaches its halt loop and the
rejection is a value mismatch, not a harness error. Each rejection records which field differed (product/P/Y/SP) and the
mutant must name the field it is expected to break: the two arithmetic mutants must break the product, the two flag
mutants the returned P.
Negative checks (7 x 3 inputs on the real core, 21 rejections in total): the five value mutants above must produce a
value mismatch naming their required field; a guest write to $2300 must be rejected by the full diff as an out-of-set
byte; a guest overwrite of one byte of the caller's stack data above SP must be rejected by the sentinel check, because
that byte is inside the allowed set and the full diff cannot see it.
Not checked: A and X (declared clobbered); the probe reads work RAM only, so zero page, card RAM, other banks,
I/O and VDC state are not observed. The 65,536-multiplier, 12-multiplicand host model lives in
tests/host/test_ma_mul16u_dev.py.
Run: python3 tests/integration/geargrafx_dev_mul16u.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import verify_dev_rom  # noqa: E402
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as dev  # noqa: E402  (entry_address, run_probe: the shared DEV-entry runner)

BLOCKED_EXIT = 3
SLOT_MA_MUL16U = 0x41
STORE = 0x2200  # $2200-$2203 product LE, $2204 P after, $2205 Y after, $2206 SP after
SENTINEL = 0xC3  # caller stack data above SP, written by the guest before the pushes
# The sentinel loop (up to 245 iterations of 4 instructions at SP=$0A) plus the routine needs more steps than the
# dev_bcd default of 400; 2048 is the probe's maximum (geargrafx_api_probe.cpp rejects larger counts).
MAX_PROBE_STEPS = 2048
WRAM_BASE = 0x2000  # work RAM index i = CPU address $2000 + i (MPR1 = $F8)
CASES = [(0, 0), (0, 0xFFFF), (0xFFFF, 0), (1, 1), (1, 0xFFFF), (0xFFFF, 1), (0xFFFF, 0xFFFF),
         (0x8000, 2), (2, 0x8000), (0x0100, 0x0100), (0x00FF, 0x0101), (0xAAAA, 0x5555),
         (0x1234, 0x5678), (0x8000, 0x8000), (0x7FFF, 0xFFFF), (0x00FF, 0x00FF), (255, 256), (0x0F0F, 0x3C3C)]
Y_PRESET = 0xA5
SP_CASES = [0x0A, 0x40, 0xFF]  # caller SP before the 4 pushes; entry SP = caller SP - 6 (must be in $04..$F9)


def guest(reset, entry, m, q, sp, extra=b"", t_set=False):
    """Reset-replacement code at CPU address `reset`. `extra` is appended before the halt loop (mutants only).
    Returns (code, halt CPU address).

    t_set=True (JSR T-latch check only): SET (opcode $F4) right before the JSR. The pinned core has no CLT opcode;
    it latches T into the next instruction and clears P.T there (src/huc6280_inline.h RunInstruction), so the JSR
    consumes the SET and the routine starts with T=0 in P."""
    push = lambda v: [0xA9, v, 0x48]  # LDA #v; PHA
    code = bytes([0x78,                      # SEI
                  0xA9, 0xF8, 0x53, 0x02,    # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, (sp + 1) & 0xFF, 0xF0, 0x08,  # LDX #sp+1; BEQ past loop (sp=$FF: nothing above SP)
                  0xA9, SENTINEL, 0x9D, 0x00, 0x21,   # loop: LDA #$C3; STA $2100,X (caller data above SP)
                  0xE8, 0xD0, 0xF8,                   # INX; BNE loop (X wraps to 0 after $21FF)
                  0xA2, sp, 0x9A,            # LDX #sp; TXS (caller SP before the pushes)
                  0xF8, 0x38, 0xB8,          # SED (caller D=1 on purpose), SEC (C=1), CLV (V=0)
                  0xA0, Y_PRESET]            # LDY #$A5
                 + push(m & 0xFF) + push(m >> 8) + push(q & 0xFF)
                 + [0xA9, q >> 8, 0x48]      # multiplier hi last (LDA sets the N/Z that P must show at entry)
                 + ([0xF4] if t_set else [])  # SET (JSR T-latch check only; the JSR consumes it)
                 + [0x20, entry & 0xFF, entry >> 8]  # JSR entry
                 + [0x08, 0x68, 0x8D, (STORE + 4) & 0xFF, (STORE + 4) >> 8]  # PHP; PLA; STA $2204 (P after)
                 + [0x8C, (STORE + 5) & 0xFF, (STORE + 5) >> 8]              # STY $2205
                 + [0x68, 0x8D, (STORE + 3) & 0xFF, (STORE + 3) >> 8]        # PLA (bits 24-31); STA $2203
                 + [0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8]        # PLA (bits 16-23); STA $2202
                 + [0x68, 0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8]        # PLA (bits 8-15);  STA $2201
                 + [0x68, 0x8D, STORE & 0xFF, STORE >> 8]                    # PLA (bits 0-7);   STA $2200
                 + [0xBA, 0x8E, (STORE + 6) & 0xFF, (STORE + 6) >> 8]        # TSX; STX $2206 (SP after)
                 + list(extra))
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


class Mismatch(ValueError):
    """The guest ran to its halt loop but returned a value that differs from the spec.

    `fields` names the compared items that differ (product/P/Y/SP). It is a plain list of str so the
    evidence JSON stays serializable; subclasses that do not compare those items keep it empty.
    """
    fields = ()


class OutOfSet(Mismatch):
    """Work RAM changed outside the expected set, or caller stack data above SP was overwritten."""


def expected_p(q):
    """P at JSR entry: I=1 (SEI), D=1 (SED), C=1 (SEC), V=0 (CLV), N/Z from LDA #q_hi (last flag setter)."""
    return 0x04 | 0x08 | 0x01 | (0x80 if q & 0x8000 else 0) | (0x02 if (q >> 8) == 0 else 0)


def check_work_ram(label, pre, post, sp):
    """Full 8 KiB diff: pre = 'loaded' (before the guest runs), post = 'cold' (halt loop). Raises OutOfSet."""
    if len(pre) != 0x2000 or len(post) != 0x2000:
        raise ValueError(f"{label}: work RAM observation is not 8 KiB (pre {len(pre)}, post {len(post)})")
    stack = range(0x100 + sp - 9, 0x100 + sp + 1)  # $21(sp-9)..$21(sp), see the module docstring
    store = range(0x200, 0x207)  # $2200-$2206
    above_sp = range(0x100 + sp + 1, 0x200)  # caller data above SP (sentinel)
    allowed = set(stack) | set(store) | set(above_sp)
    changed = [i for i in range(0x2000) if pre[i] != post[i]]
    outside = [i for i in changed if i not in allowed]
    if outside:
        shown = ", ".join(f"${WRAM_BASE + i:04X}" for i in outside[:8])
        raise OutOfSet(f"{label}: work RAM bytes changed outside the expected set: {shown} "
                       f"({len(outside)} total)")
    lost = [i for i in above_sp if post[i] != SENTINEL]
    if lost:
        raise OutOfSet(f"{label}: caller stack data above SP overwritten at ${WRAM_BASE + lost[0]:04X} "
                       f"(value ${post[lost[0]]:02X}, want ${SENTINEL:02X})")


def check(label, rows, halt, m, q, sp):
    cold = next(r for r in rows if r["phase"] == "cold")
    loaded = next(r for r in rows if r["phase"] == "loaded")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    for index in (0, 2, 3, 4, 5, 6):
        if cold["mpr"][index] != loaded["mpr"][index]:
            raise ValueError(f"{label}: MPR{index} changed (loaded ${loaded['mpr'][index]:02X}, "
                             f"after ${cold['mpr'][index]:02X})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    product = m * q
    # Mask $EF: bit 4 (B) ignored, every other bit compared. expected_p has bit 5 (T) = 0, so T=0 is checked.
    observed = (int.from_bytes(s[0:4], "little"), s[4] & 0xEF, s[5], s[6])
    want = (product, expected_p(q), Y_PRESET, sp)
    if observed != want:
        names = ("product", "P", "Y", "SP")
        error = Mismatch(f"{label}: product/P/Y/SP observed {observed}, expected {want}")
        error.fields = [n for n, a, b in zip(names, observed, want) if a != b]  # which field the mutant changed
        raise error
    # The value check comes first on purpose: a value mutant must be rejected by the value comparison
    # (that is the field the mutant is expected to break), not by the work-RAM side-effect check.
    check_work_ram(label, bytes.fromhex(loaded["work_ram_hex"]), bytes.fromhex(cold["work_ram_hex"]), sp)


def run_case(probe, base, folder, cue, reset, label, entry, m, q, sp=0xFF, extra=b""):
    code, halt = guest(reset, entry, m, q, sp, extra)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, MAX_PROBE_STEPS)
    check(label, rows, halt, m, q, sp)


def jsr_t_latch_check(probe, base, folder, cue, reset, entry, m, q, sp=0xFF):
    """JSR T-latch check: SET before the JSR must be consumed by the JSR. Asserts the product, the halt, the stack
    balance and that the returned P has bit 5 (T) = 0. Does not exercise the routine with T=1."""
    code, halt = guest(reset, entry, m, q, sp, t_set=True)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"tlatch-{m:04x}-{q:04x}-sp{sp:02x}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, MAX_PROBE_STEPS)
    check(f"jsr-t-latch-{m:04x}-{q:04x}", rows, halt, m, q, sp)
    cold = next(r for r in rows if r["phase"] == "cold")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    if s[4] & 0x20:
        raise ValueError(f"JSR T-latch check: returned P has T set (P=${s[4]:02X}); the JSR did not consume the SET")
    return {"m": m, "q": q, "sp": sp, "halt_cpu_address": f"${halt:04X}", "product_expected": m * q,
            "product_observed": int.from_bytes(s[0:4], "little"), "p_after_raw": f"${s[4]:02X}", "t_clear": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_MUL16U)
    table = 3 * SLOT_MA_MUL16U  # the DEV API table is 3-byte JMPs from $E000; $E0C3 is this JMP
    body = data[table + 1] | data[table + 2] << 8  # its target is the override body (api_slot_41)
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $41 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_MUL16U, NOT System Card ABI compatibility",
                "sp_meaning": "caller SP before the 4 pushes; entry SP = caller SP - 6",
                "mpr_checked": "MPR1 and MPR7 absolute; MPR0 and MPR2-6 equal to the loaded values",
                "work_ram_checked": "full 8 KiB work RAM diff, loaded vs halt loop; allowed: $21(sp-9)..$21(sp), "
                                    "$2200-$2206, $21(sp+1)..$21FF must keep the guest's $C3 sentinel",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-mul16u-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for sp in SP_CASES:
            for m, q in CASES if sp == 0xFF else ((0xFFFF, 0xFFFF), (0x1234, 0x5678), (0, 0xFFFF)):
                run_case(probe, data, folder, cue, reset, f"mul-{m:04x}-{q:04x}-sp{sp:02x}", entry, m, q, sp)
                evidence["cases"].append({"m": m, "q": q, "sp": sp, "expected_product": m * q})
        # Negative mutants: exactly one instruction changed, same byte length and same stack effect, so the
        # routine's stack offsets ($2100+X+n) do not move. A NOP for PHP/PLP would shift SP by one byte and the guest
        # would never reach its halt loop (a harness error, not a value mismatch), so PHP/PLP become PHA/PLA instead.
        code_at = body - 0xE000
        if data[code_at:code_at + 2] != b"\x08\xD8":
            raise ValueError("slot $41 body does not start with PHP; CLD")
        plp_at = data.find(b"\x28\x60", code_at, code_at + 120)  # file offset of PLP; RTS
        if plp_at < 0:
            raise ValueError("slot $41 body has no PLP; RTS")
        def one(pattern, name):
            """The file offset of `pattern` inside the routine body; exactly one occurrence is required."""
            hits = [i for i in range(code_at, code_at + 120 - len(pattern) + 1) if data[i:i + len(pattern)] == pattern]
            if len(hits) != 1:
                raise ValueError(f"{name}: pattern {pattern.hex()} found {len(hits)} times in the slot $41 body, want 1")
            return hits[0]

        mutants = {
            # name: (byte patches, the field the rejection must name)
            "41_without_CLD_D1": ([(code_at + 1, 0xEA)], "product"),  # D stays 1: ADC runs in BCD, the product differs
            "41_PHP_as_PHA": ([(code_at, 0x48)], "P"),                # the P save slot gets A; product/Y/SP unchanged, P differs
            "41_PLP_as_PLA": ([(plp_at, 0x68)], "P"),                 # the caller's P is not restored (D=0 on exit), SP unchanged
            # Arithmetic mutants: they break the multiply itself, so the product check is exercised and not only the
            # flag save/restore. Both keep the instruction length and the stack effect.
            "41_ADC_as_SBC": ([(one(bytes.fromhex("7D0A21"), "41_ADC_as_SBC"), 0xFD)], "product"),  # adc $210A,x -> sbc $210A,x
            "41_ROR_as_ROL": ([(one(bytes.fromhex("7E0821"), "41_ROR_as_ROL"), 0x3E)], "product"),  # ror $2108,x -> rol $2108,x
        }
        seen_mutants = set()
        for name, (patches, required) in mutants.items():
            if name in seen_mutants:
                raise ValueError(f"{name}: the mutant is listed twice; its evidence would be added twice")
            seen_mutants.add(name)
            mutant = bytearray(data)
            for offset, value in patches:
                mutant[offset] = value
            rejected = []
            for m, q in ((0xFFFF, 0xFFFF), (0x1234, 0x5678), (0x00FF, 0x0101)):
                try:
                    run_case(probe, bytes(mutant), folder, cue, reset, f"{name}-{m:04x}-{q:04x}", entry, m, q)
                except OutOfSet as error:
                    # A value mutant must be rejected by the value comparison. A work-RAM side-effect
                    # rejection is a different failure and must not be counted as a value rejection.
                    raise ValueError(f"{name}: rejected by the work-RAM side-effect check, not by the value "
                                     f"comparison: {error}") from error
                except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                    rejected.append(error)
            if len(rejected) != 3:
                raise ValueError(f"{name}: mutant matched the spec in at least one case (expected 3 mismatches)")
            fields = sorted({f for error in rejected for f in error.fields})
            if required not in fields:
                raise ValueError(f"{name}: the rejection does not name {required} (fields: {fields})")
            # The evidence is JSON: every value must be a plain type. `first` is the text of the first
            # rejection and `fields` the JSON-compatible list of the differing items.
            evidence["negative_cases"].append({"case": name, "rejected": len(rejected), "fields": fields,
                                               "required_field": required,
                                               "inputs": [[0xFFFF, 0xFFFF], [0x1234, 0x5678], [0x00FF, 0x0101]],
                                               "rejections": [{"input": [m, q], "fields": list(error.fields),
                                                               "first": str(error)}
                                                              for (m, q), error in zip(
                                                                  ((0xFFFF, 0xFFFF), (0x1234, 0x5678),
                                                                   (0x00FF, 0x0101)), rejected)],
                                               "first": str(rejected[0])})
        # Negative 6 (Issue #456): the guest writes one byte to $2300, outside the expected work-RAM set. The full
        # work-RAM diff must reject it with OutOfSet naming $2300; any other rejection is an error.
        out_of_set = bytes([0xA9, 0x5A, 0x8D, 0x00, 0x23])  # LDA #$5A; STA $2300
        rejected = []
        for m, q in ((0xFFFF, 0xFFFF), (0x1234, 0x5678), (0x00FF, 0x0101)):
            try:
                run_case(probe, data, folder, cue, reset, f"out-of-set-2300-{m:04x}-{q:04x}", entry, m, q,
                         extra=out_of_set)
            except OutOfSet as error:
                if "$2300" not in str(error):
                    raise ValueError(f"out-of-set mutant rejected for the wrong byte: {error}") from error
                rejected.append(str(error))
        if len(rejected) != 3:
            raise ValueError("out-of-set $2300 mutant was not rejected by the work-RAM check (expected 3)")
        evidence["negative_cases"].append({"case": "guest_writes_2300_outside_expected_set", "rejected": 3,
                                           "fields": ["work_ram_outside_expected_set"], "required_field":
                                           "work_ram_outside_expected_set",
                                           "inputs": [[0xFFFF, 0xFFFF], [0x1234, 0x5678], [0x00FF, 0x0101]],
                                           "first": rejected[0]})
        # Negative 7 (Issue #456): the guest overwrites one byte of the caller's stack data above SP. That byte is
        # inside the allowed set, so the full diff cannot see it; only the sentinel check can. The rejection must be
        # the sentinel one, naming the address. It runs at caller SP=$0A because $FF has no data above SP.
        above_sp = bytes([0xA2, 0x0B, 0xA9, 0x5A, 0x9D, 0x00, 0x21])  # LDX #$0B; LDA #$5A; STA $2100,X
        rejected = []
        for m, q in ((0xFFFF, 0xFFFF), (0x1234, 0x5678), (0x00FF, 0x0101)):
            try:
                run_case(probe, data, folder, cue, reset, f"above-sp-{m:04x}-{q:04x}", entry, m, q, sp=0x0A,
                         extra=above_sp)
            except OutOfSet as error:
                if "above SP overwritten" not in str(error) or "$210B" not in str(error):
                    raise ValueError(f"above-SP mutant rejected for the wrong reason: {error}") from error
                rejected.append(str(error))
        if len(rejected) != 3:
            raise ValueError("above-SP sentinel mutant was not rejected by the sentinel check (expected 3)")
        evidence["negative_cases"].append({"case": "guest_overwrites_caller_stack_above_sp", "rejected": 3,
                                           "fields": ["caller_stack_above_sp"], "required_field":
                                           "caller_stack_above_sp",
                                           "inputs": [[0xFFFF, 0xFFFF], [0x1234, 0x5678], [0x00FF, 0x0101]],
                                           "first": rejected[0]})
        # JSR T-latch check: SET before the JSR is consumed by the JSR (not a test of the routine with T=1).
        latch = [jsr_t_latch_check(probe, data, folder, cue, reset, entry, m, q) for m, q in ((0x1234, 0x5678), (0xFFFF, 0xFFFF))]
        evidence["jsr_t_latch_checks"] = latch
        print("PASS JSR T-latch check: SET before JSR is consumed; routine entered with T=0; "
              f"{len(latch)} cases (product correct, halt reached, returned P bit 5 = 0)")
    (args.rom.parent / "geargrafx-dev-mul16u-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2 * 3  # full table at SP=$FF, plus 3 cases at each of SP=$0A and $40
    rejections = sum(c["rejected"] for c in evidence["negative_cases"])
    print(f"PASS real Geargrafx DEV ROM: MA_MUL16U slot $41 {total} cases (SP=$FF full table; SP=$0A boundary "
          f"and SP=$40 mid-range); entry SP $04..$F9 precondition; caller T=0; 32-bit product, Y, P (C,Z,I,D,V,N "
          f"with B ignored and T=0 on return), SP, MPR1/MPR7 absolute and MPR0/2-6 unchanged; full 8 KiB work RAM "
          f"diff (allowed $21(s-9)..$21(s), $2200-$2206, sentinel above SP); mutant rejections: {rejections}/21 "
          f"(CLD->NOP, PHP->PHA, PLP->PLA, ADC->SBC, ROR->ROL, out-of-set $2300 write, above-SP overwrite; "
          f"3 cases each; the reason of each value rejection is recorded)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV MUL16U integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
