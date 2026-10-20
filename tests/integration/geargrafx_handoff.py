"""Real Geargrafx check of a synthetic ROM-to-RAM handoff (Issue #242; test-only, NOT a BIOS ABI).

tests/fixtures/handoff-probe.s copies a 16-byte payload from bank0 to work RAM $2200 (MPR1 = $F8) and JMPs
there; the payload writes a marker at $2220 and JMPs back to a ROM halt label. The Geargrafx probe records
PC/SP/MPR at handoff_jump, handoff_entry and handoff_done (cold and warm) and dumps CPU $2200-$223F.
A run is accepted only if the RAM entry contract, the copied payload bytes and the marker all match.
Two byte-mutated ROMs (MPR1 bank not work RAM, misaligned entry) must be rejected by the same judge.
Out of scope: CD media, the loader, CD_EXEC, the callable ABI and any production BIOS behaviour.

Run (after make setup, TOOLS_DIR outside the repo):  python3 tests/integration/geargrafx_handoff.py --mode debug
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_contracts import execute  # noqa: E402

ROM_BASE, BANK_BYTES, IMAGE_BYTES = 0xE000, 0x2000, 0x40000
RAM_WINDOW = (0x2000, 0x4000)      # CPU window of MPR1 = work RAM bank $F8
RAM_ENTRY = 0x2200
IO_BANK, WORK_BANK = 0xFF, 0xF8
MARKER_OFF, MARKER = 0x20, b"\xa5\x5a"  # written at $2220-$2221
DUMP_BYTES = 64                    # probe dumps work RAM offset $200..$23F
PHASES = ("handoff_jump", "handoff_entry", "handoff_done")
# Independent expectation, typed here from the source (ca65 encoding of the four payload instructions
# before the final JMP): lda #$A5 / sta $2220 / lda #$5A / sta $2221. The JMP operand is appended from
# the handoff_done label, so the expected bytes never come from the ROM copy they are checking.
PAYLOAD_PREFIX = bytes.fromhex("a9a58d2022a95a8d2122")


def expected_payload(handoff_done: int) -> bytes:
    return PAYLOAD_PREFIX + b"\x4c" + handoff_done.to_bytes(2, "little")
CFG = """MEMORY {
    ROM0: start = $E000, size = $2000, file = %O, fill = yes, fillval = $FF;
    WRAM: start = $2200, size = $0100, type = rw;
}
SEGMENTS {
    BOOT: load = ROM0, type = ro, start = $F000;
    RAMCODE: load = ROM0, run = WRAM, type = ro;
    VECTORS: load = ROM0, type = ro, start = $FFF6;
}
"""


def check_handoff_contract(entry, size, sp, mpr):
    """Entry must lie inside the MPR1 work-RAM window; MPR0/MPR1/MPR7 and SP must match the handoff contract."""
    if not (RAM_WINDOW[0] <= entry and entry + size <= RAM_WINDOW[1]):
        raise ValueError(f"entry {entry:#06x}+{size} outside work RAM window $2000-$3FFF")
    if (mpr[0], mpr[1], mpr[7]) != (IO_BANK, WORK_BANK, 0):
        raise ValueError(f"MPR0/1/7 {mpr[0]:#x}/{mpr[1]:#x}/{mpr[7]:#x} differ from the RAM handoff contract")
    if sp != 0xFF:
        raise ValueError(f"stack pointer {sp:#x} at handoff, expected 0xff")


def parse_labels(path: Path) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", path.read_text(), re.M):
        found[name] = int(address, 16)
    return found


def build_handoff(output: Path, mode: str):
    ca65, ld65 = executable("ca65"), executable("ld65")
    output.mkdir(parents=True, exist_ok=True)
    cfg = output / "handoff.cfg"
    cfg.write_text(CFG)
    obj = output / "handoff-probe.o"
    subprocess.run([ca65, str(ROOT / "tests/fixtures/handoff-probe.s"), "-o", str(obj),
                    *(["-g"] if mode == "debug" else [])], check=True)
    bank, labels_path = output / "bank0.bin", output / "handoff.lbl"
    subprocess.run([ld65, "-C", str(cfg), "-o", str(bank), "-Ln", str(labels_path),
                    "-m", str(output / "handoff.map"), str(obj)], check=True)
    data = bank.read_bytes()
    if len(data) != BANK_BYTES:
        raise ValueError("handoff bank is not 8 KiB")
    labels = parse_labels(labels_path)
    if labels.get("handoff_entry") != RAM_ENTRY or any(name not in labels for name in PHASES):
        raise ValueError(f"handoff symbols/placement mismatch: {labels}")
    size = labels["handoff_payload_end"] - RAM_ENTRY
    if size != len(expected_payload(labels["handoff_done"])):
        raise ValueError(f"payload size {size} differs from the typed expectation")
    src_at = labels["handoff_payload_src"] - ROM_BASE
    load = int.from_bytes(data[src_at:src_at + 2], "little")  # ROM address of the payload copy source
    if not (ROM_BASE <= load and load + size <= ROM_BASE + BANK_BYTES):
        raise ValueError(f"payload source {load:#06x} is outside bank0")
    head = len(PAYLOAD_PREFIX)
    if data[load - ROM_BASE:load - ROM_BASE + head + 1] != PAYLOAD_PREFIX + b"\x4c":
        raise ValueError("ROM payload source differs from the typed instructions")
    rom = output / "handoff-not-bios.pce"
    rom.write_bytes(data + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    trace_labels = output / "trace-labels.txt"
    trace_labels.write_text("".join(f"{name} {labels[name]:04x}\n" for name in PHASES), encoding="ascii")
    return rom, labels, size, trace_labels


def mutate(rom: bytes, labels: dict, kind: str) -> bytes:
    data = bytearray(rom)
    if kind == "mpr1_rom_bank":          # lda #$F8 before tam #2 -> lda #$00: MPR1 maps ROM, not work RAM
        off = labels["handoff_bank"] - 1 - ROM_BASE
        if data[off] != WORK_BANK:
            raise ValueError("mutation anchor mismatch (MPR1 immediate)")
        data[off] = 0x00
    elif kind == "entry_misaligned":     # jmp $2200 -> jmp $2201: starts inside the payload
        off = labels["handoff_jump"] + 1 - ROM_BASE
        if bytes(data[off - 1:off + 2]) != b"\x4c\x00\x22":
            raise ValueError("mutation anchor mismatch (JMP operand)")
        data[off] = 0x01
    else:
        raise ValueError(f"unknown mutation {kind}")
    return bytes(data)


def judge(records, labels, payload):
    """Raise ValueError unless cold AND warm runs entered the RAM payload under the contract and returned."""
    for cycle in ("cold", "warm"):
        rows = [r for r in records["TRACE"] if r["cycle"] == cycle]
        phases = [r["phase"] for r in rows]
        if phases != list(PHASES):
            raise ValueError(f"{cycle}: trace phases {phases}, expected {list(PHASES)}")
        jump, entry, done = rows
        if jump["pc"] != labels["handoff_jump"] or jump["sp"] != 0xFF or jump["mpr"][:2] != [IO_BANK, WORK_BANK]:
            raise ValueError(f"{cycle}: state at handoff_jump differs from the contract")
        check_handoff_contract(entry["pc"], len(payload), entry["sp"], entry["mpr"])
        if entry["pc"] != RAM_ENTRY:
            raise ValueError(f"{cycle}: payload entry at {entry['pc']:#x}, expected {RAM_ENTRY:#x}")
        if done["pc"] != labels["handoff_done"] or done["mpr"][7] != 0:
            raise ValueError(f"{cycle}: payload did not return to ROM handoff_done")
    observed = [row for row in records["OBS"] if row["phase"] in ("cold", "warm")]
    if [row["phase"] for row in observed] != ["cold", "warm"]:
        raise ValueError("missing cold/warm end observations")
    for row in observed:
        dump = bytes.fromhex(row["work_ram_0200_hex"])
        if len(dump) != DUMP_BYTES:
            raise ValueError(f"{row['phase']}: RAM dump has {len(dump)} bytes")
        if dump[:len(payload)] != payload:
            raise ValueError(f"{row['phase']}: RAM at {RAM_ENTRY:#x} differs from the copied payload")
        if dump[MARKER_OFF:MARKER_OFF + 2] != MARKER:
            raise ValueError(f"{row['phase']}: marker not reached at $2220")
        if row["pc"] != labels["handoff_done"] or row["sp"] != 0xFF:
            raise ValueError(f"{row['phase']}: end PC/SP mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    mode = parser.parse_args().mode
    out = ROOT / "build" / mode / "handoff"
    rom_path, labels, size, trace_labels = build_handoff(out, mode)
    rom = rom_path.read_bytes()
    payload = expected_payload(labels["handoff_done"])
    probe = compile_probe(out)
    negatives = {}
    with tempfile.TemporaryDirectory(prefix="pce-handoff-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        records = execute(probe, rom_path, cue, trace_labels)
        judge(records, labels, payload)
        for kind in ("mpr1_rom_bank", "entry_misaligned"):
            path = folder / f"{kind}-not-bios.pce"
            path.write_bytes(mutate(rom, labels, kind))
            try:
                judge(execute(probe, path, cue, trace_labels), labels, payload)
            except (ValueError, subprocess.CalledProcessError) as error:
                # A probe crash on a mutant is also a rejection: that run was not accepted as a handoff.
                negatives[kind] = f"REJECTED: {error}"
            else:
                raise ValueError(f"mutated ROM {kind} passed the handoff judge on the real core")
    (out / "handoff-evidence.json").write_text(json.dumps({
        "scope": "synthetic ROM-to-RAM handoff only; not CD, not loader, not BIOS ABI",
        "rom_sha256": hashlib.sha256(rom).hexdigest(), "payload_bytes": size,
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "status": "PASS", "observations": records, "negatives": negatives,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS real Geargrafx handoff to synthetic RAM payload (cold/warm); negatives rejected: {sorted(negatives)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"handoff integration FAIL/BLOCKED: {error}") from error
