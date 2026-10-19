"""Real Geargrafx CD-DA observation driven by independent guest HuC6280 code (Issue #15, diagnostic only).

The guest (src/cdda_probe.s) issues raw SCSI READ(6)/$D8/$DD/$DA/$D9 on the CD bus. The C++ frontend
only reads CdRomAudio::GetState() and the SCSI phase; tools/cdda_verify.py judges every run against the
fixture generator that also wrote the BIN (see docs/cdda-core-observation.md). Scope: pinned-emulator
behaviour of the SCSI layer. The System Card CD_PLAY/CD_PAUSE/CD_SEARCH ABI is NOT measured here and
stays UNKNOWN.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cdda import AUDIO_SECTORS, DATA_SECTORS, verify_cdda
from cdda_verify import PAUSED, PLAYING, check_all, check_playback, checkpoints, parse, pcm
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

STEP_BUDGET = 12_000_000   # a full script runs ~3.6M guest instructions


def execute(probe, rom: Path, cue: Path, labels: Path, budget: int = STEP_BUDGET) -> dict:
    result = subprocess.run([str(probe), str(rom), str(cue), str(labels), str(budget)],
                            check=True, capture_output=True, text=True, timeout=900)
    return parse(result.stdout)


def tampered_disc(directory: Path, lba: int, offset: int, length: int, change) -> Path:
    """Mixed disc whose BIN bytes [offset, offset+length) of `lba` are replaced by change(old bytes)."""
    build_image(directory, DATA_SECTORS, AUDIO_SECTORS, distinct_right=True)
    raw = bytearray((directory / BIN_NAME).read_bytes())
    at = lba * RAW_BYTES + offset
    raw[at:at + length] = change(bytes(raw[at:at + length]))
    (directory / BIN_NAME).write_bytes(raw)
    return directory / CUE_NAME


def expect_rejected(name, records, match, results, **observed):
    try:
        check_playback(records, checkpoints(records))
    except ValueError as error:
        if match not in str(error):
            raise ValueError(f"{name}: rejected for the wrong reason: {error}")
        results[name] = {"status": "PASS_EXPECTED_FAILURE", "rejected_because": str(error), **observed}
        return
    raise ValueError(f"{name}: judge ACCEPTED an invalid playback")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "cdda-probe"
    rom = output / "cdda-probe-not-bios.pce"
    payload = rom.read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    symbols, labels = manifest["symbols"], output / "trace-labels.txt"
    digest = verify_cdda(payload, base.read_bytes(), symbols)
    probe = compile_probe(output, "geargrafx_cdda_probe.cpp", "geargrafx-cdda-probe")
    results = {}
    with tempfile.TemporaryDirectory(prefix="pce-original-cdda-") as tmp:
        folder = Path(tmp)
        disc = build_image(folder / "mixed", DATA_SECTORS, AUDIO_SECTORS, distinct_right=True)
        records = execute(probe, rom, folder / "mixed" / CUE_NAME, labels)
        observed = check_all(records)
        results["mixed_mode_script"] = {"status": "PASS", **observed}

        # Negative 1: one PCM byte of a played sector changed in the BIN -> sample compare must fail there.
        # Byte 100 of LBA 20 is the low byte of sample 25's left channel.
        left, right = pcm(20, 25)
        tampered_left = int.from_bytes(((left & 0xFFFF) ^ 0x40).to_bytes(2, "little"), "little", signed=True)
        cue = tampered_disc(folder / "tampered", 20, 100, 1, lambda old: bytes((old[0] ^ 0x40,)))
        expect_rejected("tampered_pcm_byte_in_lba20", execute(probe, rom, cue, labels),
                        f"CD-DA sample at LBA 20+25 is {(tampered_left, right)}, BIN has {(left, right)}", results)

        # Negative 2: left/right of LBA 21 sample 30 swapped in the BIN (fixture R = ~L, so they differ).
        left, right = pcm(21, 30)
        cue = tampered_disc(folder / "swapped", 21, 120, 4, lambda old: old[2:] + old[:2])
        expect_rejected("channels_swapped_lba21_sample30", execute(probe, rom, cue, labels),
                        f"CD-DA sample at LBA 21+30 is {(right, left)}, BIN has {(left, right)}", results)

        # Negative 3: same script on a data-only disc: $D8 to LBA 16 targets a MODE1 sector.
        data_only = folder / "data-only"
        build_image(data_only, DATA_SECTORS + AUDIO_SECTORS, 0)
        silent = execute(probe, rom, data_only / CUE_NAME, labels)
        state = checkpoints(silent)[1][0]["audio"]["state"]
        nonzero = sum(1 for row in silent["AUD"] if row[2] or row[3])
        expect_rejected("play_on_data_track", silent, "BIN has", results,
                        cdda_state_after_d8=state, samples=len(silent["AUD"]), nonzero_samples=nonzero,
                        observation="Geargrafx enters PLAYING on a data-track LBA and outputs silence")
        if state != PLAYING or nonzero:
            raise ValueError("play_on_data_track: emulator behaviour changed; re-examine")

        # Negative 4: $D8 byte 1 = 0 -> pinned source calls StartAudio(pause=true): seek and hold, no samples.
        variant = bytearray(payload)
        at = symbols["cdda_table"] - 0xE000 + 16 + 6
        if variant[at:at + 2] != b"\xd8\x01":
            raise ValueError("expected the $D8 CDB as table entry 1")
        variant[at + 1] = 0x00
        held_rom = folder / "d8-mode0-not-bios.pce"
        held_rom.write_bytes(variant)
        held = execute(probe, held_rom, folder / "mixed" / CUE_NAME, labels)
        state = checkpoints(held)[1][0]["audio"]["state"]
        expect_rejected("d8_byte1_zero_seeks_paused", held, "expected PLAYING", results,
                        cdda_state_after_d8=state, samples=len(held["AUD"]))
        if state != PAUSED or held["AUD"]:
            raise ValueError("d8_byte1_zero_seeks_paused: emulator behaviour changed; re-examine")

    evidence = {
        "scope": "pinned Geargrafx SCSI-layer CD-DA behaviour driven by original guest code; NOT CD_PLAY ($06) and NOT the System Card ABI",
        "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "disc": {"format": "CUE/BIN, track 01 MODE1/2352 (LBA 0-15) + track 02 AUDIO (LBA 16-79), audio R = ~L",
                 "expected_pcm": "make_cd_image.audio_frame(distinct_right=True), the same function that wrote the BIN",
                 "bin_sha256": disc["bin_sha256"], "cue_sha256": disc["cue_sha256"], "generator": "tools/make_cd_image.py"},
        "observation_points": {
            "state": "GeargrafxCore::GetCDROMAudio()->GetState(): CURRENT_STATE, START_LBA, STOP_LBA, CURRENT_LBA, CURRENT_SAMPLE, STOP_EVENT, SEEK_CYCLES, PLAYBACK_DELAY_CYCLES",
            "audio_buffer": "CdRomAudio_State BUFFER[0..FRAME_SAMPLES) after each RunToVBlank step (pre-mix CD-DA samples)",
            "scsi": "$D8 start / $D9 stop / $DA pause / $DD Q-subcode, ScsiController::ExecuteCommand (scsi_controller.cpp)"},
        "tests": results,
        "cd_play_api_slot_06": "NOT_IMPLEMENTED", "system_card_cd_play_abi": "UNKNOWN",
        "real_hardware_or_second_emulator": "NOT_TESTED", "proprietary_content": "NONE",
        "status": "PASS",
    }
    (output / "cdda-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS real Geargrafx CD-DA observation: {observed['cdda_play']['samples_verified_against_bin']} samples "
          f"matched audio_frame() (the BIN's own generator), READ(6)-on-audio kept IDLE, pause/stop held position, "
          f"{sum(r['status'] == 'PASS_EXPECTED_FAILURE' for r in results.values())} real-core negatives rejected; "
          f"NOT BIOS, CD_PLAY API NOT_IMPLEMENTED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdda integration FAIL/BLOCKED: {error}") from error
