"""Real Geargrafx run of MA_DIV16S (slot $42, Issue #447) through the DEV ROM entry $E0C6.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, sets the caller SP, pushes the
divisor (low, then high), sets D=1 and C=1 on purpose, preloads Y=$A5, JSRs $E0C6 with X:A = dividend, then stores
X:A, P, Y and the remainder bytes (popped from the divisor slots) and SP at $2200-$2206 and halts.
The expectation is this project's own spec (src/overrides/slot_42.s and tools/ma_div16s_model.py), NOT the reference
BIOS MA_DIV16S ABI, which stays UNVERIFIED. Every 16-bit pair is a valid input; the divide-by-zero and -32768/-1 cases are
defined by the project contract and are checked as such.
Preconditions under test: (1) S at routine entry >= $07. The two boundary cases run last at caller SP=$0B (entry S=$07).
(2) Caller T=0 at entry. The routine copies the caller's T bit into the restored P (the $2C mask at @finish), so a
caller that could raise T would get T=1 back; T=1 is therefore OUT OF CONTRACT. On the pinned core (Geargrafx
b49ae82) the T flag is consumed by the instruction that follows SET: the core samples P.T into its memory-to-memory
mode flag and clears P.T at the start of every instruction, and PHP always pushes T=0. A caller's SET before the JSR is
therefore swallowed by the JSR, so the routine's own instructions never run with T=1 and P.T is 0 wherever the guest
can observe it. The contract cases never set T; one SET-before-JSR run is a characterisation: it prints what the core
observed and asserts nothing about the routine.
Scope: 22 real-core cases (20 at caller SP=$FF, 2 at the boundary), the no-CLD mutant, and eleven arithmetic mutants
(ARITH_MUTANTS, Issue #458: each is one same-length instruction edit in the DEV ROM body, and at least one case must
reject it; the sign, V, remainder and divide-by-zero paths each have a mutant). P is compared exactly on every bit except B (bit 4): C, Z, I, D, V, N, and T (bit 5), which must be 0
because the caller T=0 and the routine restores P with PLP. B is not a stored flag: PHP always pushes B=1, so it is
ignored. Every mutant rejection records which field differed (quotient, remainder, P bit, Y or SP).
This is NOT an exhaustive test of the assembled routine; the host model is tests/host/test_ma_div16s.py.
Run: python3 tests/integration/geargrafx_dev_div16s.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
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
from ma_div16s_model import expected as model  # noqa: E402

BLOCKED_EXIT = 3
SLOT_MA_DIV16S = 0x42
# The shared DEV runner defaults to 400 instruction steps, which the 16-iteration restoring division does not fit in.
# The API probe accepts at most 2048 steps (tests/integration/geargrafx_api_probe.cpp, the count check). The guest
# halts in a BRA loop after the call, so a larger budget only observes the same halted state.
CALL_STEPS = 2000
STORE = 0x2200  # $2200 quotient lo, $2201 quotient hi, $2202 P after, $2203 Y, $2204 rem hi, $2205 rem lo, $2206 SP after
CASES = [  # (dividend, divisor) as unsigned 16-bit encodings
    (0x0000, 0x0001), (0x0064, 0x0007), (0xFF9C, 0x0007), (0x0064, 0xFFF9), (0xFF9C, 0xFFF9),
    (0x7FFF, 0x0001), (0x8000, 0x0002), (0x8000, 0xFFFF), (0x0005, 0x0000), (0xFFFB, 0x0000),
    (0x0000, 0x0000), (0xFFFF, 0x0002), (0x0007, 0xFFFD), (0x0001, 0x0001), (0xFFFF, 0xFFFF),
    (0x7FFF, 0xFFFF), (0x03E8, 0x0003), (0xFC18, 0x0003), (0x1234, 0x0100), (0x8001, 0x0100),
]
# Inputs whose BCD interpretation differs from binary (checked on the real core: each mutant case mismatches).
MUTANT_CASES = [(0xFF9C, 0xFFF9), (0xFFFF, 0xFFFF), (0x9999, 0x0001)]
# Arithmetic mutants (Issue #458). Each pattern is the machine code of the instruction(s) in src/overrides/slot_42.s
# (the comment names the source lines). The pattern must occur exactly once in the ROM; the listed byte is replaced by a
# same-length value (a branch opcode or an immediate), so the body layout does not change.
ARITH_MUTANTS = [  # (name, pattern, index of the byte to change in the pattern, new byte)
    ("a_restoring_bcc_to_bcs", bytes.fromhex("BD0221DD0A2190"), 6, 0xB0),   # lda $2102,x; cmp $210A,x; bcc @no_sub
    ("b_quotient_negate_skipped", bytes.fromhex("BD04212902F0"), 5, 0x80),  # lda $2104,x; and #$02; beq @qpos
    ("c_remainder_negate_skipped", bytes.fromhex("BD04212904F0"), 5, 0x80),  # lda $2104,x; and #$04; beq @rpos
    ("d_dividend_sign_skipped", bytes.fromhex("BD052110"), 3, 0x80),        # lda $2105,x; bpl @dividend_pos
    ("e_overflow_flag_removed", bytes.fromhex("BD042109409D0421"), 4, 0x00),  # lda $2104,x; ora #$40 -> ora #$00
    ("h_divisor_sign_skipped", bytes.fromhex("BD0A2110"), 3, 0x80),  # lda $210A,x; bpl @divisor_pos -> bra (no negate)
    ("i_divisor_sign_not_in_quotient", bytes.fromhex("BD042149029D0421"), 4, 0x00),  # lda $2104,x; eor #$02 -> eor #$00
    ("j_overflow_check_wrong_value", bytes.fromhex("BD0521C980"), 4, 0x00),  # lda $2105,x; cmp #$80 -> cmp #$00
    # Divide-by-zero mutants (the contract: divisor 0 -> quotient 0, remainder = dividend, C = 1).
    ("f_divide_by_zero_branch_inverted", bytes.fromhex("BD0A211D0B21D0"), 6, 0xF0),  # bne @divide -> beq @divide
    ("g_divide_by_zero_c_flag_cleared", bytes.fromhex("BD042109019D0421"), 4, 0x00),  # ora #$01 -> ora #$00
    ("k_divide_by_zero_remainder_hi_wrong", bytes.fromhex("BD05219D0A21"), 1, 0x06),  # lda $2105,x -> lda $2106,x
]
# What a divide-by-zero mutant must show, so that the divide-by-zero handling is really observed: the required fields
# (all of them) and a group of which at least one must appear.
DIVZERO_MUTANTS = {
    # The inverted branch sends a zero divisor into the normal division and every other divisor into the
    # divide-by-zero path, so a divide-by-zero case loses the quotient, the remainder and the C flag.
    "f_divide_by_zero_branch_inverted": ({"P.C"}, {"quotient_lo", "quotient_hi"}),
    # The cleared C flag changes nothing but the C bit of the returned P.
    "g_divide_by_zero_c_flag_cleared": ({"P.C"}, set()),
    # The remainder high is loaded from the dividend low: a divide-by-zero case with a nonzero dividend shows it.
    "k_divide_by_zero_remainder_hi_wrong": ({"remainder_hi"}, set()),
}
Y_PRESET = 0xA5
CALLER_SP = 0xFF
BOUNDARY_SP = 0x0B  # caller SP such that entry S = $0B - 4 = $07 (2 divisor pushes, then JSR pushes 2)


def body_span(data, slot):
    """ROM offsets [start, end) of the routine body for `slot`: its table JMP target up to the next table target."""
    targets = sorted({data[3 * i + 1] | data[3 * i + 2] << 8 for i in range(81) if data[3 * i] == 0x4C})
    start = data[3 * slot + 1] | data[3 * slot + 2] << 8
    end = next((t for t in targets if t > start), 0xFFF6)
    return start - 0xE000, end - 0xE000


class Mismatch(ValueError):
    """The guest ran to its halt loop but returned a value that differs from the spec.

    ``fields`` names the observed differences (quotient, remainder, P bits, Y, SP) so that a mutant rejection can be
    recorded with its reason instead of only a label.
    """

    def __init__(self, message, fields=None):
        super().__init__(message)
        self.fields = list(fields or [])


# The compared P bits. B (bit 4) is masked out on both sides (PHP always pushes it set), so it never differs.
P_BITS = (("N", 0x80), ("V", 0x40), ("T", 0x20), ("D", 0x08), ("I", 0x04), ("Z", 0x02), ("C", 0x01))


def guest(reset, entry, dividend, divisor, sp=CALLER_SP, t_entry=False):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address).
    t_entry=True is the out-of-contract characterisation: SET before the JSR. The HuC6280 has no clear-T opcode
    (SET is $F4 and $62 is CLA, not CLT), and the pinned core consumes and clears P.T at the start of every
    instruction, so the SET is swallowed by the JSR that follows it. The rest of the guest is byte-identical to the
    contract variant, so the two runs can be compared directly."""
    lo_a, hi_x = dividend & 0xFF, dividend >> 8
    d_lo, d_hi = divisor & 0xFF, divisor >> 8
    store_q = [0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200 (quotient low)
               0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8]  # STX $2201 (quotient high)
    store_p = [0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8]  # STA $2202 (P after, pulled by PLA)
    head = [0xF4] if t_entry else []   # SET: T=1 (characterisation only)
    tail = store_q + [0x08, 0x68] + store_p   # STA; STX; PHP; PLA; STA $2202
    code = bytes([0x78,                   # SEI
                  0xA9, 0xF8, 0x53, 0x02,  # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,          # LDX #sp; TXS (caller SP; divisor pushes and the JSR follow)
                  0xF8, 0x38,              # SED (caller D=1 on purpose), SEC (caller C=1)
                  0xA9, d_lo, 0x48,        # LDA #divisor low; PHA
                  0xA9, d_hi, 0x48,        # LDA #divisor high; PHA (high on top, as the contract requires)
                  0xA0, Y_PRESET,          # LDY #$A5
                  0xA9, lo_a,              # LDA #dividend low
                  0xA2, hi_x] + head + [   # LDX #dividend high
                  0x20, entry & 0xFF, entry >> 8] + tail + [  # JSR entry
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203
                  0x68, 0x8D, (STORE + 4) & 0xFF, (STORE + 4) >> 8,  # PLA (remainder high); STA $2204
                  0x68, 0x8D, (STORE + 5) & 0xFF, (STORE + 5) >> 8,  # PLA (remainder low); STA $2205
                  0xBA, 0x8E, (STORE + 6) & 0xFF, (STORE + 6) >> 8])  # TSX; STX $2206 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def expected_p(q16, c, v):
    """P after the call under the contract (caller T=0): I=1 and D=1 kept from the caller (SEI, SED); T=0 kept;
    C, V, N, Z from the contract. B (bit 4) is not compared: PHP always pushes it set."""
    return 0x04 | 0x08 | c | (v << 6) | (q16 >> 15) << 7 | (0x02 if q16 == 0 else 0)


def check(label, rows, halt, dividend, divisor, sp=CALLER_SP):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    q16, r16, c, v, n, z = model(dividend, divisor)
    observed = (s[0], s[1], s[2] & 0xEF, s[3], s[4], s[5], s[6])  # P: every bit but B (bit 4), T (bit 5) included
    want = (q16 & 0xFF, q16 >> 8, expected_p(q16, c, v) & 0xEF, Y_PRESET, r16 >> 8, r16 & 0xFF, sp)
    if observed != want:
        names = ("quotient_lo", "quotient_hi", "P", "Y", "remainder_hi", "remainder_lo", "SP")
        fields = [n for n, got, want_value in zip(names, observed, want) if got != want_value]
        if "P" in fields:
            fields.remove("P")
            fields += [f"P.{n}" for n, bit in P_BITS if (observed[2] & bit) != (want[2] & bit)]
        raise Mismatch(f"{label}: A/X/P/Y/remHI/remLO/SP observed {observed}, expected {want}", fields)


def find_one(data, pattern, label, span):
    """Offset of the single occurrence of `pattern` inside `span` (the routine body). Zero or several are a failure.

    The search is limited to the routine's own body because the DEV ROM holds several routines that share instruction
    sequences (for example the restoring-division compare in slot $42 and slot $43).
    """
    lo, hi = span
    hits = [i for i in range(lo, min(hi, len(data) - len(pattern) + 1)) if data[i:i + len(pattern)] == pattern]
    if len(hits) != 1:
        raise ValueError(f"{label}: mutant pattern found {len(hits)} times in the DEV ROM, want exactly 1")
    return hits[0]


def run_case(probe, base, folder, cue, reset, label, entry, dividend, divisor, sp=CALLER_SP):
    code, halt = guest(reset, entry, dividend, divisor, sp)  # contract variant: caller T=0
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=CALL_STEPS)
    check(label, rows, halt, dividend, divisor, sp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_DIV16S)
    table = 3 * SLOT_MA_DIV16S  # the DEV API table is 3-byte JMPs from $E000
    body = data[table + 1] | data[table + 2] << 8  # target of the JMP = the override body (api_slot_42)
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $42 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_DIV16S, NOT the reference BIOS ABI",
                "sp_meaning": "caller SP before the divisor pushes; entry SP = caller SP - 4",
                "mpr_checked": "MPR1 and MPR7 only",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "cases": [], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-div16s-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for dividend, divisor in CASES:
            run_case(probe, data, folder, cue, reset, f"div-{dividend:04x}-{divisor:04x}", entry, dividend, divisor)
            evidence["cases"].append({"dividend": dividend, "divisor": divisor, "caller_sp": CALLER_SP})
        # Precondition boundary: caller SP=$0B gives entry S=$07, the lowest value that does not wrap the 7 pushes.
        for dividend, divisor in ((0xFF9C, 0x0007), (0x8000, 0xFFFF)):
            run_case(probe, data, folder, cue, reset, f"lowsp-{dividend:04x}-{divisor:04x}", entry, dividend, divisor,
                     sp=BOUNDARY_SP)
            evidence["cases"].append({"dividend": dividend, "divisor": divisor, "caller_sp": BOUNDARY_SP})
        # Negative: the routine's CLD (second byte of the entry) replaced by NOP. With D=1 the SBC/ADC run in BCD, so
        # every mutant case must differ from the spec; a case that still matches means the D=1 check is weak.
        code_at = body - 0xE000
        if data[code_at] != 0x08 or data[code_at + 1] != 0xD8:
            raise ValueError("slot $42 body does not start with PHP; CLD")
        mutant = bytearray(data)
        mutant[code_at + 1] = 0xEA
        rejected = []
        for dividend, divisor in MUTANT_CASES:
            try:
                run_case(probe, bytes(mutant), folder, cue, reset, f"nocld-{dividend:04x}-{divisor:04x}", entry,
                         dividend, divisor)
            except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                rejected.append(error)
        if len(rejected) != len(MUTANT_CASES):
            raise ValueError("mutant without CLD matched the spec in at least one case "
                             f"(expected {len(MUTANT_CASES)} mismatches)")
        evidence["negative_cases"].append({"case": "42_without_CLD_D1", "rejected": len(rejected),
                                           "fields": sorted({f for error in rejected for f in error.fields}),
                                           "first": str(rejected[0])})
        # Issue #458: every arithmetic mutant must be rejected by at least one real-core case (value mismatch only),
        # and the rejection reason (which field differed) is recorded for every case.
        for name, pattern, index, new in ARITH_MUTANTS:
            mutant = bytearray(data)
            mutant[find_one(data, pattern, name, body_span(data, SLOT_MA_DIV16S)) + index] = new
            catching = []
            for dividend, divisor in CASES:
                try:
                    run_case(probe, bytes(mutant), folder, cue, reset, f"{name}-{dividend:04x}-{divisor:04x}", entry,
                             dividend, divisor)
                except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                    catching.append({"case": f"{dividend:04X}/{divisor:04X}", "dividend": dividend, "divisor": divisor,
                                     "fields": error.fields, "detail": str(error)})
            if not catching:
                raise ValueError(f"{name}: no test case rejects the mutant")
            fields = sorted({f for entry in catching for f in entry["fields"]})
            record = {"mutant": name, "rejected": len(catching), "fields": fields,
                      "rejected_by": [entry["case"] for entry in catching], "first": catching[0]["detail"]}
            if name in DIVZERO_MUTANTS:
                # The divide-by-zero mutants must be caught by a divide-by-zero case and the rejection must name the
                # C flag (and, for the inverted branch, a quotient field). This is what proves the divide-by-zero
                # handling (quotient 0, remainder = dividend, C=1) is actually observed.
                required, group = DIVZERO_MUTANTS[name]
                divzero = [entry for entry in catching if entry["divisor"] == 0]
                if not divzero:
                    raise ValueError(f"{name}: no divide-by-zero case rejects the mutant")
                seen = set(divzero[0]["fields"])
                if not required <= seen:
                    raise ValueError(f"{name}: the divide-by-zero rejection does not name {sorted(required - seen)}")
                if group and not group & seen:
                    raise ValueError(f"{name}: the divide-by-zero rejection does not name any of {sorted(group)}")
                record["divide_by_zero_rejection"] = {"case": divzero[0]["case"], "fields": divzero[0]["fields"]}
            evidence.setdefault("arithmetic_mutants", []).append(record)
    (args.rom.parent / "geargrafx-dev-div16s-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2  # two boundary cases at caller SP=$0B (entry S=$07)
    print(f"PASS real Geargrafx DEV ROM: MA_DIV16S slot $42 {total} cases ({len(CASES)} at caller SP=$FF, 2 at caller "
          f"SP=$0B boundary; preconditions S>=$07 and T=0 at entry); quotient, remainder, P (C/Z/I/D/V/N and T=0 exact; "
          f"B ignored), Y, SP, MPR1/MPR7 checked; a SET before the JSR is characterised only (out of contract: the core "
          f"consumes T in the JSR, so the routine is unchanged); "
          f"no-CLD mutant rejected {len(rejected)}/{len(MUTANT_CASES)}; "
          f"arithmetic mutants rejected {len(evidence['arithmetic_mutants'])}/{len(ARITH_MUTANTS)} "
          f"(including 3 divide-by-zero mutants)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV DIV16S integration {status}: {error}", file=sys.stderr)
        sys.exit(BLOCKED_EXIT if status == "BLOCKED" else 1)
