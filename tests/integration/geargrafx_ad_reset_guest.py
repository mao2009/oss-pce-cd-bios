#!/usr/bin/env python3
"""Issue #295: project-own ad_reset_idle guest run on the pinned Geargrafx core (opt-in, local).

The routine (src/ad_reset_idle.s) and its driver are assembled with cc65 and executed as CPU code by the
GPL-linked probe (geargrafx_ad_reset_guest.cpp). Synthetic BIOS/CD only. No System Card AD_RESET runs,
nothing is wired into the jump table or slot $10, and nothing here is a BIOS-compatibility or release claim
(release_eligible=false).

What the guest observes (C/A are STATUS-BASED IDLE, not device-idle): the routine polls $180C 256 times and
returns C=0/A=0 only if status & $8D == 0 at a poll. PLAYING is not readable by the guest, so it is judged
host-side from the core's internal ADPCM state (Adpcm::GetState) after each run. ADDRESS is internal too.

Three real-core stages (each a separate probe run):
  1. preset: ADDRESS is set to $1234 from idle; the host checks the internal ADDRESS really is $1234
     (otherwise a later "cleared" result would be vacuous).
  2. pos-only: ad_reset_idle with CONTROL $80 from idle; the host checks internal PLAYING=0, ADDRESS=0,
     CONTROL=$80 and status $00 after the reset.
  3. full fixture (tests/fixtures/ad_reset_guest.s): pos case (guest A=0/C=0 and register/stack/MPR
     regression) and neg case (play bit kept: C=1, A has the play bit, internal PLAYING still 1).

Mutation gate (all on the full fixture, each must be rejected by the judge): success-on-fail (sec->clc),
forged reset-success (A=0, C=0 while PLAYING stays 1), X/Y restore order swapped, PHX or PHY dropped
(S leak), PLX or PLY dropped (saved-register destruction, X or Y left at its unsaved value), MPR6 corrupted.
Not gated: wrong poll count and uninitialized X. On the pinned core both give the same bus-visible results
(busy stays busy), so the judge cannot see them; they are not claimed as covered.
"""
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
from geargrafx_reference_guard import synthetic_media  # noqa: E402

GUEST_BASE = 0x3E00
GUEST_MAX = 0x200
RESULT_BYTES = 0x80
RECORD = 32
DONE_OFFSET = 0x40
DONE = 0xA5
STATUS_MASK = 0x8D
STATUS_PLAY = 0x08
PRESET_ADDRESS = 0x1234
PLAY_RESET_CONTROL = 0xA0
IDLE_RESET_CONTROL = 0x80
POS_VERDICT = "PASS_IDLE_AFTER_RESET_FROM_IDLE"
POS_DEVICE_VERDICT = "PASS_PLAYING0_ADDRESS0_AFTER_RESET"
NEG_VERDICT = "NOT_IDLE_RESET_WITH_PLAY_BIT_SET"
EXIT_FAIL = 1

# Hand encoding of src/ad_reset_idle.s (HuC6280). build_guest() checks that the linked binary ends with it.
HEAD = [0xDA, 0x5A, 0x09, 0x80, 0x8D, 0x0D, 0x18, 0xA2, 0x00,        # phx; phy; ora #$80; sta $180D; ldx #0
        0xAD, 0x0C, 0x18, 0x29, 0x8D, 0xF0, 0x06, 0xCA, 0xD0, 0xF6]  # poll: lda $180C; and #$8D; beq idle; dex; bne poll
TAIL = [0x38, 0x80, 0x01, 0x18, 0x7A, 0xFA, 0x60]                    # sec; bra +1; clc; ply; plx; rts
ROUTINE = bytes(HEAD + TAIL)
# Forged variant: busy path also loads A=0 and clears C, so the branch to @idle moves 2 bytes further.
FORGED_HEAD = HEAD[:14] + [0xF0, 0x08] + HEAD[16:]

MUTANTS = {
    "success-on-fail": HEAD + [0x18, 0x80, 0x01, 0x18, 0x7A, 0xFA, 0x60],
    "forged-reset-success": FORGED_HEAD + [0xA9, 0x00, 0x18, 0x80, 0x01, 0x18, 0x7A, 0xFA, 0x60],
    "xy-order-swap": HEAD + [0x38, 0x80, 0x01, 0x18, 0xFA, 0x7A, 0x60],
    "s-leak-phx-dropped": [0xEA] + HEAD[1:] + TAIL,
    "saved-x-destroyed": [0xEA] + HEAD[1:] + [0x38, 0x80, 0x01, 0x18, 0x7A, 0xEA, 0x60],
    "saved-y-destroyed": HEAD[:1] + [0xEA] + HEAD[2:] + [0x38, 0x80, 0x01, 0x18, 0xA0, 0x00, 0xFA, 0x60],
    "mpr6-corrupted": HEAD + [0x38, 0x80, 0x01, 0x18, 0x7A, 0xFA, 0x48, 0xA9, 0xF7, 0x53, 0x40, 0x68, 0x60],
}

PRESET_DRIVER = """\
.setcpu "huc6280"
.import ad_reset_idle
.export ad_guest_entry
AD_ADDR_LO = $1808
AD_ADDR_HI = $1809
ad_guest_entry:
    sei
    cld
    ldx #$FF
    txs
    lda #$FF
    tam #$01
    lda #$F8
    tam #$02
    lda #$34
    sta AD_ADDR_LO
    lda #$12
    sta AD_ADDR_HI
@halt:
    bra @halt
"""
POS_DRIVER = PRESET_DRIVER.replace("@halt:", """    lda #$80
    ldx #$A5
    ldy #$3C
    jsr ad_reset_idle
@halt:""")


def build_guest(output, driver=None):
    """Assemble driver + routine with cc65 (driver first, so the entry is $3E00); return the linked binary."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    sources = [Path(driver) if driver else ROOT / "tests/fixtures/ad_reset_guest.s", ROOT / "src/ad_reset_idle.s"]
    objects = []
    for source in sources:
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, "--cpu", "huc6280", "-o", str(obj), str(source)], check=True)
        objects.append(str(obj))
    binary, labels = output / "ad-reset-guest.bin", output / "ad-reset-guest.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "tests/fixtures/ad_reset_guest.cfg"), "-Ln", str(labels),
                    "-o", str(binary), *objects], check=True)
    symbols = {name: int(address, 16) for address, name in
               re.findall(r"^al ([0-9A-Fa-f]{6}) \.(\w+)$", labels.read_text(), re.M)}
    if symbols.get("ad_guest_entry") != GUEST_BASE or "ad_reset_idle" not in symbols:
        raise ValueError("guest layout mismatch: entry must be $3E00 and ad_reset_idle must link")
    if not binary.read_bytes().endswith(ROUTINE):
        raise ValueError("routine encoding mismatch: linked ad_reset_idle differs from the hand encoding")
    return binary


def run_probe(probe, bios, cue, guest):
    """Return (result bytes, internal final state, stop_pc). Halting and marker checks belong to the judge."""
    result = subprocess.run([str(probe), str(bios), str(cue), str(guest)], timeout=300,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"probe exit {result.returncode}: {result.stderr[-300:]}")
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    if not lines:
        raise ValueError("probe emitted no observation on stderr")
    row = json.loads(lines[-1])
    return bytes.fromhex(row["result_hex"]), row["final"], row.get("stop_pc")


def _halted(stop_pc):
    if stop_pc is None or not GUEST_BASE <= stop_pc < GUEST_BASE + GUEST_MAX:
        raise ValueError(f"guest did not halt inside its code: stop_pc={stop_pc}")


def judge_preset(final, stop_pc):
    """Stage 1: the ADDRESS preset must be visible in the core, or the reset check is vacuous."""
    _halted(stop_pc)
    if final["address"] != PRESET_ADDRESS or final["playing"] != 0 or final["control"] != 0:
        raise ValueError(f"preset not established: address={final['address']:#x} "
                         f"playing={final['playing']} control={final['control']:#x}")


def judge_pos_device(final, stop_pc):
    """Stage 2: after reset from idle the core's internal PLAYING, ADDRESS, CONTROL and status are cleared."""
    _halted(stop_pc)
    if final["control"] != IDLE_RESET_CONTROL:
        raise ValueError(f"pos-reset device: control={final['control']:#x}, expected {IDLE_RESET_CONTROL:#x}")
    if final["playing"] != 0:
        raise ValueError("pos-reset device: PLAYING still 1 after reset from idle")
    if final["address"] != 0:
        raise ValueError(f"pos-reset device: ADDRESS not cleared (address={final['address']:#x})")
    if final["status"] & STATUS_MASK:
        raise ValueError(f"pos-reset device: status {final['status']:#x} not idle")


def _regression(record, label):
    if record[2] != 0xA5 or record[3] != 0x3C:
        raise ValueError(f"{label}: X/Y not preserved (X={record[2]:#x}, Y={record[3]:#x})")
    if record[4] != record[5]:
        raise ValueError(f"{label}: stack pointer changed ({record[4]:#x} -> {record[5]:#x})")
    if record[8:16] != record[16:24]:
        raise ValueError(f"{label}: MPR0-7 changed across the call")


def judge(result, final, stop_pc):
    """Stage 3 (full fixture). Return verdicts per case; raise ValueError on any unexpected observation."""
    _halted(stop_pc)
    if len(result) != RESULT_BYTES or result[DONE_OFFSET] != DONE:
        raise ValueError("guest did not reach its done marker")
    pos, neg = result[0:RECORD], result[RECORD:2 * RECORD]
    if pos[6] & STATUS_MASK:
        raise ValueError(f"pos-reset: precondition not idle (status {pos[6]:#x})")
    if (pos[0], pos[1]) != (0, 0):
        raise ValueError(f"pos-reset: routine returned A={pos[0]:#x} C={pos[1]}, expected idle A=0 C=0")
    _regression(pos, "pos-reset")
    if neg[7] != 1 or not neg[6] & STATUS_PLAY:
        raise ValueError(f"neg-reset-playing: playback not started before the reset (status {neg[6]:#x})")
    if neg[1] != 1:
        raise ValueError("neg-reset-playing: routine claimed success while playback continues")
    if not neg[0] & STATUS_PLAY or neg[0] & ~STATUS_MASK & 0xFF:
        raise ValueError(f"neg-reset-playing: failure value A={neg[0]:#x} does not report the play bit")
    _regression(neg, "neg-reset-playing")
    if final["control"] != PLAY_RESET_CONTROL or final["address"] != 0:
        raise ValueError(f"final device state: control={final['control']:#x} address={final['address']:#x}")
    if final["playing"] != 1:
        raise ValueError("final device state: playback stopped, the play-bit reset case was not kept")
    return {"pos-reset": POS_VERDICT, "neg-reset-playing": NEG_VERDICT}


def with_routine(guest, routine):
    """Return the linked guest with its ad_reset_idle bytes (always the tail) replaced by a mutant routine."""
    return guest.read_bytes()[:-len(ROUTINE)] + bytes(routine)


def main():
    with tempfile.TemporaryDirectory(prefix="pce-ad-reset-guest-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_ad_reset_guest.cpp",
                              target_name="ad-reset-guest-probe")
        (directory / "preset-driver.s").write_text(PRESET_DRIVER, encoding="ascii")
        (directory / "pos-driver.s").write_text(POS_DRIVER, encoding="ascii")
        preset_bin = build_guest(directory / "preset", directory / "preset-driver.s")
        judge_preset(*run_probe(probe, bios, cue, preset_bin)[1:])
        pos_bin = build_guest(directory / "pos", directory / "pos-driver.s")
        judge_pos_device(*run_probe(probe, bios, cue, pos_bin)[1:])
        guest = build_guest(directory / "guest")
        result, final, stop_pc = run_probe(probe, bios, cue, guest)
        verdicts = judge(result, final, stop_pc)
        for name, routine in MUTANTS.items():
            mutant = directory / f"mutant-{name}.bin"
            mutant.write_bytes(with_routine(guest, routine))
            try:
                judge(*run_probe(probe, bios, cue, mutant))
            except ValueError as error:
                print(f"mutation rejected: {name}: {error}")
            else:
                raise ValueError(f"mutation FAILED: {name} was accepted by the judge")
        digest = hashlib.sha256(guest.read_bytes()).hexdigest()
        for case, verdict in (("pos-reset", verdicts["pos-reset"]), ("pos-reset-device", POS_DEVICE_VERDICT),
                              ("neg-reset-playing", verdicts["neg-reset-playing"])):
            print("EVIDENCE " + json.dumps({
                "case": case, "observation_kind": "REAL_CORE_GUEST_CPU", "bios": "SYNTHETIC",
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "guest_sha256": digest, "verdict": verdict}, sort_keys=True))
        print(f"pos-reset: A={result[0]:#x} C={result[1]} -> {verdicts['pos-reset']}")
        print(f"pos-reset device (host, internal): PLAYING=0 ADDRESS=0 -> {POS_DEVICE_VERDICT}")
        print(f"neg-reset-playing: A={result[RECORD]:#x} C={result[RECORD + 1]} -> {verdicts['neg-reset-playing']}")
    print("PASS issue-295 ad_reset_idle guest (pinned core, synthetic BIOS/CD): status-based idle reset, "
          "PLAYING/ADDRESS cleared host-side, play-bit refusal, register/stack/MPR regression, mutants rejected")
    print("LIMIT: device-layer observation of the pinned core only; C/A are status-based idle (PLAYING checked "
          "host-side); no BIOS AD_RESET executed; busy-from-transfer not exercised; poll count not observable; "
          "no slot wiring; release_eligible=false.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL issue-295 ad_reset_idle guest: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from error
