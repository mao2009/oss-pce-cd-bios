"""Real Geargrafx run of the DEV EX_RND entry (slot $3E, candidate $E0BA, Issue #72).

Each run replaces the reset code of the built DEV ROM with a guest program that:
  1. sets MPR1 = work RAM ($F8) and clears the fail counter ($2703),
  2. either zeroes the state ($2500/$2501, "zero" run) or seeds with X:A, Y=1 ("seed" run),
  3. calls next COUNT times, storing each A at $2400+i, and checks X=$5A and Y=0 are preserved,
  4. sets SP=$06 (caller SP) and calls next once more, storing SP and A after.
The probe caps one run at 2048 instructions, so coverage is split into several short runs.
Expectation: this project's own contract (src/overrides/slot_3E.s, docs/ex-rnd-internal.md)
and the host model tools/ex_rnd.py, NOT the reference System Card EX_RND. ABI stays UNVERIFIED.

Mutants that must be rejected by the same checks: taps $B4 -> $B5 and the seed high-byte
store (STX $2501) replaced by NOPs.
Run: python3 tests/integration/geargrafx_dev_ex_rnd.py --rom build/<mode>/bios-dev/dev-only-not-compatible-syscard3.pce
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
import ex_rnd  # noqa: E402  (project-own host model: the expectation source)
import geargrafx_api as api  # noqa: E402
import geargrafx_dev_bcd as dev  # noqa: E402  (entry_address, run_probe: the shared DEV-entry runner)

BLOCKED_EXIT = 3
SLOT_EX_RND = 0x3E
COUNT = 10               # next calls per run (probe cap is 2048 instructions per run)
OUT = 0x2400             # outputs: OUT + i
FAILS = 0x2703           # INC on any X/Y not preserved
STACK_SP = 0x2701        # SP after the SP=$06 call
STACK_A = 0x2702         # A output of that call
RUN_STEPS = 2048         # probe maximum
WRAM_BASE = 0x2000       # dump index 0 = CPU $2000


def loop_block(entry):
    """next x COUNT: store A at OUT+i; check X=$5A and Y=0 preserved (fail counter on mismatch)."""
    blk = bytearray([0xA9, 0x00, 0x8D, 0x00, 0x26])            # LDA #0; STA $2600 (counter)
    top = len(blk)
    blk += bytes([0xA2, 0x5A, 0xA0, 0x00, 0x20, entry & 0xFF, entry >> 8])  # LDX #$5A; LDY #0; JSR
    blk += bytes([0xE0, 0x5A, 0xF0, 0x03, 0xEE, FAILS & 0xFF, FAILS >> 8])  # CPX #$5A; BEQ +3; INC fail
    blk += bytes([0xC0, 0x00, 0xF0, 0x03, 0xEE, FAILS & 0xFF, FAILS >> 8])  # CPY #0; BEQ +3; INC fail
    blk += bytes([0xAE, 0x00, 0x26])                            # LDX $2600
    blk += bytes([0x9D, OUT & 0xFF, OUT >> 8])                  # STA OUT,X
    blk += bytes([0xEE, 0x00, 0x26])                            # INC $2600
    blk += bytes([0xAD, 0x00, 0x26, 0xC9, COUNT])               # LDA $2600; CMP #COUNT
    blk += bytes([0xD0, (top - (len(blk) + 2)) & 0xFF])         # BNE top
    return bytes(blk)


def seed_block(entry, x, a):
    """seed X:A with Y=1; check X and Y are preserved (fail counter on mismatch)."""
    return bytes([0xA2, x, 0xA9, a, 0xA0, 0x01, 0x20, entry & 0xFF, entry >> 8,
                  0xE0, x, 0xF0, 0x03, 0xEE, FAILS & 0xFF, FAILS >> 8,
                  0xC0, 0x01, 0xF0, 0x03, 0xEE, FAILS & 0xFF, FAILS >> 8])


def guest(reset, entry, seed=None):
    """Reset-replacement code. seed=None: zero the state RAM instead of seeding. Returns (code, halt)."""
    code = bytearray([0x78, 0xD8,                # SEI; CLD
                      0xA9, 0xF8, 0x53, 0x02,    # LDA #$F8; TAM #$02 -> MPR1 = work RAM ($2000-$3FFF)
                      0x9C, 0x03, 0x27])         # STZ $2703 (fail counter)
    if seed is None:
        code += bytes([0x9C, 0x00, 0x25, 0x9C, 0x01, 0x25])   # STZ $2500; STZ $2501 (zero state)
    else:
        code += seed_block(entry, seed >> 8, seed & 0xFF)
    code += loop_block(entry)
    code += bytes([0xA2, 0x06, 0x9A,             # LDX #$06; TXS (caller SP = $06)
                   0xA2, 0x5A, 0xA0, 0x00, 0xA9, 0x00,  # LDX #$5A; LDY #0; LDA #0
                   0x20, entry & 0xFF, entry >> 8,       # JSR entry
                   0xBA, 0x8E, STACK_SP & 0xFF, STACK_SP >> 8,   # TSX; STX SP-after
                   0x8D, STACK_A & 0xFF, STACK_A >> 8])          # STA output
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])                  # BRA -2
    if reset + len(code) > 0xFFF6:
        raise ValueError("BLOCKED: guest code does not fit before the vectors")
    return bytes(code), halt


def expected(seed):
    """Host model results for a run. Zero-state RAM behaves as seed 0 (which the model maps to 1)."""
    stream = ex_rnd.rnd_sequence(seed, COUNT + 1)
    state = seed
    for _ in range(COUNT + 1):
        _, state = ex_rnd.rnd_next(state)
    return stream[:COUNT], stream[COUNT], state


def check(label, rows, halt, seed):
    cold = next(r for r in rows if r["phase"] == "cold")
    if cold["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach its halt loop (pc ${cold['pc']:04X}, want ${halt:04X})")
    if cold["mpr"][1] != 0xF8 or cold["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={cold['mpr']})")
    wram = bytes.fromhex(cold["work_ram_hex"])

    def at(addr):
        return wram[addr - WRAM_BASE]

    want_seq, want_stack, want_state = expected(seed)
    got_seq = list(wram[OUT - WRAM_BASE:OUT - WRAM_BASE + COUNT])
    if got_seq != want_seq:
        raise ValueError(f"{label}: sequence mismatch (got {got_seq[:4]}..., want {want_seq[:4]}...)")
    if at(FAILS):
        raise ValueError(f"{label}: X or Y not preserved ({at(FAILS)} call(s))")
    if at(STACK_SP) != 0x06 or cold["sp"] != 0x06:
        raise ValueError(f"{label}: stack not balanced (SP after ${at(STACK_SP):02X}, halt ${cold['sp']:02X})")
    if at(STACK_A) != want_stack:
        raise ValueError(f"{label}: SP=$06 call output ${at(STACK_A):02X}, want ${want_stack:02X}")
    got_state = at(0x2500) | at(0x2501) << 8
    if got_state != want_state:
        raise ValueError(f"{label}: state RAM $2501/$2500 = ${at(0x2501):02X}{at(0x2500):02X}, "
                         f"want ${want_state:04X}")
    return {"seed": seed, "sequence": got_seq, "stack_out": at(STACK_A), "sp_after": at(STACK_SP),
            "state_final": f"{got_state:04X}", "fail_counter": at(FAILS)}


def run(probe, rom_bytes, folder, cue, reset, entry, label, seed, model_seed=None):
    """One real-core run. seed=None zeroes the state RAM; model_seed is the host-model expectation."""
    code, halt = guest(reset, entry, seed)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue, count=RUN_STEPS)
    return check(label, rows, halt, seed if model_seed is None else model_seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_EX_RND)
    table = 3 * SLOT_EX_RND
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $3E table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    from environment import DEPENDENCIES  # noqa: E402
    evidence = {"scope": "DEV ROM real-core run of project-own EX_RND, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(),
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "count_per_run": COUNT, "runs": [], "negative_cases": []}
    runs = [("zero-ram", None), ("seed-0000", 0x0000), ("seed-1234", 0x1234), ("seed-a5c3", 0xA5C3)]
    with tempfile.TemporaryDirectory(prefix="pce-dev-exrnd-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        for name, seed in runs:
            model_seed = 0 if seed is None else seed  # zero RAM == seed 0 by contract
            evidence["runs"].append(run(probe, data, folder, cue, reset, entry, f"exrnd-{name}", seed,
                                        model_seed))
        # Mutants inside the slot body: each must fail the seed-1234 run's checks.
        taps, seed_hi = bytes([0x49, 0xB4]), bytes([0x8E, 0x01, 0x25])  # EOR #$B4; STX $2501
        for name, pattern, patch in (("taps_B4_to_B5", taps, bytes([0x49, 0xB5])),
                                     ("seed_hi_store_nopped", seed_hi, bytes([0xEA, 0xEA, 0xEA]))):
            code_at = body - 0xE000  # CPU $E000 is file offset 0 of bank 0 (as in the MA_SQRT harness)
            at = data.find(pattern, code_at, code_at + 0x60)
            if at < 0:
                raise ValueError(f"mutant {name}: pattern not found in slot $3E body")
            mutant = bytearray(data)
            mutant[at:at + len(patch)] = patch
            try:
                run(probe, bytes(mutant), folder, cue, reset, entry, f"mut-{name}", 0x1234)
            except ValueError as error:
                if "sequence mismatch" not in str(error) and "state RAM" not in str(error):
                    raise  # only the data mismatch counts as a rejection (a crash is not a detection)
                evidence["negative_cases"].append({"case": name, "rejected_by": str(error)})
            else:
                raise ValueError(f"mutant {name} passed all checks (not detected)")
    (args.rom.parent / "geargrafx-dev-ex-rnd-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: EX_RND slot $3E: {len(runs)} runs x {COUNT} next calls match the "
          f"host model (zero RAM, seed $0000, $1234, $A5C3); X/Y preserved on every call; SP=$06 boundary "
          f"balanced; state RAM $2500/$2501 matches; {len(evidence['negative_cases'])} mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV EX_RND integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
