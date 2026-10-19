"""Additional real-core CD-DA scenarios on the existing guest probe (Issue #215, diagnostic only).

Separate execution path from geargrafx_cdda.py: it writes cdda-scenarios-evidence.json and never touches
cdda-evidence.json, so a failure here cannot erase the earlier evidence. The guest is src/cdda_probe.s, unchanged.
Each scenario runs a patched copy of the built ROM (the $D8 LBA bytes of table entry 1, the same technique as
negative 4 in geargrafx_cdda.py), so no new ROM is built or committed. Expected values come from the pinned
Geargrafx source (revision b49ae82); line references are in docs/cdseek-device-observation.md.
Scenarios: valid start LBA (A), start LBA at the medium end (B), instruction budget exhaustion (C).
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_cdda import AUDIO_SECTORS, AUDIO_START_LBA, DATA_SECTORS, verify_cdda
from cdda_verify import check_all, check_guest, check_invalid_audio_start, check_read6_on_audio, check_run
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdda import STEP_BUDGET, execute
from make_cd_image import CUE_NAME, build_image

VALID_LBA = AUDIO_START_LBA + 24      # 40: inside the audio track (16..79), not its first sector
TIMEOUT_BUDGET = 1_000_000            # bounded, and well under the ~3.6M instructions a full script needs
TABLE_ENTRY_1_CDB = b"\xd8\x01\x00\x00\x00\x10"   # $D8, byte1 = play, LBA bytes 3-5 = 16 (cdda_table entry 1)
ENTRY_BYTES, CDB_OFFSET = 16, 6


def patched_rom(payload: bytes, symbols: dict, folder: Path, name: str, lba: int) -> Path:
    variant = bytearray(payload)
    at = symbols["cdda_table"] - 0xE000 + ENTRY_BYTES + CDB_OFFSET
    if bytes(variant[at:at + 6]) != TABLE_ENTRY_1_CDB:
        raise ValueError("expected the $D8 play CDB with LBA 16 as table entry 1")
    variant[at + 3:at + 6] = lba.to_bytes(3, "big")
    path = folder / name
    path.write_bytes(variant)
    return path


def timeout_rejected(run: dict) -> str:
    """The run must be an unfinished budget exhaustion, and the judge must reject it."""
    if run["reason"] != "budget" or run["halt_confirmed"] or run["step"] != TIMEOUT_BUDGET:
        raise ValueError(f"timeout scenario: budget not exhausted (reason {run['reason']}, step {run['step']})")
    try:
        check_guest(run)
    except ValueError as error:
        if "(budget)" not in str(error):
            raise
        return str(error)
    raise ValueError("timeout_budget_exhausted: judge ACCEPTED a run that did not finish")


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
    with tempfile.TemporaryDirectory(prefix="pce-cdda-scenarios-") as tmp:
        folder = Path(tmp)
        disc = build_image(folder / "mixed", DATA_SECTORS, AUDIO_SECTORS, distinct_right=True)
        cue = folder / "mixed" / CUE_NAME
        medium_sectors = disc["sectors"] + disc["audio_sectors"]
        if not AUDIO_START_LBA < VALID_LBA < medium_sectors:
            raise ValueError("valid scenario LBA is not inside the audio track")

        # A: $D8 to a valid audio LBA other than the first one. START_LBA, the seek, the PCM and the
        # PLAYING -> PAUSED -> STOPPED transitions must all follow that LBA (same judge as mixed_mode_script).
        records = execute(probe, patched_rom(payload, symbols, folder, "valid-start-not-bios.pce", VALID_LBA), cue, labels)
        observed = check_all(records, VALID_LBA)
        results["valid_audio_start_lba40"] = {"status": "PASS", "start_lba": VALID_LBA, **observed}

        # B: $D8 to the first LBA past the medium end. Pinned CommandAudioStartPosition returns GOOD without
        # StartAudio, so CD-DA state and position must not change; no seek and no sample may appear.
        invalid_lba = medium_sectors
        records = execute(probe, patched_rom(payload, symbols, folder, "invalid-start-not-bios.pce", invalid_lba), cue, labels)
        run, points = check_run(records)
        observed = {"read6_on_audio_lba": check_read6_on_audio(records, run, points),
                    "invalid_start": check_invalid_audio_start(records, points)}
        results["invalid_audio_start_lba_at_medium_end"] = {
            "status": "PASS", "start_lba": invalid_lba, "medium_sectors": medium_sectors,
            "scsi_status_message_bytes_after_d8": bytes.fromhex(run["result_hex"])[0x14:0x18].hex(),
            "scsi_phase_after_d8": points[1][0]["scsi_phase"], **observed}

        # C: the same unmodified ROM with a bounded budget that the script cannot finish. The judge must
        # reject it as budget exhaustion; a budget overrun is never a PASS.
        records = execute(probe, rom, cue, labels, budget=TIMEOUT_BUDGET)
        results["timeout_budget_exhausted"] = {
            "status": "PASS_EXPECTED_FAILURE", "budget_steps": TIMEOUT_BUDGET,
            "rejected_because": timeout_rejected(records["RUN"][0]),
            "observation": "the probe reports reason=budget; the judge rejects it, the scenario is not a PASS of the guest"}

    evidence = {
        "scope": "pinned Geargrafx SCSI-layer CD-DA scenarios driven by the existing cdda guest probe; NOT CD_PLAY ($06) and NOT the System Card ABI",
        "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
        "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"],
        "disc": {"format": "CUE/BIN, track 01 MODE1/2352 (LBA 0-15) + track 02 AUDIO (LBA 16-79), audio R = ~L",
                 "sectors": medium_sectors, "bin_sha256": disc["bin_sha256"], "cue_sha256": disc["cue_sha256"],
                 "generator": "tools/make_cd_image.py"},
        "inputs": {"valid_start_lba": VALID_LBA, "invalid_start_lba": invalid_lba, "timeout_budget": TIMEOUT_BUDGET,
                   "normal_budget": STEP_BUDGET, "patched_field": "$D8 LBA bytes 3-5 of table entry 1"},
        "tests": results,
        "cd_play_api_slot_06": "NOT_IMPLEMENTED", "system_card_cd_play_abi": "UNKNOWN",
        "real_hardware_or_second_emulator": "NOT_TESTED", "proprietary_content": "NONE",
        "status": "PASS",
    }
    (output / "cdda-scenarios-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS real Geargrafx CD-DA scenarios: valid start LBA {VALID_LBA} followed, invalid start LBA {invalid_lba} "
          f"left CD-DA unchanged, budget {TIMEOUT_BUDGET} rejected as unfinished; NOT BIOS, CD_PLAY API NOT_IMPLEMENTED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cdda scenarios FAIL/BLOCKED: {error}") from error
