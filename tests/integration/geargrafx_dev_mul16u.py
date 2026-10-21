"""Real Geargrafx run of MA_MUL16U (slot $41, Issue #448) through the DEV ROM entry $E0C3.

A guest program replaces the reset code of the built DEV ROM. It sets MPR1 to work RAM, SP=sp, D=1, C=1, V=0 on
purpose (the routine must still multiply in binary and restore the caller's flags), Y=$A5, pushes the four operand
bytes (multiplicand lo, hi, multiplier lo, hi), JSRs $E0C3, then pops the four product bytes into $2200-$2203
(bits 0-7 first), stores P at $2204, Y at $2205 and SP after at $2206, and halts.
The expectation is this project's own spec (src/overrides/slot_41.s), NOT the reference System Card ABI, which
stays UNVERIFIED. Every 16-bit pair is a valid input, so there is no invalid-input error path to test. The
checked cases are edge values, carry-heavy values and a few mixed values, at two caller SP values (the boundary
$0A, i.e. entry SP $04, and $FF, i.e. entry SP $F9).
Precondition: caller T (P bit 5) = 0 at entry. T=1 is out of contract (src/overrides/slot_41.s header). Every
checked case runs with T=0. P is compared with mask $EF: C,Z,I,D,V,N must match and bit 5 (T) must be 0 on
return; bit 4 (B) is ignored because PHP pushes it as 1 and PLP does not store it. One T=1 characterisation
run (SET before the JSR) records what the pinned core returns and asserts no PASS, because T=1 is out of contract.
Not checked: A and X (declared clobbered), MPR0 and MPR2-6 (MPR1 and MPR7 only), other RAM.
The 65,536-multiplier, 12-multiplicand host model lives in tests/host/test_ma_mul16u_dev.py.
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
CASES = [(0, 0), (0, 0xFFFF), (0xFFFF, 0), (1, 1), (1, 0xFFFF), (0xFFFF, 1), (0xFFFF, 0xFFFF),
         (0x8000, 2), (2, 0x8000), (0x0100, 0x0100), (0x00FF, 0x0101), (0xAAAA, 0x5555),
         (0x1234, 0x5678), (0x8000, 0x8000), (0x7FFF, 0xFFFF), (0x00FF, 0x00FF), (255, 256), (0x0F0F, 0x3C3C)]
Y_PRESET = 0xA5
SP_CASES = [0x0A, 0x40, 0xFF]  # caller SP before the 4 pushes; entry SP = caller SP - 6 (must be in $04..$F9)


def guest(reset, entry, m, q, sp, t_set=False):
    """Reset-replacement code at CPU address `reset`. Returns (code, halt CPU address).

    t_set=True (characterisation only): SET (opcode $F4) right before the JSR. The pinned core has no CLT opcode;
    it latches T into the next instruction and clears P.T there (src/huc6280_inline.h RunInstruction), so the JSR
    consumes the SET and the routine starts with T=0 in P."""
    push = lambda v: [0xA9, v, 0x48]  # LDA #v; PHA
    code = bytes([0x78,                      # SEI
                  0xA9, 0xF8, 0x53, 0x02,    # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                  0xA2, sp, 0x9A,            # LDX #sp; TXS (caller SP before the pushes)
                  0xF8, 0x38, 0xB8,          # SED (caller D=1 on purpose), SEC (C=1), CLV (V=0)
                  0xA0, Y_PRESET]            # LDY #$A5
                 + push(m & 0xFF) + push(m >> 8) + push(q & 0xFF)
                 + [0xA9, q >> 8, 0x48]      # multiplier hi last (LDA sets the N/Z that P must show at entry)
                 + ([0xF4] if t_set else [])  # SET (T=1 requested; characterisation only)
                 + [0x20, entry & 0xFF, entry >> 8]  # JSR entry
                 + [0x08, 0x68, 0x8D, (STORE + 4) & 0xFF, (STORE + 4) >> 8]  # PHP; PLA; STA $2204 (P after)
                 + [0x8C, (STORE + 5) & 0xFF, (STORE + 5) >> 8]              # STY $2205
                 + [0x68, 0x8D, (STORE + 3) & 0xFF, (STORE + 3) >> 8]        # PLA (bits 24-31); STA $2203
                 + [0x68, 0x8D, (STORE + 2) & 0xFF, (STORE + 2) >> 8]        # PLA (bits 16-23); STA $2202
                 + [0x68, 0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8]        # PLA (bits 8-15);  STA $2201
                 + [0x68, 0x8D, STORE & 0xFF, STORE >> 8]                    # PLA (bits 0-7);   STA $2200
                 + [0xBA, 0x8E, (STORE + 6) & 0xFF, (STORE + 6) >> 8])       # TSX; STX $2206 (SP after)
    halt = reset + len(code)
    return code + bytes([0x80, 0xFE]), halt  # BRA -2


class Mismatch(ValueError):
    """The guest ran to its halt loop but returned a value that differs from the spec."""


def expected_p(q):
    """P at JSR entry: I=1 (SEI), D=1 (SED), C=1 (SEC), V=0 (CLV), N/Z from LDA #q_hi (last flag setter)."""
    return 0x04 | 0x08 | 0x01 | (0x80 if q & 0x8000 else 0) | (0x02 if (q >> 8) == 0 else 0)


def check(label, rows, halt, m, q, sp):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP ${cold['sp']:02X}, want ${sp:02X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    product = m * q
    # Mask $EF: bit 4 (B) ignored, every other bit compared. expected_p has bit 5 (T) = 0, so T=0 is checked.
    observed = (int.from_bytes(s[0:4], "little"), s[4] & 0xEF, s[5], s[6])
    want = (product, expected_p(q), Y_PRESET, sp)
    if observed != want:
        raise Mismatch(f"{label}: product/P/Y/SP observed {observed}, expected {want}")


def run_case(probe, base, folder, cue, reset, label, entry, m, q, sp=0xFF):
    code, halt = guest(reset, entry, m, q, sp)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue)
    check(label, rows, halt, m, q, sp)


def characterise_t1(probe, base, folder, cue, reset, entry, m, q, sp=0xFF):
    """T=1 characterisation (out of contract). Records the observed values and asserts NOTHING about PASS."""
    code, halt = guest(reset, entry, m, q, sp, t_set=True)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"t1-{m:04x}-{q:04x}-sp{sp:02x}.pce"
    rom.write_bytes(variant)
    cold = next(r for r in dev.run_probe(probe, rom.resolve(), cue) if r["phase"] == "cold")
    s = bytes.fromhex(cold["work_ram_0200_hex"])[:7]
    return {"m": m, "q": q, "sp": sp, "halt_cpu_address": f"${halt:04X}", "pc_observed": f"${cold['pc']:04X}",
            "halt_reached": cold["pc"] == halt, "sp_after": cold["sp"],
            "product_observed": int.from_bytes(s[0:4], "little"), "product_expected": m * q,
            "p_after_raw": f"${s[4]:02X}", "y_after": f"${s[5]:02X}", "sp_stored": s[6]}


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
                "mpr_checked": "MPR1 and MPR7 only",
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
        # Negative 1: the routine's CLD replaced by NOP. With D=1 the ADCs run in BCD, so every mutant case must
        # differ from the binary product (a value mismatch is a rejection).
        code_at = body - 0xE000
        if data[code_at] != 0x08 or data[code_at + 1] != 0xD8:
            raise ValueError("slot $41 body does not start with PHP; CLD")
        mutants = {"41_without_CLD_D1": [(code_at + 1, 0xEA)]}
        # Negative 2: PHP and the PLP before RTS replaced by NOP. The caller's P is then not restored (D stays 0).
        plp_at = data.find(b"\x28\x60", code_at, code_at + 120)  # file offset of PLP; RTS
        if plp_at < 0:
            raise ValueError("slot $41 body has no PLP; RTS")
        mutants["41_without_PHP_PLP"] = [(code_at, 0xEA), (plp_at, 0xEA)]
        for name, patches in mutants.items():
            mutant = bytearray(data)
            for offset, value in patches:
                mutant[offset] = value
            rejected = []
            for m, q in ((0xFFFF, 0xFFFF), (0x1234, 0x5678), (0x00FF, 0x0101)):
                try:
                    run_case(probe, bytes(mutant), folder, cue, reset, f"{name}-{m:04x}-{q:04x}", entry, m, q)
                except Mismatch as error:  # only a value mismatch counts; infrastructure errors must propagate
                    rejected.append(str(error))
            if len(rejected) != 3:
                raise ValueError(f"{name}: mutant matched the spec in at least one case (expected 3 mismatches)")
            evidence["negative_cases"].append({"case": name, "rejected": len(rejected), "first": rejected[0]})
        # T=1 characterisation (out of contract): recorded only, no assertion on the values.
        t1 = [characterise_t1(probe, data, folder, cue, reset, entry, m, q) for m, q in ((0x1234, 0x5678), (0xFFFF, 0xFFFF))]
        evidence["t1_characterisation_out_of_contract"] = t1
        for row in t1:
            print("CHARACTERISATION T=1 (out of contract, no PASS asserted): " + json.dumps(row, sort_keys=True))
    (args.rom.parent / "geargrafx-dev-mul16u-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2 * 3  # full table at SP=$FF, plus 3 cases at each of SP=$0A and $40
    print(f"PASS real Geargrafx DEV ROM: MA_MUL16U slot $41 {total} cases (SP=$FF full table; SP=$0A boundary "
          f"and SP=$40 mid-range); entry SP $04..$F9 precondition; caller T=0; 32-bit product, Y, P (C,Z,I,D,V,N "
          f"with B ignored and T=0 on return), SP, MPR1/MPR7 checked; mutant rejections: "
          f"{sum(c['rejected'] for c in evidence['negative_cases'])}/6 (no-CLD and no-PHP/PLP, 3 cases each)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV MUL16U integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
