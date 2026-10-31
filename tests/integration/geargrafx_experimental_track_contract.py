"""Pinned real-core test for the DEV-only host-selected synthetic data-track contract.

The host analyses original synthetic CUE files and embeds the resulting INDEX 01
LBA in each experimental ROM. The guest validates that record, then uses its
internal variable READ(6) helper. It does not read a TOC and is not a BIOS API.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(Path(__file__).resolve().parent)]

from build_cdload_exec import build_exec_disc, encode_sector
from build_experimental_bios_boot import ROM_NAME, build_experimental_bios_boot
from cdread_verify import PHASE_DATA_IN, check_halt_confirmation, result_block
from environment import DEPENDENCIES
from experimental_track_selection import SelectionError, selection_from_cue
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
from synthetic_boot_media import BIN_NAME as MIXED_BIN, CUE_NAME as MIXED_CUE, USER_OFFSET, write_case

STEP_BUDGET = 12_000_000
BUFFER_BASE, MARK_OFF = 0x2800, 0x3F00 - 0x2800
MARKER = (0xA5, 0x5A, 0xF8, 0xFF, 0x3C)


def patch_selected_payload(cue, lba, code, bin_name=BIN_NAME):
    raw = bytearray((cue.parent / bin_name).read_bytes())
    raw[lba * RAW_BYTES + USER_OFFSET:lba * RAW_BYTES + USER_OFFSET + 2048] = encode_sector(lba, code)
    (cue.parent / bin_name).write_bytes(raw)


def assert_success(records, symbols, code, lba):
    if [r["cycle"] for r in records["RUN"]] != ["cold", "warm"]:
        raise ValueError("missing cold/warm observations")
    for run in records["RUN"]:
        if run["reason"] != "cd_halt_ok" or run["pc"] != symbols["cd_halt_ok"]:
            raise ValueError(f"{run['cycle']}: did not complete load/handoff: {run['reason']} at {run['pc']:#x}, {result_block(run)}")
        check_halt_confirmation(run)
        if result_block(run)["bytes"] != 2048:
            raise ValueError(f"{run['cycle']}: incomplete selected-sector transfer")
        buffer = bytes.fromhex(run["buffer_hex"])
        if buffer[:2048] != encode_sector(lba, code):
            raise ValueError(f"{run['cycle']}: selected LBA {lba} data differs")
        mark = buffer[MARK_OFF:MARK_OFF + 7]
        if (mark[0], mark[1], mark[4], mark[5], mark[6]) != MARKER:
            raise ValueError(f"{run['cycle']}: payload handoff marker differs")
        rows = [p for p in records["PHASE"] if p["cycle"] == run["cycle"] and p["scsi_phase"] == PHASE_DATA_IN]
        if [(p["load_sector"], p["load_count"]) for p in rows] != [(lba, 1)]:
            raise ValueError(f"{run['cycle']}: READ(6) was not the selected LBA")


def assert_invalid(records, symbols):
    for run in records["RUN"]:
        if run["reason"] != "dev_selection_halt" or run["pc"] != symbols["dev_selection_halt"]:
            raise ValueError(f"{run['cycle']}: invalid/audio record was not rejected at selection halt: {run['reason']} {run['pc']:#x}")
        check_halt_confirmation(run)
        if any(p["cycle"] == run["cycle"] and p["scsi_phase"] == PHASE_DATA_IN for p in records["PHASE"]):
            raise ValueError(f"{run['cycle']}: invalid/audio record issued READ(6)")


def corrupt_selection_kind(rom_path, symbols):
    """Turn a host-valid image into a guest-negative fixture without accepting host metadata."""
    raw = bytearray(rom_path.read_bytes())
    offset = (symbols["experimental_selection"] & 0x1fff) + 6
    raw[offset] = 0
    rom_path.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    with tempfile.TemporaryDirectory(prefix="pce-experimental-track-contract-") as tmp:
        root = Path(tmp)
        probe = compile_probe(root, "geargrafx_cdread_probe.cpp", "geargrafx-experimental-track-contract")
        results = {}
        # A single data-track baseline selects LBA 0; its payload is produced by the shared loader generator.
        single_dir = root / "single"
        single_out = root / "single-rom"
        build_image(single_dir)
        single_cue = single_dir / CUE_NAME
        single_selected = selection_from_cue(single_cue, single_cue)
        build_experimental_bios_boot(base, single_out, args.mode, single_cue)
        code = (single_out / "payload.bin").read_bytes()
        single_cue = build_exec_disc(single_dir, single_selected["lba"], code)
        single_manifest = json.loads((single_out / "manifest.json").read_text())
        single_records = execute(probe, single_out / ROM_NAME, single_cue, single_out / "trace-labels.txt", STEP_BUDGET)
        assert_success(single_records, single_manifest["trace_symbols"], code, single_selected["lba"])
        results["single_data_lba0"] = {"status": "PASS", "lba": single_selected["lba"], "cold_warm": True}

        # The mixed fixture starts its MODE1 INDEX 01 well beyond LBA 5; patch only original synthetic user data.
        mixed_dir = root / "mixed"
        write_case(mixed_dir, "NONE", "V2")
        selected = selection_from_cue(mixed_dir / MIXED_CUE, mixed_dir / MIXED_CUE)
        if selected["lba"] == 5:
            raise ValueError("dynamic-LBA fixture accidentally selected the old fixed LBA")
        mixed_out = root / "mixed-rom"
        build_experimental_bios_boot(base, mixed_out, args.mode, mixed_dir / MIXED_CUE)
        patch_selected_payload(mixed_dir / MIXED_CUE, selected["lba"], (mixed_out / "payload.bin").read_bytes(), MIXED_BIN)
        mixed_manifest = json.loads((mixed_out / "manifest.json").read_text())
        mixed_records = execute(probe, mixed_out / ROM_NAME, mixed_dir / MIXED_CUE, mixed_out / "trace-labels.txt", STEP_BUDGET)
        assert_success(mixed_records, mixed_manifest["trace_symbols"], code, selected["lba"])
        results["mixed_audio_pregap_dynamic_non_lba5"] = {"status": "PASS", "track": selected["track"], "lba": selected["lba"], "cold_warm": True}

        audio_dir = root / "audio"
        write_case(audio_dir, "NO_TRACK", "V1")
        try:
            selection_from_cue(audio_dir / MIXED_CUE, audio_dir / MIXED_CUE)
        except SelectionError:
            pass
        else:
            raise ValueError("audio-only fixture unexpectedly selected a data track")
        # The normal host path refuses the audio CUE.  Corrupt a separately
        # host-valid ROM for the guest-negative case; arbitrary audio metadata
        # is never accepted by normal record construction.
        audio_out = root / "audio-rom"
        build_experimental_bios_boot(base, audio_out, args.mode, single_cue)
        audio_manifest = json.loads((audio_out / "manifest.json").read_text())
        corrupt_selection_kind(audio_out / ROM_NAME, audio_manifest["trace_symbols"])
        audio_records = execute(probe, audio_out / ROM_NAME, audio_dir / MIXED_CUE, audio_out / "trace-labels.txt", STEP_BUDGET)
        assert_invalid(audio_records, audio_manifest["trace_symbols"])
        results["audio_only_invalid_selection_before_read"] = {"status": "PASS_EXPECTED_FAILURE", "cold_warm": True, "read6": "not issued"}
    print("PASS real Geargrafx experimental host-selected synthetic tracks: single/mixed/pregap/dynamic LBA + audio rejection; NOT BIOS TOC/API")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"experimental track contract FAIL/BLOCKED: {error}") from error
