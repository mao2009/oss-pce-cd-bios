"""Real Geargrafx run of the DEV EX_JOYREP entry (slot $22, candidate $E066, Issue #44).

Each run replaces the reset code of the built DEV ROM with a guest program that:
  1. sets MPR1 = work RAM ($F8) and SP to the case value ($FF or the $06 boundary),
  2. fills a $2300..$230F RAM sentinel with i ^ $A5,
  3. presets P via PLP (I=1, D=0 in every case), X, Y and A,
  4. calls the slot once via JSR, then stores A/P(after PHP)/X/Y/SP-after at $2200..$2204.
Expectation: this project's own contract (src/overrides/slot_22.s, a plain RTS),
NOT the reference System Card EX_JOYREP. ABI stays UNVERIFIED.

Mutant that must be rejected by the same checks: the single RTS ($60) replaced by
a NOP ($EA), so the call falls through into the next override body.
Run: python3 tests/integration/geargrafx_dev_joyrep.py --rom build/<mode>/bios-dev/dev-only-not-compatible-syscard3.pce
Needs the pinned cc65 and Geargrafx core (TOOLS_DIR). Missing tools exit 3 (BLOCKED).
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
import geargrafx_dev_bcd as dev  # noqa: E402

BLOCKED_EXIT = 3
SLOT_EX_JOYREP = 0x22
STORE = 0x2200            # A/P/X/Y/SP-after stored at STORE..STORE+4
SENTINEL = 0x2300         # 16-byte RAM sentinel, must be untouched
SENTINEL_LEN = 16
RUN_STEPS = 2048          # probe maximum
WRAM_BASE = 0x2000        # work_ram_0200_hex dump index 0 = CPU $2000
P_MASK = 0xCF             # PHP sets B (bit 4) and bit 5; PLP ignores them


def sentinel_byte(i):
    return i ^ 0xA5


def guest(reset, entry, a, x, y, p, sp):
    """Reset-replacement code. Presets regs, JSRs the entry once, records results. Returns (code, halt)."""
    code = bytearray([0x78, 0xD8,                # SEI; CLD
                      0xA9, 0xF8, 0x53, 0x02,    # LDA #$F8; TAM #$02 -> MPR1 = work RAM
                      0xA2, sp, 0x9A])           # LDX #sp; TXS
    code += bytes([0xA2, 0x00,                   # LDX #0 (sentinel fill loop)
                   0x8A, 0x49, 0xA5,             # TXA; EOR #$A5
                   0x9D, SENTINEL & 0xFF, SENTINEL >> 8,  # STA SENTINEL,X
                   0xE8,                         # INX
                   0xE0, SENTINEL_LEN,           # CPX #len
                   0xD0, 0xF5])                  # BNE fill (-11)
    code += bytes([0xA9, p, 0x28,                # LDA #p; PLP (preset P, I=1/D=0 in every case)
                   0xA2, x, 0xA0, y,             # LDX #x; LDY #y
                   0xA9, a,                      # LDA #a
                   0x20, entry & 0xFF, entry >> 8,  # JSR entry
                   0x08,                         # PHP (flags after the call)
                   0x8D, STORE & 0xFF, STORE >> 8,  # STA STORE (A)
                   0x68,                         # PLA
                   0x8D, (STORE + 1) & 0xFF, (STORE + 1) >> 8,  # STA STORE+1 (P)
                   0x8E, (STORE + 2) & 0xFF, (STORE + 2) >> 8,  # STX STORE+2 (X)
                   0x8C, (STORE + 3) & 0xFF, (STORE + 3) >> 8,  # STY STORE+3 (Y)
                   0xBA,                         # TSX
                   0x8E, (STORE + 4) & 0xFF, (STORE + 4) >> 8])  # STX STORE+4 (SP after)
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])                  # BRA -2
    if reset + len(code) > 0xFFF6:
        raise ValueError("BLOCKED: guest code does not fit before the vectors")
    return bytes(code), halt


def check(label, rows, halt, a, x, y, p, sp):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    wram = bytes.fromhex(cold["work_ram_0200_hex"])

    def at(addr):
        return wram[addr - WRAM_BASE]

    got = (at(STORE), at(STORE + 1) & P_MASK, at(STORE + 2), at(STORE + 3))
    want = (a, p & P_MASK, x, y)
    if got != want:
        raise ValueError(f"{label}: A/P/X/Y observed {[f'${v:02X}' for v in got]}, "
                         f"expected {[f'${v:02X}' for v in want]}")
    if at(STORE + 4) != sp or cold["sp"] != sp:
        raise ValueError(f"{label}: stack not balanced (SP after ${at(STORE + 4):02X}, halt ${cold['sp']:02X}, "
                         f"want ${sp:02X})")
    sentinel = bytes(wram[SENTINEL - WRAM_BASE:SENTINEL - WRAM_BASE + SENTINEL_LEN])
    want_sentinel = bytes(sentinel_byte(i) for i in range(SENTINEL_LEN))
    if sentinel != want_sentinel:
        raise ValueError(f"{label}: RAM sentinel ${SENTINEL:04X} changed ({sentinel.hex()}, want {want_sentinel.hex()})")
    return {"a": a, "x": x, "y": y, "p": f"{p:02X}", "sp": f"{sp:02X}"}


def run(probe, rom_bytes, folder, cue, reset, entry, label, regs):
    """One real-core run for a register preset. Returns the recorded preset."""
    code, halt = guest(reset, entry, *regs)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=RUN_STEPS)
    return check(label, rows, halt, *regs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_EX_JOYREP)
    table = 3 * SLOT_EX_JOYREP
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $22 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    from environment import DEPENDENCIES  # noqa: E402
    evidence = {"scope": "DEV ROM real-core run of project-own EX_JOYREP, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "runs": [], "negative_cases": []}
    cases = [("regs-123456", (0x12, 0x34, 0x56, 0x34, 0xFF)),
             ("regs-zero", (0x00, 0x00, 0x00, 0x24, 0xFF)),
             ("regs-ff", (0xFF, 0xFF, 0xFF, 0xF4, 0xFF)),
             ("regs-sp06", (0xA5, 0x5A, 0x3C, 0xB4, 0x06))]
    with tempfile.TemporaryDirectory(prefix="pce-dev-joyrep-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for name, regs in cases:
            evidence["runs"].append(run(probe, data, folder, cue, reset, entry, f"joyrep-{name}", regs))
        # Mutant inside the slot body: RTS ($60) -> NOP ($EA) must fail the regs-123456 checks.
        code_at = body - 0xE000  # CPU $E000 is file offset 0 of bank 0 (as in the MA_SQRT harness)
        at = data.find(bytes([0x60]), code_at, code_at + 0x10)
        if at < 0:
            raise ValueError("mutant rts_to_nop: RTS not found in slot $22 body")
        mutant = bytearray(data)
        mutant[at] = 0xEA
        try:
            run(probe, bytes(mutant), folder, cue, reset, entry, "mut-rts-to-nop", cases[0][1])
        except ValueError as error:
            evidence["negative_cases"].append({"case": "rts_to_nop", "rejected_by": str(error)})
        else:
            raise ValueError("mutant rts_to_nop passed all checks (not detected)")
    (args.rom.parent / "geargrafx-dev-joyrep-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: EX_JOYREP slot $22: {len(cases)} runs preserve A/X/Y/P, "
          f"balance the stack (SP=$FF and $06), keep MPR1/MPR7 and the RAM sentinel; "
          f"{len(evidence['negative_cases'])} mutant rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV EX_JOYREP integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
