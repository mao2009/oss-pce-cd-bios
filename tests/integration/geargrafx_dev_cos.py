"""Real Geargrafx run of MA_COS (slot $46, Issue #80) through the DEV ROM entry $E0D2.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM,
SP=$FF (or $06 for the boundary), sets D=1 and C=1 on purpose (the routine must still compute in
binary and restore the caller's flags), preloads X=$5A and Y=$A5, JSRs $E0D2 with A = angle index,
then stores A (result low), X (result high), P, Y and SP at $2200-$2205 and halts.
The expectation is this project's own spec (src/overrides/slot_46.s): Q15 cosine,
X:A = round(32767 * cos(2*pi*A/256)). It is NOT the reference System Card MA_COS ABI (UNVERIFIED).
Every angle index 0..255 is valid. The default run (CI via make dev-integration) uses --full: all 256
angles at SP=$FF on the real core, plus the SP=$06 boundary, P and N/Z vectors and 4 mutants.
Run: python3 tests/integration/geargrafx_dev_cos.py --full --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (make setup, TOOLS_DIR on Linux). Missing tools exit 3 (BLOCKED).
"""
import hashlib
import json
import math
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
SLOT_MA_COS = 0x46
STORE = 0x2200  # $2200 A(result lo), $2201 X(result hi), $2202 P after JSR, $2203 Y, $2205 SP after
P_PRE_OUT = 0x2400  # P before JSR (captured after the guest sets its P)
# Angle indices covering each quadrant, quadrant boundaries and the wrap point.
CASES = [0, 1, 31, 32, 33, 63, 64, 65, 96, 127, 128, 129, 160, 191, 192, 193, 224, 254, 255]
Y_PRESET = 0xA5
X_PRESET = 0x5A
# Same usable effective-P vector scheme as MA_SIN: N/Z at entry come from LDA #angle;
# C/I/D/V are installed with PLP. T/B (bits 5/4) are core-specific and not asserted.
P_FLAG_CASES = [0x00, 0x01, 0x04, 0x08, 0x40, 0x80, 0x0D, 0x05, 0x0C, 0x41, 0x81, 0x4D]
NZ_CASES = [(0x00, 0x0D), (0x80, 0x0D), (0x01, 0x0D), (0x80, 0x4D), (0x00, 0x41)]


def q15(k):
    return math.floor(32767 * math.cos(2 * math.pi * k / 256) + 0.5)


def guest(reset, entry, a, sp=0xFF, p_entry=None):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address).

    Order matters: every MPR (MPR1 = stack page, MPR0 and MPR2-6 = work RAM $F8) and SP are set
    before the first PHA/PLP, so the caller P is never read from an unmapped stack. MPR7 stays the
    CPU reset mapping (bank 0). No test depends on the power-on MPR or register values.
    """
    code = bytearray([0x78])                  # SEI (probe requires first instruction SEI)
    for mpr in (1, 0, 2, 3, 4, 5, 6):
        code.extend([0xA9, 0xF8, 0x53, 1 << mpr])  # LDA #$F8; TAM #(1<<mpr): work RAM
    code.extend([0xA2, sp, 0x9A])             # LDX #sp; TXS (caller SP; JSR then pushes 2 bytes)
    if p_entry is None:
        code.extend([0xF8, 0x38, 0xB8])       # SED; SEC; CLV
    else:
        code.extend([0xA9, p_entry & 0xFF, 0x48, 0x28])  # LDA #P; PHA; PLP: caller P = p_entry
    code.extend([0xA2, X_PRESET,             # LDX #$5A (input X is ignored by MA_COS)
                  0xA0, Y_PRESET,             # LDY #$A5
                  0xA9, a,                    # LDA #a (angle index)
                  0x48,                       # PHA (save angle)
                  0x08,                       # PHP (P before JSR)
                  0x68,                       # PLA
                  0x8D, P_PRE_OUT & 0xFF, P_PRE_OUT >> 8,  # STA $2400 (P before JSR)
                  0x68,                       # PLA (restore angle)
                  0x20, entry & 0xFF, entry >> 8,  # JSR entry
                  0x8D, STORE & 0xFF, STORE >> 8,              # STA $2200 (result lo; no P effect)
                  0x08, 0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # PHP;PLA;STA $2202 (P after JSR)
                  0x8E, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STX $2201 (result hi)
                  0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY $2203
                  0xBA, 0x8E, (STORE + 5) & 0xFF, (STORE + 5) >> 8])  # TSX; STX $2205 (SP after)
    halt = reset + len(code)
    return bytes(code) + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, a, sp=0xFF, p_entry=None):
    cold = next((r for r in rows if r["phase"] == "cold"), None)
    if cold is None:
        raise ValueError(f"{label}: no cold-phase row from the probe")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:6]
    lo, hi, p_post, y, _, sp_after = s[0], s[1], s[2], s[3], s[4], s[5]
    wram_hex = cold.get("work_ram_hex")
    if not wram_hex:
        raise ValueError(f"{label}: missing full work RAM dump")
    p_pre = bytes.fromhex(wram_hex)[P_PRE_OUT - 0x2000]
    want = q15(a) & 0xFFFF
    observed = (lo | (hi << 8), y, sp_after)
    if observed != (want, Y_PRESET, sp):
        raise ValueError(f"{label}: angle ${a:02X}: X:A/Y/SP observed {observed}, "
                         f"expected {(want, Y_PRESET, sp)}")
    # Observability precondition: the caller's D (and I) must be the requested value at entry, or a
    # CLD mutant cannot change the result and a PASS would prove nothing.
    if p_entry is not None and (p_pre & 0x0C) != (p_entry & 0x0C):
        raise ValueError(f"{label}: precondition failed: entry D/I is {p_pre & 0x0C:#x}, requested {p_entry & 0x0C:#x}")
    # P preservation: effective bits only (mask 0xCF); T/B core-specific.
    P_MASK = 0xCF
    if (p_pre & P_MASK) != (p_post & P_MASK):
        raise ValueError(f"{label}: P not preserved (pre ${p_pre:02X}, post ${p_post:02X}, mask ${P_MASK:02X})")


def run_case(probe, base, folder, cue, reset, label, entry, a, sp=0xFF, count=2048, p_entry=None):
    code, halt = guest(reset, entry, a, sp, p_entry)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=count)
    check(label, rows, halt, a, sp, p_entry)


REJECT_MARKERS = ("observed", "not balanced", "MPR changed", "did not reach", "P not preserved")


def wrapper_sites(data, body):
    """File offsets inside the assembled slot $46 wrapper, found by its exact byte sequence:
    php; cld; clc; adc #64; plp; jmp api_slot_45  (08 D8 18 69 40 28 4C). Must be unique in the body."""
    start = body - 0xE000
    pattern = bytes([0x08, 0xD8, 0x18, 0x69, 0x40, 0x28, 0x4C])
    hits = [i for i in range(start, start + 0x40) if data[i:i + len(pattern)] == pattern]
    if len(hits) != 1:
        raise ValueError(f"slot $46 wrapper not located uniquely in its body ({len(hits)} hits)")
    p = hits[0]
    return {"cld": p + 1, "adc_operand": p + 4, "plp": p + 5}


def sine_table_sites(data):
    """Offset of the shared quarter-wave table (slot $45 sin_lo, then sin_hi), located by exact Q15 bytes."""
    lo = bytes((q15_sin(i) & 0xFF) for i in range(65))
    hi = bytes(((q15_sin(i) >> 8) & 0xFF) for i in range(65))
    table = data.find(lo)
    if table < 0 or data[table + 65:table + 130] != hi or data.find(lo, table + 1) >= 0:
        raise ValueError("shared sine table not located uniquely in the built ROM")
    return table


def q15_sin(i):
    """Independent oracle: round(32767 * sin(2*pi*i/256)), i.e. the sine table entries T[i] = sin(pi/2 * i/64)."""
    return math.floor(32767 * math.sin(2 * math.pi * i / 256) + 0.5)


def apply_mutant(data, offset, new):
    """Return a copy of the ROM with exactly one byte changed; assert the change is applied."""
    variant = bytearray(data)
    old = variant[offset]
    variant[offset] = new
    diffs = [i for i in range(len(data)) if variant[i] != data[i]]
    if diffs != [offset] or old == new:
        raise ValueError(f"mutant at ${offset:05X} not applied as one byte change")
    return bytes(variant)


def run_mutants(probe, data, folder, cue, reset, entry, body, evidence):
    wrap = wrapper_sites(data, body)
    t = sine_table_sites(data)
    plan = [
        # (name, file offset, new byte, angle that must expose it, caller P or None)
        ("phase_shift_adc_63", wrap["adc_operand"], 0x3F, 1, None),           # cos(a) = sin(a+63): off by one step
        ("plp_to_nop", wrap["plp"], 0xEA, 1, None),                           # P not restored / stack unbalanced
        ("cld_to_nop_decimal_caller", wrap["cld"], 0xEA, 0x99, 0x08),         # caller D=1: BCD add gives a wrong angle
        ("shared_sine_table_byte", t + 16, data[t + 16] ^ 0x01, 208, None),   # a=208 -> index (208+64)&$FF=16 in slot $45's table
    ]
    # Baseline: the unmutated build must PASS the exact case the D-mutant needs (caller D=1 at entry).
    run_case(probe, data, folder, cue, reset, "baseline-cld-caller-d1", entry, 0x99, p_entry=0x08)
    results = []
    for name, offset, new, angle, p_entry in plan:
        if name == "cld_to_nop_decimal_caller":
            assert p_entry & 0x08, "the CLD mutant is only observable with caller D=1"
        mutant = apply_mutant(data, offset, new)
        label = f"mut-{name}"
        try:
            run_case(probe, mutant, folder, cue, reset, label, entry, angle, p_entry=p_entry)
        except ValueError as error:
            message = str(error)
            if not any(marker in message for marker in REJECT_MARKERS):
                raise ValueError(f"{name}: rejected for an unexpected reason: {message}") from error
            results.append({"mutant": name, "offset": offset, "angle": angle, "applied": True,
                            "rejected": True, "reason": message})
            print(f"MUTANT {name}: applied at ${offset:05X}, REJECTED ({message})")
        else:
            raise ValueError(f"mutant {name} was NOT rejected (real-core check too weak)")
    evidence["negative_cases"] = results
    return len(results)


def main():
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="all 256 angle indices at SP=$FF (make dev-integration uses this)")
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_COS)
    table = 3 * SLOT_MA_COS  # the DEV API table is 3-byte JMPs from $E000; $E0D2 is this JMP
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $46 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    evidence = {"scope": "DEV ROM real-core run of project-own MA_COS, NOT System Card ABI compatibility",
                "contract": "A = angle index (256 per turn); X:A = round(32767*cos(2*pi*A/256)) signed Q15",
                "precondition": "caller SP >= $06 (entry S >= $04)",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "geargrafx_revision": None,
                "cases": []}
    from environment import DEPENDENCIES  # noqa: E402
    evidence["geargrafx_revision"] = DEPENDENCIES["geargrafx"]["revision"]
    with tempfile.TemporaryDirectory(prefix="pce-dev-cos-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        angles = list(range(256)) if args.full else CASES  # make dev-integration passes --full
        for a in angles:
            run_case(probe, data, folder, cue, reset, f"cos-{a:02x}", entry, a)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0xFF})
        # Precondition boundary: caller SP=$06 gives entry S=$04.
        for a in (0, 64, 255):
            run_case(probe, data, folder, cue, reset, f"lowsp-{a:02x}", entry, a, sp=0x06)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0x06})
        # P vectors use the same caller-visible convention as MA_SIN.  The 19
        # arithmetic/stack cases above remain unchanged; these add effective-P coverage.
        for p_entry in P_FLAG_CASES:
            run_case(probe, data, folder, cue, reset, f"pflag-{p_entry:02x}", entry, 1,
                     p_entry=p_entry)
            evidence["cases"].append({"angle": 1, "expected_q15": q15(1), "sp": 0xFF,
                                      "p_entry": p_entry})
        for a, p_entry in NZ_CASES:
            run_case(probe, data, folder, cue, reset, f"nz-a{a:02x}-p{p_entry:02x}", entry, a,
                     p_entry=p_entry)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0xFF,
                                      "p_entry": p_entry})
        # Mutant detection: each mutant is applied to the assembled ROM (exactly one byte, confirmed
        # before running) and must be rejected by the real-core checks above.
        mutants = run_mutants(probe, data, folder, cue, reset, entry, body, evidence)
    (args.rom.parent / "geargrafx-dev-cos-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(angles) + 3 + len(P_FLAG_CASES) + len(NZ_CASES)
    print(f"PASS real Geargrafx DEV ROM: MA_COS slot $46 {total} cases ({len(angles)} at SP=$FF, "
          f"3 at SP=$06 boundary, {len(P_FLAG_CASES)} P vectors, {len(NZ_CASES)} N/Z vectors); "
          f"the 19 arithmetic/stack cases are unchanged; X:A=Q15 cosine of A, Y and SP checked; MPR1/MPR7 checked; "
          f"mutants rejected {mutants}/4 (phase shift, plp->nop, caller-D cld->nop, shared sine table byte)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV COS integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
