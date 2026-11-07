"""Real Geargrafx check of a synthetic ROM-to-RAM handoff (Issue #242; test-only, NOT a BIOS ABI).

tests/fixtures/handoff-probe.s copies a 13-byte payload from bank0 to work RAM $2200 (MPR1 = $F8) and JMPs
there; the payload writes a marker at $2220 and JMPs back to a ROM halt label. The probe loads the synthetic ROM
via LoadBiosFromBuffer(syscard), not a System Card image, and an empty dummy CD image (LoadMedia, 32 zero-filled MODE1/2352 sectors,
no CD content). It records the first arrival per cycle (cold and warm) at handoff_jump, handoff_entry and
handoff_done, and dumps work RAM offsets $200-$23F (CPU $2200-$223F). The full work RAM ($2000-$3FFF) is
compared between the 'loaded', 'cold', 'nonreturning' and 'warm' observations: the cold cycle must differ from
'loaded' only by the typed payload copy and marker, the halt must be stable, and the warm end state must equal
the cold end state. Per cycle the judge also checks guest-driven registers (A/X), preservation of Y, MPR2-6
and SP across jump -> entry -> done, the SEI interrupt flag, and that the sampled VDC state is unchanged.
Power-on residues (absolute Y/P and MPR2-6 values) are deliberately not asserted: they differ between debug
and release builds and between cold and warm cycles on the pinned core.
Before assembly, build_handoff also runs fixture_static_violations() on the fixture source: an
operand-level reachability check (every mnemonic and operand, TAM targets, jump/branch targets, vectors)
that must be empty, or the build fails closed. This statically constrains every guest write in this fixture
to STA $2200,x / $2220 / $2221 under MPR1=$F8; it is not a dynamic MMIO write trace (mmio_side_effects
stays NOT_RUN).
A run is accepted only if the entry contract, the phase order, the RAM bytes and the marker all match the typed
expectation. Four byte-mutated ROMs (MPR1 bank not work RAM, misaligned entry, extra copied byte, marker
store redirected to the hardware page) must be rejected by the same judge, each for its own reason key.
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
PROBE_SOURCE_PATH = Path(__file__).with_name("geargrafx_api_probe.cpp")
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from environment import DEPENDENCIES, executable  # noqa: E402
from probe_digest import check_evidence_file, sha256_file  # noqa: E402
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
EXPECTED_REJECTIONS = {"mpr1_rom_bank": "mpr", "entry_misaligned": "entry", "extra_copy_byte": "ram_side_effect",
                      "marker_outside_ram": "payload"}
# Independent expectation typed from the source: ca65 encoding of lda #$A5 / sta $2220 / lda #$5A /
# sta $2221. The JMP operand is appended from the handoff_done label, so the expected bytes never come
# from the ROM copy they are checking.
PAYLOAD_PREFIX = bytes.fromhex("a9a58d2022a95a8d2122")
WORK_BASE, WORK_RAM_BYTES = 0x2000, 0x2000
# Structured status per check: PASS only where this run judged it; NOT_RUN is printed and recorded, never folded into PASS.
CHECKS = {
    "entry_mpr_sp_phase_order_cold_warm": "PASS",
    "marker_and_payload_64_bytes_cold_warm": "PASS",
    "full_ram_cold_typed_warm_equals_cold": "PASS",
    "register_preservation_cold_warm": "PASS",
    "fixture_static_reachability": "PASS",
    "stack_page_unchanged": "PASS",
    "mmio_sampled_vdc_unchanged": "PASS",
    "mmio_side_effects": "NOT_RUN",
    "cd_read_and_cd_exec": "NOT_RUN",
    "bios_callable_abi": "NOT_RUN",
}
STEPS = 512                        # bounded run: cold observation after 512 instructions
POST_HALT_STEPS = 32               # 'nonreturning' observation: the BRA loop at handoff_done must be stable
LIMITATIONS = [
    "RAM side effects: the full work RAM $2000-$3FFF is compared cold (loaded -> cold -> nonreturning, typed writes only) and warm (warm end state must equal the cold end state); there is no separate warm-loaded baseline observation, so a warm write that reproduced the cold image byte-for-byte would not be visible",
    "MMIO side effects (VDC/VCE/PSG/timer/ADPCM/CD I/O) are NOT_RUN: the probe has no write trace and only samples VDC AR/R0 at the four end observations (checked unchanged, mmio_sampled_vdc_unchanged). The fixture's guest writes are statically constrained by fixture_static_violations() (run as a build gate): the only write instructions are STA $2200,x / $2220 / $2221, which under the dynamically checked MPR1=$F8 land in work RAM $2000-$3FFF; ST0/ST1/ST2, STX/STY, block transfers, indirect forms, JSR/RTS and stack ops are absent and statically rejected. That is a bounded static guarantee for this fixture under probe conditions (SEI, no injected IRQ, MPR7=0, dummy CD), not a dynamic MMIO trace",
    "interrupts: the fixture keeps SEI from reset and never CLIs, and the probe injects nothing; the stack page $21F8-$21FF is checked unchanged at the four end observations, so an interrupt/BRK dispatch push during the run would be detected post-hoc (its bytes remain after a return), but handler execution itself is unverified. NMI has no source in this harness (its vector points at the writing-free trap)",
    "registers and flags: only guest-driven values and preservation are asserted (A/X at jump/entry/done, Y and MPR2-6 preserved across the payload, SP=$FF, I flag set); absolute Y/P and absolute MPR2-6 values are power-on residues that differ between debug/release and cold/warm, so they are not asserted",
    "MPR: handoff_jump checks MPR0/MPR1, handoff_entry checks MPR0/MPR1/MPR7, handoff_done checks MPR7; MPR2-6 are checked for preservation across the payload, not for absolute values",
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


# Operand-level static reachability for tests/fixtures/handoff-probe.s. The fixture has no ST0/ST1/ST2,
# STX/STY, block transfers (TII/TIN/TDD/TIA/TAI), read-modify-write, JSR/RTS/RTI, PHA/PHP/PLA/PLP, indirect
# or zero-page addressing, or CLI. Its only memory writes are the three STA forms below, and its only MPR
# changes are TAM #$01 (MPR0) and TAM #$02 (MPR1); MPR1=$F8 is then checked dynamically at handoff_entry,
# which is what makes $2200/$2220/$2221 work-RAM addresses rather than hardware-page addresses. Any other
# mnemonic, operand, vector target, or a missing leading SEI is a violation and fails the build closed.
ALLOWED_MNEMONICS = {"sei", "cld", "lda", "tam", "txs", "ldx", "sta", "inx", "cpx", "bne", "jmp", "bra"}
NO_OPERAND = {"sei", "cld", "txs", "inx"}
ALLOWED_OPERANDS = {
    "lda": {"#$FF", "#$F8", "#$A5", "#$5A", "handoff_payload_rom,x"},
    "ldx": {"#$FF", "#$00"},
    "cpx": {"#13"},
    "tam": {"#$01", "#$02"},
    "sta": {"$2200,x", "$2220", "$2221"},
    "jmp": {"$2200", "handoff_done"},
    "bne": {"handoff_copy"},
    "bra": {"handoff_done", "handoff_trap"},
}
ALLOWED_VECTORS = {"handoff_trap", "handoff_reset"}


def fixture_static_violations(source: str) -> list:
    """Return static reachability violations for the handoff fixture source ([] means the fixture is exactly
    the reviewed instruction/operand set). Pure text check: no assembly, no execution."""
    violations, mnemonics = [], []
    for lineno, raw in enumerate(source.splitlines(), 1):
        code = raw.split(";", 1)[0].strip()
        if not code:
            continue
        if code.startswith(".word"):
            target = code[len(".word"):].strip()
            if target not in ALLOWED_VECTORS:
                violations.append(f"line {lineno}: vector target {target!r} not allowed")
            continue
        if code.startswith("."):
            # Only behaviour-free directives are allowed; .byte/.res/.align/.org could inject bytes
            # that never appear as a checked mnemonic.
            directive = code.split(None, 1)[0].lower()
            if directive not in (".setcpu", ".export", ".segment"):
                violations.append(f"line {lineno}: directive {directive!r} not allowed")
            continue
        if ":" in code:  # label alone, or label: instruction on one line
            code = code.split(":", 1)[1].strip()
            if not code:
                continue
        parts = code.split(None, 1)
        mnemonic = parts[0].lower()
        operand = parts[1].strip() if len(parts) == 2 else ""
        mnemonics.append(mnemonic)
        if mnemonic not in ALLOWED_MNEMONICS:
            violations.append(f"line {lineno}: mnemonic {mnemonic!r} not allowed")
        elif mnemonic in NO_OPERAND:
            if operand:
                violations.append(f"line {lineno}: {mnemonic} takes no operand, got {operand!r}")
        elif operand not in ALLOWED_OPERANDS[mnemonic]:
            violations.append(f"line {lineno}: {mnemonic} operand {operand!r} not allowed")
    if not mnemonics or mnemonics[0] != "sei":
        violations.append("reset entry must start with sei (interrupts masked from the first instruction)")
    return violations


def check_ram_side_effects(records, payload):
    """Cold: every byte outside the typed writes is unchanged from 'loaded', and the image is stable.
    Warm: the end state must equal the cold end state byte-for-byte (the warm reset re-runs the same copy)."""
    rows = {r["phase"]: r for r in records["OBS"]}
    images = {}
    for phase in ("loaded", "cold", "nonreturning", "warm"):
        if phase not in rows or "work_ram_hex" not in rows[phase]:
            raise HandoffRejected("obs", f"full work RAM observation '{phase}' missing")
        images[phase] = bytes.fromhex(rows[phase]["work_ram_hex"])
        if len(images[phase]) != WORK_RAM_BYTES:
            raise HandoffRejected("obs", f"'{phase}' work RAM has {len(images[phase])} bytes")
    if images["cold"] != images["nonreturning"]:
        offset = next(i for i in range(WORK_RAM_BYTES) if images["cold"][i] != images["nonreturning"][i])
        raise HandoffRejected("ram_unstable", f"work RAM changed after the halt at offset {offset:#x}")
    if images["warm"] != images["cold"]:
        offset = next(i for i in range(WORK_RAM_BYTES) if images["warm"][i] != images["cold"][i])
        raise HandoffRejected("ram_warm", f"warm work RAM differs from cold at offset {offset:#x}")
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


FLAG_INTERRUPT = 0x04  # P I bit; same encoding as tests/integration/geargrafx_contracts.py


def check_register_preservation(records, payload):
    """Per cycle, guest-driven registers and payload-untouched state across jump -> entry -> done.

    A at jump/entry is the last copied payload byte (the high byte of the JMP target, taken from the typed
    payload, not from the RAM copy being checked); X is the copy-loop exit value; A at done is the payload's
    final LDA operand (the marker's second byte). Y, MPR2-6 and SP are not written by the fixture, so they
    must be preserved -- their absolute values are power-on residues and are not asserted. The I flag must
    stay set (SEI at reset, no CLI in the fixture).
    """
    for cycle in ("cold", "warm"):
        rows = {r["phase"]: r for r in records["TRACE"] if r["cycle"] == cycle}
        if any(phase not in rows or any(key not in rows[phase]
                                        for key in ("a", "x", "y", "p", "sp", "mpr"))
               for phase in PHASES):
            raise HandoffRejected("obs", f"{cycle}: register observation incomplete")
        jump, entry, done = rows["handoff_jump"], rows["handoff_entry"], rows["handoff_done"]
        if (jump["a"], entry["a"]) != (payload[-1], payload[-1]):
            raise HandoffRejected("regs", f"{cycle}: A at jump/entry {jump['a']:#x}/{entry['a']:#x} != last payload byte")
        if (jump["x"], entry["x"], done["x"]) != (len(payload), len(payload), len(payload)):
            raise HandoffRejected("regs", f"{cycle}: X across the handoff is not the copy-loop exit value")
        if done["a"] != MARKER[1]:
            raise HandoffRejected("regs", f"{cycle}: A at handoff_done {done['a']:#x} != final LDA operand")
        if not (jump["y"] == entry["y"] == done["y"]):
            raise HandoffRejected("regs", f"{cycle}: Y not preserved across the payload")
        if not (jump["mpr"][2:7] == entry["mpr"][2:7] == done["mpr"][2:7]):
            raise HandoffRejected("regs", f"{cycle}: MPR2-6 not preserved across the payload")
        if (jump["sp"], entry["sp"], done["sp"]) != (0xFF, 0xFF, 0xFF):
            raise HandoffRejected("regs", f"{cycle}: SP not $FF across the handoff")
        if any(not (row["p"] & FLAG_INTERRUPT) for row in (jump, entry, done)):
            raise HandoffRejected("regs", f"{cycle}: interrupt flag not set across the handoff")


def check_stack_unchanged(records):
    """Stack page bytes ($21F8-$21FF, MPR1-mapped work RAM) must be unchanged at the four end observations.

    The fixture has no stack instruction, so the only way these bytes change is an interrupt/BRK dispatch
    push (SEI blocks IRQ1/IRQ2/timer, the probe injects nothing, and there is no BRK instruction; NMI has
    no source in this harness, and its vector points at the writing-free trap). A push during the run
    would leave its bytes behind even if a handler returned, so this detects it post-hoc. Handler
    execution itself remains unverified (see LIMITATIONS)."""
    rows = {r["phase"]: r for r in records["OBS"]}
    stacks = {}
    for phase in ("loaded", "cold", "nonreturning", "warm"):
        if phase not in rows or "stack_01f8_hex" not in rows[phase]:
            raise HandoffRejected("obs", f"stack observation '{phase}' missing")
        stacks[phase] = rows[phase]["stack_01f8_hex"]
    if any(value != stacks["loaded"] for value in stacks.values()):
        raise HandoffRejected("stack", f"stack page changed from loaded: {stacks}")


def check_mmio_sampled(records):
    """Sampled VDC state (AR/R0) at the four end observations must be unchanged from 'loaded'.

    This is a sampled-state check only, not an MMIO write trace: a write that restored the sampled
    registers before the observation would not be visible (see LIMITATIONS)."""
    rows = {r["phase"]: r for r in records["OBS"]}
    sampled = {}
    for phase in ("loaded", "cold", "nonreturning", "warm"):
        if phase not in rows or "vdc_ar" not in rows[phase] or "vdc_r0" not in rows[phase]:
            raise HandoffRejected("obs", f"sampled VDC observation '{phase}' missing")
        sampled[phase] = (rows[phase]["vdc_ar"], rows[phase]["vdc_r0"])
    if any(value != sampled["loaded"] for value in sampled.values()):
        raise HandoffRejected("mmio_sampled", f"sampled VDC state changed: {sampled}")


def parse_labels(path: Path) -> dict:
    found = {}
    for address, name in re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", path.read_text(), re.M):
        found[name] = int(address, 16)
    return found


def build_handoff(output: Path, mode: str):
    """Assemble and link the handoff ROM; return (rom path, ROM labels, payload size, trace labels path)."""
    violations = fixture_static_violations((ROOT / "tests/fixtures/handoff-probe.s").read_text(encoding="ascii"))
    if violations:
        raise ValueError(f"handoff fixture static violations: {violations}")
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
    elif kind == "marker_outside_ram":     # payload sta $2220 -> sta $0000: a marker write into the MPR0 hardware page
        off = labels["handoff_payload_rom"] - ROM_BASE
        if bytes(data[off:off + 5]) != PAYLOAD_PREFIX[:5]:  # lda #$A5 / sta $2220
            raise ValueError("mutation anchor mismatch (payload marker STA)")
        data[off + 3:off + 5] = b"\x00\x00"
    else:
        raise ValueError(f"unknown mutation {kind}")
    return bytes(data)


def judge(records, labels, payload):
    """Accept only if cold AND warm entered the RAM payload under the contract and returned.

    Per cycle the order is: MPR0/1 at handoff_jump, PC/SP at handoff_jump, presence and contract of
    handoff_entry, then the phase order, then handoff_done; then the 64-byte dump/marker, the full-RAM
    side effects (cold and warm), register preservation, and the sampled VDC state. The RAM check runs
    before register preservation so the extra-copy negative keeps its own reason key. Raises HandoffRejected
    with a reason key.
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
        if done["pc"] != labels["handoff_done"] or done["mpr"][7] != 0 or done["mpr"][:2] != [IO_BANK, WORK_BANK]:
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
    nonreturning = [row for row in records["OBS"] if row["phase"] == "nonreturning"]
    if len(nonreturning) != 1 or nonreturning[0]["pc"] != labels["handoff_done"] or nonreturning[0]["sp"] != 0xFF:
        raise HandoffRejected("end", "nonreturning PC/SP mismatch")
    check_ram_side_effects(records, payload)
    check_register_preservation(records, payload)
    check_mmio_sampled(records)
    check_stack_unchanged(records)


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
    # An evidence file from an earlier run whose recorded probe digest no longer matches the source is STALE: fail.
    check_evidence_file(out / "handoff-evidence.json", PROBE_SOURCE_PATH)
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
        # Derived from the probe source file at write time (never copied from an earlier run); the probe
        # path is recorded so the digest cannot be mistaken for a different probe build.
        "probe_source_file": "tests/integration/geargrafx_api_probe.cpp",
        "probe_source_sha256": sha256_file(PROBE_SOURCE_PATH),
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
