#!/usr/bin/env python3
"""Issue #294: ADPCM reset device-layer diagnostic on the pinned real core (diagnostic only).

Pinned Geargrafx instruction-debugger core (local GPL-3.0 link, b49ae82a012eade566d72e9d61c9d95a5caa4da3;
no core code copied here). Synthetic BIOS/CD and a project-owned BRA-* guest only. No System Card
AD_RESET is executed, so this is NOT a BIOS API result and NOT a System Card compatibility claim.

Observed device behaviour (pinned core source, adpcm_inline.h): CONTROL bit7 makes SoftReset run on
every clock (ADDRESS/READ/WRITE/LENGTH cleared, END/HALF IRQ cleared), but PLAYING is set from CONTROL
bit5 in the same path, so a reset written with the play bit still set leaves playback running.

Cases:
* ``pos-reset``: from idle, a reset write (CONTROL=$80) must reach an idle state (not playing, no busy, no IRQ).
* ``neg-reset-playing``: reset written while playing (CONTROL=$A0) must NOT be judged idle. The core keeps
  PLAYING=1; the diagnostic records it and the idle predicate must reject it.

Run manually (not wired into the Makefile):
    python3 tests/integration/geargrafx_ad_reset.py
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))
from environment import DEPENDENCIES  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_reference_guard import synthetic_media  # noqa: E402

PRESET_ADDRESS = 0x1234
SAMPLE_RATE_DEFAULT = 0x0F
BUSY_IRQ_PLAY_MASK = 0x8D  # status: play 0x08, end IRQ 0x01, read busy 0x80, write busy 0x04
PLAY_BIT = 0x20
RESET_IDLE = 0x80
RESET_WITH_PLAY = 0xA0
POS_VERDICT = "PASS_IDLE_AFTER_RESET_FROM_IDLE"
NEG_VERDICT = "NOT_IDLE_RESET_WITH_PLAY_BIT_SET"
EXIT_FAIL = 1


def idle(post):
    """Idle predicate for the post-reset snapshot: no playback, no busy, no pending END IRQ."""
    return post["playing"] == 0 and (post["status"] & BUSY_IRQ_PLAY_MASK) == 0


def _ints(row, section, keys):
    for key in keys:
        value = row[section][key]
        if type(value) is not int:
            raise ValueError(f"{section}.{key} is not an integer: {value!r}")


SNAP_KEYS = ("control", "playing", "status", "dma", "sample_rate", "address", "read_address",
             "write_address", "length", "end_irq", "half_irq")


def _common(row, mode):
    if row.get("mode") != mode:
        raise ValueError(f"{mode}: measured mode {row.get('mode')!r}")
    if row.get("status") != "ok":
        raise ValueError(f"{mode}: observation not ok ({row.get('status')})")
    _ints(row, "pre", SNAP_KEYS)
    _ints(row, "post", SNAP_KEYS)


def check_pos(row):
    """Reset from idle: every reset-cleared register/state must read back cleared, unchanged fields kept."""
    _common(row, "pos-reset")
    pre, post = row["pre"], row["post"]
    if pre["playing"] != 0 or pre["address"] != PRESET_ADDRESS:
        raise ValueError(f"pos-reset: precondition not met (playing={pre['playing']}, address={pre['address']:#x})")
    if post["control"] != RESET_IDLE:
        raise ValueError(f"pos-reset: CONTROL readback {post['control']:#x}, expected {RESET_IDLE:#x}")
    if not idle(post):
        raise ValueError(f"pos-reset: not idle after reset (playing={post['playing']}, status={post['status']:#x})")
    for key in ("address", "read_address", "write_address", "length", "end_irq", "half_irq"):
        if post[key] != 0:
            raise ValueError(f"pos-reset: {key} not cleared by reset ({post[key]:#x})")
    if post["sample_rate"] != SAMPLE_RATE_DEFAULT or post["dma"] != pre["dma"]:
        raise ValueError("pos-reset: sample rate or DMA register changed by reset")
    return POS_VERDICT


def check_neg(row):
    """Reset with the play bit set: the core keeps playing, so the post-reset state must NOT be idle."""
    _common(row, "neg-reset-playing")
    if row.get("frames_to_playing", 0) <= 0:
        raise ValueError("neg-reset-playing: playback never started (precondition not met)")
    pre, post = row["pre"], row["post"]
    if pre["control"] != PLAY_BIT or pre["playing"] != 1:
        raise ValueError(f"neg-reset-playing: precondition control={pre['control']:#x} playing={pre['playing']}")
    if post["control"] != RESET_WITH_PLAY or post["playing"] != 1 or (post["status"] & 0x08) != 0x08:
        raise ValueError(f"neg-reset-playing: observed post state differs from the pinned core "
                         f"(control={post['control']:#x}, playing={post['playing']}, status={post['status']:#x})")
    if post["address"] != 0:
        raise ValueError(f"neg-reset-playing: ADDRESS not cleared by reset ({post['address']:#x})")
    if idle(post):
        raise ValueError("neg-reset-playing: post-reset state judged idle while PLAYING=1")
    return NEG_VERDICT


def mutation_test(pos_row, neg_row):
    """Falsified observations must be rejected by the classifiers or the idle predicate."""
    if idle(neg_row["post"]) or not idle(pos_row["post"]):
        raise ValueError("mutation test FAILED: idle predicate does not separate pos from neg")
    falsified = copy.deepcopy(neg_row)
    falsified["post"]["playing"] = 0
    falsified["post"]["status"] &= ~0x08
    try:
        check_neg(falsified)
    except ValueError:
        pass
    else:
        raise ValueError("mutation test FAILED: neg case accepted with playback stopped")
    pos_playing = copy.deepcopy(pos_row)
    pos_playing["post"]["playing"] = 1
    pos_playing["post"]["status"] |= 0x08
    try:
        check_pos(pos_playing)
    except ValueError:
        pass
    else:
        raise ValueError("mutation test FAILED: pos case accepted with playback still running")
    pos_skipped = copy.deepcopy(pos_row)
    pos_skipped["post"]["address"] = PRESET_ADDRESS
    try:
        check_pos(pos_skipped)
    except ValueError:
        return
    raise ValueError("mutation test FAILED: pos case accepted with ADDRESS not cleared")


def run_case(probe, bios, cue, mode):
    result = subprocess.run([str(probe), str(bios), str(cue), mode], timeout=300, capture_output=True,
                            text=True)
    if result.returncode != 0:
        raise ValueError(f"{mode}: probe exit {result.returncode}: {result.stderr[-300:]}")
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    if not lines:
        raise ValueError(f"{mode}: no observation on stderr")
    return json.loads(lines[-1])


def main():
    with tempfile.TemporaryDirectory(prefix="pce-ad-reset-") as tmp:
        directory = Path(tmp)
        bios, cue = synthetic_media(directory)
        probe = compile_probe(directory, source_name="geargrafx_ad_reset.cpp", target_name="ad-reset-probe")
        pos_row = run_case(probe, bios, cue, "pos-reset")
        neg_row = run_case(probe, bios, cue, "neg-reset-playing")
        verdicts = {"pos-reset": check_pos(pos_row), "neg-reset-playing": check_neg(neg_row)}
        mutation_test(pos_row, neg_row)
        for case, row in (("pos-reset", pos_row), ("neg-reset-playing", neg_row)):
            print("EVIDENCE " + json.dumps({"case": case, "observation_kind": "REAL_CORE_DEVICE_LAYER",
                                            "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                                            "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
                                            "bios": "SYNTHETIC",
                                            "verdict": verdicts[case], "row": row}, sort_keys=True))
            print(f"{case}: frames_to_playing={row['frames_to_playing']} post_control={row['post']['control']:#x} "
                  f"post_playing={row['post']['playing']} post_status={row['post']['status']:#x} "
                  f"post_address={row['post']['address']:#x} -> {verdicts[case]}")
    print("PASS issue-294 ADPCM reset device-layer diagnostic (pinned core, synthetic BIOS/CD):")
    print("  pos-reset: reset from idle reaches idle; reset-cleared state reads back cleared")
    print("  neg-reset-playing: reset with play bit keeps PLAYING=1 and is refused by the idle predicate")
    print("  mutation: falsified observations rejected")
    print("LIMIT: device-layer observation of the pinned core only; no BIOS AD_RESET executed; "
          "no slot wiring; release_eligible=false.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL issue-294 ADPCM reset diagnostic: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL) from error
