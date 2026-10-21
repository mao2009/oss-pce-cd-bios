"""Real Geargrafx check of a synthetic ROM-to-RAM handoff (Issue #242; test-only, NOT a BIOS ABI).

tests/fixtures/handoff-probe.s copies a 13-byte payload from bank0 to work RAM $2200 (MPR1 = $F8) and JMPs
there; the payload writes a marker at $2220 and JMPs back to a ROM halt label. The probe loads the ROM with the
System Card loader (LoadBiosFromBuffer) and an empty dummy CD image (LoadMedia, 32 zero-filled MODE1/2352 sectors,
no CD content). It records the first arrival per cycle (cold and warm) at handoff_jump, handoff_entry and
handoff_done, and dumps work RAM offsets $200-$23F (CPU $2200-$223F). The cold cycle also compares the full
work RAM ($2000-$3FFF) between the 'loaded', 'cold' and 'nonreturning' observations.
A run is accepted only if the entry contract, the phase order, the RAM bytes and the marker all match the typed
expectation. Two byte-mutated ROMs (MPR1 bank not work RAM, misaligned entry) must be rejected by the same judge,
each for its own reason key.
Not tested: CD boot, the CD_EXEC loader, the callable ABI and any production BIOS behaviour. See LIMITATIONS.

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
RAM_ENTRY = 0x2200                 # fixed by handoff-probe.s (sta $2200,x / jmp $2200)
IO_BANK, WORK_BANK = 0xFF, 0xF8
MARKER_OFF, MARKER = 0x20, b"\xa5\x5a"  # written at $2220-$2221
DUMP_BYTES = 64                    # probe dumps work RAM offset $200..$23F
PHASES = ("handoff_jump", "handoff_entry", "handoff_done")
# Judge reasons that a negative must produce. Entry and MPR are checked before the phase order.
EXPECTED_REJECTIONS = {"mpr1_rom_bank": "mpr", "entry_misaligned": "entry", "extra_copy_byte": "ram_side_effect"}
# Independent expectation typed from the source: ca65 encoding of lda #$A5 / sta $2220 / lda #$5A /
# sta $2221. The JMP operand is appended from the handoff_done label, so the expected bytes never come
# from the ROM copy they are checking.
PAYLOAD_PREFIX = bytes.fromhex("a9a58d2022a95a8d2122")
WORK_BASE, WORK_RAM_BYTES = 0x2000, 0x2000
# Structured status per check: PASS only where this run judged it; NOT_RUN is printed and recorded, never folded into PASS.
CHECKS = {
    "entry_mpr_sp_phase_order_cold_warm": "PASS",
    "marker_and_payload_64_bytes_cold_warm": "PASS",
    "ram_side_effects_full_cold": "PASS",
    "ram_full_warm": "NOT_RUN",
    "mmio_side_effects": "NOT_RUN",
    "cd_read_and_cd_exec": "NOT_RUN",
    "bios_callable_abi": "NOT_RUN",
}
STEPS = 512                        # bounded run: cold observation after 512 instructions
POST_HALT_STEPS = 32               # 'nonreturning' observation: the BRA loop at handoff_done must be stable
LIMITATIONS = [
    "RAM side effects: the cold cycle compares the full work RAM $2000-$3FFF (loaded -> cold -> nonreturning) with the typed image; warm cycle observes only $2200-$223F",
    "MMIO side effects (VDC/VCE/PSG/timer/ADPCM/CD I/O) are NOT_RUN: the probe has no write trace; the fixture stores are only checked statically",
    "interrupts: the fixture keeps SEI from reset and never CLIs, and the probe injects nothing; IRQ behaviour during the payload is unverified",
    "registers and flags: A/X/Y/P are not asserted at handoff_entry or handoff_done",
    "MPR: handoff_jump checks MPR0/MPR1, handoff_entry checks MPR0/MPR1/MPR7, handoff_done checks MPR7 only; MPR2-6 are unverified",
    "block transfer/DMA side effects before the payload runs are not tested (#173, #361)",
    "CD: an empty dummy 32-sector MODE1/2352 image is mounted; no CD content, no CD boot, no CD_EXEC",
    "not a BIOS ABI: no callable entry contract, no API slot, no loader acceptance",
    "the probe records only the first arrival per label per cycle; re-entry and repeated execution are not observed",
]
CFG = """MEMORY {
    ROM0: start = $E000, size = $2000, file = %O, fill = yes, fillval = $FF;
}
SEGMENTS {
    BOOT: load = ROM0, type = ro, start = $F000;
    VECTORS: load = ROM0, type = ro, start = $FFF6;
}
"""


class Blocked(Exception):
    """The probe produced no verdict (timeout or crash). Never counted as a rejection."""


class HandoffRejected(ValueError):
    """The judge refused a run. `reason` is a stable key that the negatives assert on."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def expected_payload(handoff_done: int) -> bytes:
    return PAYLOAD_PREFIX + b"\x4c" + handoff_done.to_bytes(2, "little")


def expected_ram_writes(payload: bytes) -> dict:
    """Work RAM offset -> byte the handoff must leave there: the copied payload and the marker it writes."""
    writes = {RAM_ENTRY - WORK_BASE + i: byte for i, byte in enumerate(payload)}
    writes[RAM_ENTRY - WORK_BASE + MARKER_OFF] = MARKER[0]
    writes[RAM_ENTRY - WORK_BASE + MARKER_OFF + 1] = MARKER[1]
    return writes


def check_ram_side_effects(records, payload):
    """Cold cycle only: every byte outside the typed writes is unchanged from 'loaded', and the image is stable."""
    rows = {r["phase"]: r for r in records["OBS"]}
    images = {}
    for phase in ("loaded", "cold", "nonreturning"):
        if phase not in rows or "work_ram_hex" not in rows[phase]:
            raise HandoffRejected("obs", f"full work RAM observation '{phase}' missing")
        images[phase] = bytes.fromhex(rows[phase]["work_ram_hex"])
        if len(images[phase]) != WORK_RAM_BYTES:
            raise HandoffRejected("obs", f"'{phase}' work RAM has {len(images[phase])} bytes")
    if images["cold"] != images["nonreturning"]:
        offset = next(i for i in range(WORK_RAM_BYTES) if images["cold"][i] != images["nonreturning"][i])
        raise HandoffRejected("ram_unstable", f"work RAM changed after the halt at offset {offset:#x}")
    expected = expected_ram_writes(payload)
    for offset in range(WORK_RAM_BYTES):
        end = images["cold"][offset]
        if offset in expected:
            if end != expected[offset]:
                raise HandoffRejected("ram_expected", f"work RAM {WORK_BASE + offset:#06x} = {end:#04x}, "
                                                      f"expected {expected[offset]:#04x}")
        elif end != images["loaded"][offset]:
            raise HandoffRejected("ram_side_effect", f"unexpected write at {WORK_BASE + offset:#06x}")


def check_handoff_contract(entry, size, sp, mpr):
    """Entry must lie inside the MPR1 work-RAM window; MPR0/1/7 and SP must match the handoff contract."""
    if not (RAM_WINDOW[0] <= entry and entry + size <= RAM_WINDOW[1]):
        raise HandoffRejected("entry", f"entry {entry:#06x}+{size} outside work RAM window $2000-$3FFF")
    if (mpr[0], mpr[1], mpr[7]) != (IO_BANK, WORK_BANK, 0):
        raise HandoffRejected("mpr", f"MPR0/1/7 {mpr[0]:#x}/{mpr[1]:#x}/{mpr[7]:#x} differ from the RAM handoff contract")
    if sp != 0xFF:
        raise HandoffRejected("sp", f"stack pointer {sp:#x} at handoff, expected 0xff")


def parse_labels(path: Path) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", path.read_text(), re.M):
        found[name] = int(address, 16)
    return found


def build_handoff(output: Path, mode: str):
    """Assemble and link the handoff ROM; return (rom path, ROM labels, payload size, trace labels path)."""
    ca65, ld65 = executable("ca65"), executable("ld65")
    output.mkdir(parents=True, exist_ok=True)
    cfg = output / "handoff.cfg"
    cfg.write_text(CFG, encoding="ascii")
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
    needed = ("handoff_bank", "handoff_jump", "handoff_done", "handoff_payload_rom", "handoff_payload_end_rom")
    if any(name not in labels for name in needed):
        raise ValueError(f"handoff symbols missing: {[n for n in needed if n not in labels]}")
    load, size = labels["handoff_payload_rom"], labels["handoff_payload_end_rom"] - labels["handoff_payload_rom"]
    if size != len(expected_payload(labels["handoff_done"])):
        raise ValueError(f"payload size {size} differs from the typed expectation")
    if not (ROM_BASE <= load and load + size <= ROM_BASE + BANK_BYTES):
        raise ValueError(f"payload source {load:#06x} is outside bank0")
    if data[load - ROM_BASE:load - ROM_BASE + len(PAYLOAD_PREFIX) + 1] != PAYLOAD_PREFIX + b"\x4c":
        raise ValueError("ROM payload source differs from the typed instructions")
    rom = output / "handoff-not-bios.pce"
    rom.write_bytes(data + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    trace_labels = output / "trace-labels.txt"
    trace_labels.write_text(f"handoff_jump {labels['handoff_jump']:04x}\n"
                            f"handoff_entry {RAM_ENTRY:04x}\n"
                            f"handoff_done {labels['handoff_done']:04x}\n", encoding="ascii")
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
    elif kind == "extra_copy_byte":      # cpx #13 -> cpx #14: one unlisted byte ($220D) is also written to RAM
        bank = bytes(data[:BANK_BYTES])
        anchor = bank.find(b"\xe0\x0d\xd0")  # cpx #13 / bne handoff_copy
        if anchor < 0 or bank.count(b"\xe0\x0d\xd0") != 1:
            raise ValueError("mutation anchor mismatch (copy loop bound)")
        end_off = labels["handoff_payload_end_rom"] - ROM_BASE  # the byte the 14th copy reads
        if data[end_off] != 0xFF:  # ld65 fill: the extra write must be an unlisted RAM change, not a no-op
            raise ValueError("mutation anchor mismatch (byte after the payload is not fill)")
        data[anchor + 1] = 0x0E
    else:
        raise ValueError(f"unknown mutation {kind}")
    return bytes(data)


def judge(records, labels, payload):
    """Accept only if cold AND warm entered the RAM payload under the contract and returned.

    Per cycle the order is: MPR0/1 at handoff_jump, PC/SP at handoff_jump, presence and contract of
    handoff_entry, then the phase order, then handoff_done. Raises HandoffRejected with a reason key.
    """
    for cycle in ("cold", "warm"):
        rows = {r["phase"]: r for r in records["TRACE"] if r["cycle"] == cycle}
        jump = rows.get("handoff_jump")
        if jump is None:
            raise HandoffRejected("jump", f"{cycle}: handoff_jump not reached")
        if jump["mpr"][:2] != [IO_BANK, WORK_BANK]:
            raise HandoffRejected("mpr", f"{cycle}: MPR0/1 at handoff_jump {jump['mpr'][:2]} differ from the contract")
        if jump["pc"] != labels["handoff_jump"] or jump["sp"] != 0xFF:
            raise HandoffRejected("jump", f"{cycle}: state at handoff_jump differs from the contract")
        entry = rows.get("handoff_entry")
        if entry is None:
            raise HandoffRejected("entry", f"{cycle}: handoff_entry not reached at {RAM_ENTRY:#x}")
        check_handoff_contract(entry["pc"], len(payload), entry["sp"], entry["mpr"])
        if entry["pc"] != RAM_ENTRY:
            raise HandoffRejected("entry", f"{cycle}: payload entry at {entry['pc']:#x}, expected {RAM_ENTRY:#x}")
        phases = [r["phase"] for r in records["TRACE"] if r["cycle"] == cycle]
        if phases != list(PHASES):
            raise HandoffRejected("phase", f"{cycle}: trace phases {phases}, expected {list(PHASES)}")
        done = rows["handoff_done"]
        if done["pc"] != labels["handoff_done"] or done["mpr"][7] != 0:
            raise HandoffRejected("done", f"{cycle}: payload did not return to ROM handoff_done")
    observed = [row for row in records["OBS"] if row["phase"] in ("cold", "warm")]
    if [row["phase"] for row in observed] != ["cold", "warm"]:
        raise HandoffRejected("obs", "missing cold/warm end observations")
    for row in observed:
        dump = bytes.fromhex(row["work_ram_0200_hex"])
        if len(dump) != DUMP_BYTES:
            raise HandoffRejected("obs", f"{row['phase']}: RAM dump has {len(dump)} bytes")
        if dump[:len(payload)] != payload:
            raise HandoffRejected("payload", f"{row['phase']}: RAM at {RAM_ENTRY:#x} differs from the typed payload")
        if dump[MARKER_OFF:MARKER_OFF + 2] != MARKER:
            raise HandoffRejected("marker", f"{row['phase']}: marker not reached at $2220")
        if row["pc"] != labels["handoff_done"] or row["sp"] != 0xFF:
            raise HandoffRejected("end", f"{row['phase']}: end PC/SP mismatch")
    check_ram_side_effects(records, payload)


def guarded(run, path):
    """Run one probe. A timeout or crash means no verdict: BLOCKED, never REJECTED."""
    try:
        return run(path)
    except subprocess.TimeoutExpired as error:
        raise Blocked(f"probe timed out after {error.timeout}s on {path.name}") from error
    except subprocess.CalledProcessError as error:
        raise Blocked(f"probe exited {error.returncode} on {path.name}") from error


def run_negatives(rom, labels, payload, folder, run):
    """Each mutated ROM must be rejected by judge for its expected reason key; returns the rejections."""
    negatives = {}
    for kind, reason in EXPECTED_REJECTIONS.items():
        path = folder / f"{kind}-not-bios.pce"
        path.write_bytes(mutate(rom, labels, kind))
        records = guarded(run, path)
        try:
            judge(records, labels, payload)
        except HandoffRejected as error:
            if error.reason != reason:
                raise ValueError(f"{kind}: rejected for '{error.reason}', expected '{reason}': {error}") from error
            negatives[kind] = {"reason": error.reason, "message": str(error)}
        else:
            raise ValueError(f"mutated ROM {kind} passed the handoff judge on the real core")
    return negatives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    mode = parser.parse_args().mode
    out = ROOT / "build" / mode / "handoff"
    rom_path, labels, size, trace_labels = build_handoff(out, mode)
    rom = rom_path.read_bytes()
    payload = expected_payload(labels["handoff_done"])
    probe = compile_probe(out)
    with tempfile.TemporaryDirectory(prefix="pce-handoff-") as tmp:
        folder = Path(tmp)
        (folder / "d.bin").write_bytes(bytes(2352 * 32))
        cue = folder / "d.cue"
        cue.write_text('FILE "d.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')

        def run(path):
            return execute(probe, path, cue, trace_labels, steps=STEPS)

        records = guarded(run, rom_path)
        judge(records, labels, payload)
        negatives = run_negatives(rom, labels, payload, folder, run)
    obs = {row["phase"]: row for row in records["OBS"]}
    cold_obs, later_obs = obs["cold"], obs["nonreturning"]
    (out / "handoff-evidence.json").write_text(json.dumps({
        "scope": "synthetic ROM-to-RAM handoff only; LoadBiosFromBuffer(syscard) fed a synthetic ROM, not a System Card image; "
                 "CD = empty dummy image via LoadMedia (zero-filled, no CD content); "
                 "not CD boot, not CD_EXEC, not loader acceptance, not BIOS ABI",
        "rom_sha256": hashlib.sha256(rom).hexdigest(), "payload_bytes": size,
        "guest_source_sha256": hashlib.sha256((ROOT / "tests/fixtures/handoff-probe.s").read_bytes()).hexdigest(),
        "probe_source_sha256": hashlib.sha256(Path(__file__).with_name("geargrafx_api_probe.cpp").read_bytes()).hexdigest(),
        "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "bounded_run": {"fixed_instruction_budget": STEPS, "post_halt_steps": POST_HALT_STEPS,
                        "note": "the probe runs a fixed budget and does not stop early; PC/SP below are measured",
                        "pc_cold": hex(cold_obs["pc"]), "sp_cold": cold_obs["sp"],
                        "pc_nonreturning": hex(later_obs["pc"]), "sp_nonreturning": later_obs["sp"]},
        "checks": CHECKS,
        "status": "PASS_FOR_LISTED_CHECKS", "limitations": LIMITATIONS,
        "observations": records, "negatives": negatives,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    not_run = sorted(name for name, state in CHECKS.items() if state == "NOT_RUN")
    print(f"PASS real Geargrafx handoff to synthetic RAM payload (cold/warm); negatives rejected: {sorted(negatives)}; "
          f"NOT_RUN: {not_run}")


if __name__ == "__main__":
    try:
        main()
    except Blocked as error:
        raise SystemExit(f"handoff integration BLOCKED: {error}") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"handoff integration BLOCKED: {error}") from error
    except ValueError as error:
        raise SystemExit(f"handoff integration FAIL: {error}") from error
