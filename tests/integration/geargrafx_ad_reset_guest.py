#!/usr/bin/env python3
"""Issue #295: project-own ad_reset_idle guest run on the pinned Geargrafx core (opt-in, local).

The routine (src/ad_reset_idle.s) and its driver (tests/fixtures/ad_reset_guest.s) are assembled with
cc65 and executed as CPU code by the GPL-linked probe (geargrafx_ad_reset_guest.cpp). Synthetic BIOS/CD
only. No System Card AD_RESET runs, nothing is wired into the jump table or slot $10, and nothing here is
a BIOS-compatibility or release claim (release_eligible=false).

Cases (guest-observed, bus-visible results): pos = reset from idle must return C=0/A=0;
neg = reset with the play bit kept must NOT return success (C=1, A has the play bit).
Register/stack/MPR regression is judged from the guest's own snapshots.
Mutation: two falsified copies of the routine (failure path reports success; X/Y restore order swapped)
must be rejected by the same judge.
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
RESULT_BYTES = 0x80
RECORD = 32
DONE_OFFSET = 0x40
DONE = 0xA5
STATUS_MASK = 0x8D
STATUS_PLAY = 0x08
PLAY_RESET_CONTROL = 0xA0
# Tail of ad_reset_idle: sec; bra +1 (skips clc); clc; ply; plx; rts
TAIL = bytes((0x38, 0x80, 0x01, 0x18, 0x7A, 0xFA, 0x60))
POS_VERDICT = "PASS_IDLE_AFTER_RESET_FROM_IDLE"
NEG_VERDICT = "NOT_IDLE_RESET_WITH_PLAY_BIT_SET"
EXIT_FAIL = 1


def build_guest(output):
    """Assemble driver + routine with cc65 (driver first, so the entry is $3E00); return (bin, labels)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ca65, ld65 = executable("ca65"), executable("ld65")
    objects = []
    for source in (ROOT / "tests/fixtures/ad_reset_guest.s", ROOT / "src/ad_reset_idle.s"):
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
    return binary, symbols


def run_probe(probe, bios, cue, guest):
    result = subprocess.run([str(probe), str(bios), str(cue), str(guest)], timeout=300,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"probe exit {result.returncode}: {result.stderr[-300:]}")
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    if not lines:
        raise ValueError("probe emitted no observation on stderr")
    row = json.loads(lines[-1])
    if row.get("stop_pc") is None or not GUEST_BASE <= row["stop_pc"] < GUEST_BASE + 0x200:
        raise ValueError(f"guest did not halt inside its code: stop_pc={row.get('stop_pc')}")
    return bytes.fromhex(row["result_hex"]), row["final"]


def _regression(record, label):
    if record[2] != 0xA5 or record[3] != 0x3C:
        raise ValueError(f"{label}: X/Y not preserved (X={record[2]:#x}, Y={record[3]:#x})")
    if record[4] != record[5]:
        raise ValueError(f"{label}: stack pointer changed ({record[4]:#x} -> {record[5]:#x})")
    if record[8:16] != record[16:24]:
        raise ValueError(f"{label}: MPR0-7 changed across the call")


def judge(result, final):
    """Return verdicts per case; raise ValueError on any unexpected guest observation."""
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


def mutated(guest, edit):
    """Return a falsified copy of the guest binary with one byte edit inside ad_reset_idle."""
    data = bytearray(guest.read_bytes())
    at = data.find(TAIL)
    if at < 0 or data.find(TAIL, at + 1) >= 0:
        raise ValueError("mutation point is not unique")
    edit(data, at)
    return data


def main():
    with tempfile.TemporaryDirectory(prefix="pce-ad-reset-guest-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_ad_reset_guest.cpp",
                              target_name="ad-reset-guest-probe")
        guest, _symbols = build_guest(directory / "guest")
        result, final = run_probe(probe, bios, cue, guest)
        verdicts = judge(result, final)
        # Mutation 1: failure path reports success (sec -> clc).
        m1 = directory / "mutant-success-on-fail.bin"
        m1.write_bytes(mutated(guest, lambda d, at: d.__setitem__(at, 0x18)))
        r1, f1 = run_probe(probe, bios, cue, m1)
        try:
            judge(r1, f1)
        except ValueError:
            pass
        else:
            raise ValueError("mutation FAILED: falsified success-on-failure was accepted")
        # Mutation 2: X/Y restore order swapped (ply/plx -> plx/ply): register regression must fire.
        def swap(d, at):
            d[at + 4], d[at + 5] = d[at + 5], d[at + 4]
        m2 = directory / "mutant-xy-order.bin"
        m2.write_bytes(mutated(guest, swap))
        r2, f2 = run_probe(probe, bios, cue, m2)
        try:
            judge(r2, f2)
        except ValueError:
            pass
        else:
            raise ValueError("mutation FAILED: X/Y register regression was accepted")
        digest = hashlib.sha256(guest.read_bytes()).hexdigest()
        for case in ("pos-reset", "neg-reset-playing"):
            print("EVIDENCE " + json.dumps({
                "case": case, "observation_kind": "REAL_CORE_GUEST_CPU", "bios": "SYNTHETIC",
                "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "guest_sha256": digest, "verdict": verdicts[case]}, sort_keys=True))
        print(f"pos-reset: A={result[0]:#x} C={result[1]} -> {verdicts['pos-reset']}")
        print(f"neg-reset-playing: A={result[RECORD]:#x} C={result[RECORD + 1]} -> {verdicts['neg-reset-playing']}")
        print("mutation: success-on-failure and X/Y order rejected")
    print("PASS issue-295 ad_reset_idle guest (pinned core, synthetic BIOS/CD): idle reset, play-bit refusal, "
          "register/stack/MPR regression")
    print("LIMIT: device-layer observation of the pinned core only; no BIOS AD_RESET executed; "
          "busy-from-transfer not exercised; no slot wiring; release_eligible=false.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL issue-295 ad_reset_idle guest: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from error
