"""Real Geargrafx run of DEV MA_SIN (slot $45, candidate $E0CF; Issue #79).

This is a project-own DEV contract, not System Card ABI evidence.  All 256 angles run in
batches of eight at caller SP=$FF and the documented boundary SP=$06.  For every call the
guest records pre/post P bytes in RAM, varies C/D/I/N/Z/V vectors, and checks P under mask
$CF; HuC6280 T/B behavior is intentionally unverified.  It also checks X:A, Y, MPRs and
stack balance.  The table-byte and PLP-removal mutants must be rejected.

Run: python3 tests/integration/geargrafx_dev_sin.py --rom build/debug/bios-dev/dev-only-not-compatible-syscard3.pce
"""
import argparse
import hashlib
import json
import math
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
SLOT_MA_SIN = 0x45
BATCH = 8
OUT_LO, OUT_HI = 0x2200, 0x2300
P_PRE_OUT, P_POST_OUT, Y_OUT, SP_OUT, ANGLE, X_RESULT = 0x2400, 0x2410, 0x2420, 0x2430, 0x2440, 0x2450
STEPS = 2048
TABLE_PROBE = bytes([0x00, 0x24, 0x48, 0x6A])
MUTANT_ANGLE = 10
P_MASK = 0xCF  # C/Z/I/D/V/N; T and B are deliberately unverified.
P_VECTORS = (0x00, 0x01, 0x02, 0x04, 0x08, 0x40, 0x80, 0xCF)
CALLER_SPS = (0xFF, 0x06)


def oracle(a):
    return math.floor(32767 * math.sin(2 * math.pi * a / 256) + 0.5)


def guest(reset, entry, base, caller_sp):
    """Return reset replacement and halt address for angles base..base+7.

    The PHP/TSX/LDA sequence obtains the actual pre/post P byte from the RAM-mapped stack.
    Before JSR, LDA pre-P; PHA; LDA angle; PLP restores both the captured P and A immediately
    before the call.  This makes the RAM pre/post comparison an observation, not a typed copy.
    """
    code = bytearray([
        0x78, 0xD8,                                      # SEI; CLD
        0xA9, 0xF8, 0x53, 0x02,                          # MPR1 = work RAM
        0xA2, caller_sp, 0x9A,                           # caller SP
        0xA0, 0x00,                                      # Y = batch index
    ])
    loop = reset + len(code)
    code += bytes([
        0xD8, 0x98, 0x18, 0x69, base & 0xFF,            # CLD; TYA; CLC; ADC #base
        0x8D, ANGLE & 0xFF, ANGLE >> 8,                  # angle = A
        0xB9, 0x00, 0x00, 0x48,                          # LDA P-vector,Y; push requested P
        0xAD, ANGLE & 0xFF, ANGLE >> 8, 0x28,            # A = angle; PLP
        0x08, 0xBA, 0xBD, 0x01, 0x21,                    # PHP; TSX; LDA $2101,X = actual pre-P
        0x99, P_PRE_OUT & 0xFF, P_PRE_OUT >> 8, 0x68,    # pre-P[Y]; discard PHP snapshot
        0xB9, P_PRE_OUT & 0xFF, P_PRE_OUT >> 8, 0x48,    # push captured pre-P
        0xAD, ANGLE & 0xFF, ANGLE >> 8, 0x28,            # restore A and P immediately before JSR
        0x20, entry & 0xFF, entry >> 8,                  # JSR MA_SIN
        0x99, OUT_LO & 0xFF, OUT_LO >> 8,                # result A
        0x8E, X_RESULT & 0xFF, X_RESULT >> 8,            # preserve result X before TSX
        0x08, 0xBA, 0xBD, 0x01, 0x21,                    # PHP; TSX; LDA $2101,X = actual post-P
        0x99, P_POST_OUT & 0xFF, P_POST_OUT >> 8,        # post-P[Y]
        0x68,                                            # discard PHP snapshot
        0xAD, X_RESULT & 0xFF, X_RESULT >> 8,            # recover result X after TSX
        0x99, OUT_HI & 0xFF, OUT_HI >> 8,
        0x98, 0x99, Y_OUT & 0xFF, Y_OUT >> 8,            # returned Y
        0xC8, 0xC0, BATCH,                               # INY; CPY #BATCH
    ])
    # The table is appended after the halt loop; fill the LDA absolute,Y operand now.
    p_vector_operand = loop - reset + 9
    branch_at = reset + len(code)
    code += bytes([0xD0, (loop - (branch_at + 2)) & 0xFF])
    code += bytes([0xBA, 0x8E, SP_OUT & 0xFF, SP_OUT >> 8])
    halt = reset + len(code)
    code += bytes([0x80, 0xFE])
    p_vector_table = reset + len(code)
    code[p_vector_operand:p_vector_operand + 2] = p_vector_table.to_bytes(2, "little")
    code += bytes(P_VECTORS)
    return bytes(code), halt


def check(label, observation, halt, work_ram, base, caller_sp):
    if observation["pc"] != halt:
        raise ValueError(f"{label}: guest did not reach halt (pc ${observation['pc']:04X}, want ${halt:04X})")
    if observation["mpr"][1] != 0xF8 or observation["mpr"][7] != 0:
        raise ValueError(f"{label}: MPR changed (mpr={observation['mpr']})")
    if work_ram[SP_OUT - 0x2000] != caller_sp:
        raise ValueError(f"{label}: stack not balanced (want ${caller_sp:02X})")
    rows = []
    for i in range(BATCH):
        angle = (base + i) & 0xFF
        got = (work_ram[OUT_HI - 0x2000 + i] << 8) | work_ram[OUT_LO - 0x2000 + i]
        if got != (oracle(angle) & 0xFFFF):
            raise ValueError(f"{label}: angle ${angle:02X} X:A ${got:04X}, want ${oracle(angle) & 0xFFFF:04X}")
        if work_ram[Y_OUT - 0x2000 + i] != i:
            raise ValueError(f"{label}: Y not preserved at angle ${angle:02X}")
        pre = work_ram[P_PRE_OUT - 0x2000 + i]
        post = work_ram[P_POST_OUT - 0x2000 + i]
        requested = P_VECTORS[(base + i) % len(P_VECTORS)]
        if (pre & P_MASK) != (requested & P_MASK):
            raise ValueError(f"{label}: pre-P vector ${pre:02X} does not realize ${requested:02X} under ${P_MASK:02X}")
        if (pre & P_MASK) != (post & P_MASK):
            raise ValueError(f"{label}: P not preserved at angle ${angle:02X} (pre ${pre:02X}, post ${post:02X})")
        rows.append((pre, post))
    return rows


def run_batch(probe, rom_bytes, folder, cue, reset, entry, base, caller_sp, label):
    code, halt = guest(reset, entry, base, caller_sp)
    variant = bytearray(rom_bytes)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    cold = next(row for row in dev.run_probe(probe, rom.resolve(), cue, count=STEPS) if row["phase"] == "cold")
    return cold, halt, bytes.fromhex(cold["work_ram_hex"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    args = parser.parse_args()
    data = args.rom.read_bytes()
    verify_dev_rom(data, manifest=args.rom.parent / "manifest.json")
    reset = int.from_bytes(data[0x1FFE:0x2000], "little")
    entry = dev.entry_address(SLOT_MA_SIN)
    table = 3 * SLOT_MA_SIN
    body = data[table + 1] | data[table + 2] << 8
    if data[table] != 0x4C or not 0xE000 <= body < 0xFFF6:
        raise ValueError("slot $45 table entry is not a JMP into the DEV ROM body")
    probe = api.compile_probe(args.rom.parent)
    from environment import DEPENDENCIES  # noqa: E402
    evidence = {"scope": "DEV ROM real-core run of project-own MA_SIN, NOT System Card ABI compatibility",
                "rom_sha256": hashlib.sha256(data).hexdigest(), "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "batches": [], "angles_checked": 0, "caller_sps": list(CALLER_SPS), "p_mask": f"${P_MASK:02X}",
                "p_unverified": ["T", "B"], "p_vectors": [f"${x:02X}" for x in P_VECTORS], "negative_cases": []}
    with tempfile.TemporaryDirectory(prefix="pce-dev-sin-") as tmp:
        folder = Path(tmp)
        (folder / "original.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "original.cue"
        cue.write_text('FILE "original.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        observed = set()
        for caller_sp in CALLER_SPS:
            for base in range(0, 256, BATCH):
                label = f"sin-sp{caller_sp:02x}-{base:02x}"
                cold, halt, ram = run_batch(probe, data, folder, cue, reset, entry, base, caller_sp, label)
                rows = check(label, cold, halt, ram, base, caller_sp)
                observed.update(pre & P_MASK for pre, _post in rows)
                evidence["batches"].append({"base": base, "angles": BATCH, "caller_sp": caller_sp,
                                            "p_pre": [pre for pre, _post in rows], "p_post": [post for _pre, post in rows]})
                evidence["angles_checked"] += BATCH
        if evidence["angles_checked"] != 512 or observed != {x & P_MASK for x in P_VECTORS}:
            raise ValueError("incomplete angle/stack/P-vector coverage")

        pos = data.find(TABLE_PROBE)
        if pos < 0 or data.find(TABLE_PROBE, pos + 1) >= 0:
            raise ValueError("sin_lo table probe is not unique in the ROM")
        mutant = bytearray(data); mutant[pos + MUTANT_ANGLE] ^= 0x01
        cold, halt, ram = run_batch(probe, bytes(mutant), folder, cue, reset, entry, 8, 0xFF, "sin-table-mutant")
        try:
            check("sin-table-mutant", cold, halt, ram, 8, 0xFF)
        except ValueError as error:
            if "X:A" not in str(error):
                raise ValueError(f"table mutant failed for unrelated reason: {error}") from error
            evidence["negative_cases"].append({"case": "sin_lo[10] flipped", "rejected": True, "reason": str(error)})
        else:
            raise ValueError("table mutant passed")

        plp_rts = next((i for i in range(body - 0xE000, min(body - 0xE000 + 0x80, len(data) - 1))
                        if data[i:i + 2] == b"\x28\x60"), None)
        if plp_rts is None:
            raise ValueError("slot $45 PLP;RTS not found")
        mutant = bytearray(data); mutant[plp_rts] = 0xEA
        cold, halt, ram = run_batch(probe, bytes(mutant), folder, cue, reset, entry, 0, 0xFF, "sin-plp-mutant")
        try:
            check("sin-plp-mutant", cold, halt, ram, 0, 0xFF)
        except ValueError as error:
            if "P not preserved" not in str(error) and "halt" not in str(error):
                raise ValueError(f"PLP mutant failed for unrelated reason: {error}") from error
            evidence["negative_cases"].append({"case": "slot_45_without_PLP", "rejected": True, "reason": str(error)})
        else:
            raise ValueError("PLP mutant passed")
    (args.rom.parent / "geargrafx-dev-sin-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"PASS real Geargrafx DEV ROM: MA_SIN all 256 angles at SP=$FF/$06; X:A/Y, MPR, stack and P mask $CF checked; table and PLP mutants rejected")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV SIN integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
