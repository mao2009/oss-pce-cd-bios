"""Real Geargrafx synthetic CUE track-selection diagnostic (Issue #607 follow-up).

The host selects only a CUE-analyzed MODE1/2352 INDEX 01 LBA, then the original
guest READ(6) diagnostic reads that absolute LBA.  Geargrafx has no confirmed
guest TOC selection surface used here, so this is not a BIOS command or boot
implementation.  Audio-only rejection is host selection refusal; the paired
real-core observation records that raw READ(6) itself does not provide it.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_cdread import verify_cdread, with_request
from cdread_verify import check_success, describe_failure
from environment import DEPENDENCIES
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import CUE_NAME as READ_CUE_NAME, build_image
from synthetic_boot_media import CUE_NAME, write_case
from synthetic_track_select import NoDataTrackError, select_first_mode1_track


def run_selected(probe, rom, labels, symbols, cue, selected):
    """Run and fully judge one selected INDEX 01 sector on cold and warm reset."""
    variant = with_request(rom, symbols, selected["lba"], 1)
    path = cue.parent / "selected-read-not-bios.pce"
    path.write_bytes(variant)
    records = execute(probe, path, cue, labels)
    expected = selected["report"]
    from cue_analysis import user_sector
    # check_success indexes by absolute LBA; only the selected element is used.
    disc = [b""] * selected["lba"] + [user_sector(cue, expected, selected["lba"])]
    check_success(records, symbols, disc, selected["lba"], 1)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    output = ROOT / "build" / args.mode / "cdread-probe"
    rom = (output / "cdread-probe-not-bios.pce").read_bytes()
    manifest = json.loads((output / "manifest.json").read_text())
    labels, symbols = output / "trace-labels.txt", manifest["symbols"]
    base = ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce"
    digest = verify_cdread(rom, base.read_bytes(), symbols, manifest["code_sha256_excluding_request"])
    probe = compile_probe(output, "geargrafx_cdread_probe.cpp", "geargrafx-synthetic-track-select-probe")
    results = {}
    with tempfile.TemporaryDirectory(prefix="pce-synthetic-track-select-") as tmp:
        root = Path(tmp)
        # Single data track at LBA 0.
        single = root / "single"
        build_image(single)
        selected = select_first_mode1_track(single / READ_CUE_NAME)
        records = run_selected(probe, rom, labels, symbols, single / READ_CUE_NAME, selected)
        results["single_data_track"] = {"status": "PASS", "track": selected["track"], "lba": selected["lba"],
                                        "cycles": [r["cycle"] for r in records["RUN"]]}

        # Known mixed-mode CUE with 225 stored INDEX 00 pregap frames.  The selected
        # start is INDEX 01, never its audio predecessor or data pregap.
        mixed = root / "mixed-pregap"
        write_case(mixed, "NONE", "V2")
        selected = select_first_mode1_track(mixed / CUE_NAME)
        records = run_selected(probe, rom, labels, symbols, mixed / CUE_NAME, selected)
        track = selected["report"]["tracks"][1]
        if track["pregap_start_lba"] == selected["lba"]:
            raise ValueError("stored pregap was selected as user data")
        results["mixed_audio_data_index01_after_pregap"] = {
            "status": "PASS", "track": selected["track"], "lba": selected["lba"],
            "pregap_start_lba": track["pregap_start_lba"], "pregap_frames": track["pregap_frames"],
            "cycles": [r["cycle"] for r in records["RUN"]],
        }

        # Audio-only media is loaded by the same real core, but CUE selection refuses
        # it before any putative data-track READ(6).  The explicit audio read below
        # pins Geargrafx's raw-frame behaviour and proves it is not a rejection API.
        audio = root / "audio-only"
        write_case(audio, "NO_TRACK", "V1")
        try:
            select_first_mode1_track(audio / CUE_NAME)
        except NoDataTrackError as error:
            selection_reason = str(error)
        else:
            raise ValueError("audio-only CUE unexpectedly selected a data track")
        audio_rom = with_request(rom, symbols, 0, 1)
        audio_path = audio / "audio-read-not-bios.pce"
        audio_path.write_bytes(audio_rom)
        audio_records = execute(probe, audio_path, audio / CUE_NAME, labels)
        expected_audio = (audio / "synthetic-boot.bin").read_bytes()[16:16 + 2048]
        for run, failure in zip(audio_records["RUN"], describe_failure(audio_records)):
            got = bytes.fromhex(run["buffer_hex"])[:2048]
            if failure != ("cd_halt_ok", 0, 9, 2048) or got != expected_audio:
                raise ValueError(f"audio-only core observation changed: {run['cycle']} {failure}")
        results["audio_only_rejected_by_selection"] = {
            "status": "PASS_EXPECTED_FAILURE", "rejected_because": selection_reason,
            "core_observation": "Geargrafx READ(6) returned raw audio-frame bytes; it did not reject audio",
            "cycles": [r["cycle"] for r in audio_records["RUN"]],
        }

    evidence = {"scope": "synthetic CUE host selection plus guest READ(6) only; no BIOS TOC command or API",
                "rom_sha256": digest, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"], "tests": results,
                "cd_read_api_slot_03": "NOT_IMPLEMENTED", "bios_track_selection": "NOT_IMPLEMENTED",
                "real_hardware_or_second_emulator": "NOT_TESTED", "proprietary_content": "NONE", "status": "PASS"}
    (output / "synthetic-track-select-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx synthetic CUE selection: single/mixed/pregap cold+warm; audio-only host rejection; NOT BIOS")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"synthetic track selection FAIL/BLOCKED: {error}") from error
