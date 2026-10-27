"""Real Geargrafx run of MA_COS (slot $46, Issue #80) through the DEV ROM entry $E0D2.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM,
SP=$FF (or $06 for the boundary), sets D=1 and C=1 on purpose (the routine must still compute in
binary and restore the caller's flags), preloads X=$5A and Y=$A5, JSRs $E0D2 with A = angle index,
then stores A (result low), X (result high), P, Y and SP at $2200-$2205 and halts.
The expectation is this project's own spec (src/overrides/slot_46.s): Q15 cosine,
X:A = round(32767 * cos(2*pi*A/256)). It is NOT the reference System Card MA_COS ABI (UNVERIFIED).
Every angle index 0..255 is valid. The 256-angle check is the Python model
(tests/host/test_ma_cos_dev.py); this run covers a representative subset on the real core.
Run: python3 tests/integration/geargrafx_dev_cos.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
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
P_PRE_OUT = 0x2400  # P before JSR (captured before SED/SEC? actually after)
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
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address)."""
    code = bytearray([0x78])                  # SEI (probe requires first instruction SEI)
    if p_entry is None:
        code.extend([0xF8, 0x38, 0xB8])       # SED; SEC; CLV
    else:
        code.extend([0xA9, p_entry & 0xFF, 0x48, 0x28])  # LDA #P; PHA; PLP
    code.extend([0xA9, 0xF8, 0x53, 0x02,       # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,             # LDX #sp; TXS (caller SP; JSR then pushes 2 bytes)
                  0xA2, X_PRESET,             # LDX #$5A (input X is ignored by MA_COS)
                  0xA0, Y_PRESET,             # LDY #$A5
                  0xA9, a,                    # LDA #a (angle index)
                  0x48,                       # PHA (save angle)
                  0x08,                       # PHP (P before JSR, D=1,C=1)
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
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


def check(label, rows, halt, a, sp=0xFF, p_entry=None):
    cold = next(r for r in rows if r["phase"] == "cold")
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


REJECT_MARKERS = ("observed", "not balanced", "MPR changed", "did not reach")


def mutant_sites(data, body):
    """Byte offsets (file offsets) of the mutation targets in the assembled slot $46 routine.

    The table is located by its exact Q15 bytes (cos_lo then cos_hi, 65 bytes each), so the offsets
    come from the built ROM, not from the source. Each pattern must be unique, otherwise we fail."""
    lo = bytes((q15(i) & 0xFF) for i in range(65))
    hi = bytes(((q15(i) >> 8) & 0xFF) for i in range(65))
    table = data.find(lo)
    if table < 0 or data[table + 65:table + 130] != hi or data.find(lo, table + 1) >= 0:
        raise ValueError("MA_COS Q15 table not located uniquely in the built ROM")
    start = body - 0xE000
    def unique(pattern):
        hits = [i for i in range(start, table) if data[i:i + len(pattern)] == pattern]
        if len(hits) != 1:
            raise ValueError(f"pattern {pattern.hex()} found {len(hits)} times in slot $46 body")
        return hits[0]
    return {
        "table": table,
        "sign_bpl": unique(bytes([0x24, 0xF8, 0x10])) + 2,   # bit $F8 ; bpl done -> operand
        "epilogue": unique(bytes([0x7A, 0x28, 0x60])),       # ply ; plp ; rts
    }


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
    sites = mutant_sites(data, body)
    t = sites["table"]
    plan = [
        # (name, file offset, new byte, angle that must expose it)
        ("table_byte_flip_T16", t + 16, data[t + 16] ^ 0x01, 16),   # cos_lo[16] off by one LSB
        # bpl skips negation for positive quadrants; BRA always skips it. Angle 128 must be negative,
        # so only a negative input exposes this mutant.
        ("sign_branch_bpl_to_bra", sites["sign_bpl"], 0x80, 128),
        ("ply_to_plx_swap", sites["epilogue"], 0xFA, 1),            # PLY -> PLX: Y not restored
        ("plp_to_nop_removal", sites["epilogue"] + 1, 0xEA, 1),     # PLP -> NOP: stack unbalanced
    ]
    results = []
    for name, offset, new, angle in plan:
        mutant = apply_mutant(data, offset, new)
        label = f"mut-{name}"
        try:
            run_case(probe, mutant, folder, cue, reset, label, entry, angle)
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
    parser.add_argument("--full", action="store_true", help="all 256 angle indices at SP=$FF (manual sweep)")
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
        angles = list(range(256)) if args.full else CASES
        for a in angles:
            run_case(probe, data, folder, cue, reset, f"cos-{a:02x}", entry, a)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0xFF})
        # Precondition boundary: caller SP=$06 gives entry S=$04.
        for a in (0, 64, 255):
            run_case(probe, data, folder, cue, reset, f"lowsp-{a:02x}", entry, a, sp=0x06)
            evidence["cases"].append({"angle": a, "expected_q15": q15(a), "sp": 0x06})
        # P vectors use the same caller-visible convention as MA_SIN.  The original 22
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
                                      "p_entry": p_entry, "nz_check": True})
        # Mutant detection: each mutant is applied to the assembled ROM (exactly one byte, confirmed
        # before running) and must be rejected by the real-core checks above.
        mutants = run_mutants(probe, data, folder, cue, reset, entry, body, evidence)
    (args.rom.parent / "geargrafx-dev-cos-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(angles) + 3 + len(P_FLAG_CASES) + len(NZ_CASES)
    print(f"PASS real Geargrafx DEV ROM: MA_COS slot $46 {total} cases ({len(angles)} at SP=$FF, "
          f"3 at SP=$06 boundary, {len(P_FLAG_CASES)} P vectors, {len(NZ_CASES)} N/Z vectors); "
          f"the original 22 arithmetic/stack cases are retained; X:A=Q15 cosine of A, Y and SP checked; MPR1/MPR7 checked; "
          f"mutants rejected {mutants}/4 (table byte, sign branch, ply->plx, plp->nop)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV COS integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
