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
WRAM_BASE = 0x2000  # work RAM index i = CPU address $2000 + i (MPR1 = $F8)
CASES = [(0, 0), (0, 0xFFFF), (0xFFFF, 0), (1, 1), (1, 0xFFFF), (0xFFFF, 1), (0xFFFF, 0xFFFF),
         (0x8000, 2), (2, 0x8000), (0x0100, 0x0100), (0x00FF, 0x0101), (0xAAAA, 0x5555),
         (0x1234, 0x5678), (0x8000, 0x8000), (0x7FFF, 0xFFFF), (0x00FF, 0x00FF), (255, 256), (0x0F0F, 0x3C3C)]
Y_PRESET = 0xA5
SP_CASES = [0x0A, 0x40, 0xFF]  # caller SP before the 4 pushes; entry SP = caller SP - 6 (must be in $04..$F9)


def guest(reset, entry, m, q, sp, extra=b""):
    """Reset-replacement code at CPU address `reset`. `extra` is appended before the halt loop (mutants only).
    Returns (code, halt CPU address)."""
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
    """The guest ran to its halt loop but returned a value that differs from the spec."""


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
    observed = (int.from_bytes(s[0:4], "little"), s[4] & 0xCF, s[5], s[6])
    want = (product, expected_p(q), Y_PRESET, sp)
    if observed != want:
        raise Mismatch(f"{label}: product/P/Y/SP observed {observed}, expected {want}")
    check_work_ram(label, bytes.fromhex(loaded["work_ram_hex"]), bytes.fromhex(cold["work_ram_hex"]), sp)


def run_case(probe, base, folder, cue, reset, label, entry, m, q, sp=0xFF, extra=b""):
    code, halt = guest(reset, entry, m, q, sp, extra)
    variant = bytearray(base)
    variant[reset - 0xE000:reset - 0xE000 + len(code)] = code
    rom = folder / f"{label}.pce"
    rom.write_bytes(variant)
    rows = dev.run_probe(probe, rom.resolve(), cue)
    check(label, rows, halt, m, q, sp)


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
        # Negative 3 (Issue #456): the guest writes one byte to $2300, outside the expected work-RAM set. The full
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
                                           "first": rejected[0]})
    (args.rom.parent / "geargrafx-dev-mul16u-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    total = len(CASES) + 2 * 3  # full table at SP=$FF, plus 3 cases at each of SP=$0A and $40
    print(f"PASS real Geargrafx DEV ROM: MA_MUL16U slot $41 {total} cases (SP=$FF full table; SP=$0A boundary "
          f"and SP=$40 mid-range); entry SP $04..$F9 precondition; 32-bit product, Y, P (D=1,C=1 entry), SP, "
          f"MPR1/MPR7 absolute and MPR0/2-6 unchanged; full 8 KiB work RAM diff (allowed $21(sp-9)..$21(sp), "
          f"$2200-$2206, sentinel above SP); mutant rejections: "
          f"{sum(c['rejected'] for c in evidence['negative_cases'])}/9 "
          f"(no-CLD, no-PHP/PLP, out-of-set $2300 write; 3 cases each)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if "BLOCKED" in str(error) else "FAIL"
        print(f"DEV MUL16U integration {status}: {error}", file=sys.stderr)
        raise SystemExit(BLOCKED_EXIT if status == "BLOCKED" else 1) from error
