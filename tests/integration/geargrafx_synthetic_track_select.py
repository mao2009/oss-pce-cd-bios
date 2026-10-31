"""Real Geargrafx synthetic CUE track-selection diagnostic (Issue #607 follow-up).

The host selects only a CUE-analyzed MODE1/2352 INDEX 01 LBA, then the original
guest READ(6) diagnostic reads that absolute LBA.  Geargrafx has no confirmed
guest TOC selection surface used here, so this is not a BIOS command or boot
implementation.  Audio-only rejection is host selection refusal; the paired
real-core observation records that raw READ(6) itself does not provide it.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
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

EXIT_BLOCKED = 3


class BlockedError(RuntimeError):
    """A required local diagnostic prerequisite or observation is unavailable."""


def require_file(path):
    """Return an existing regular artifact, otherwise fail closed before judging it."""
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as error:
        raise BlockedError(f"BLOCKED: required diagnostic artifact is unreadable: {path.name}") from error
    if stat.S_ISLNK(info.st_mode):
        raise BlockedError(f"BLOCKED: required diagnostic artifact must not be a symlink: {path.name}")
    if not stat.S_ISREG(info.st_mode):
        raise BlockedError(f"BLOCKED: required diagnostic artifact is not a regular file: {path.name}")
    return path


def read_artifact(path):
    """Read a regular artifact without following a replacement symlink.

    Build outputs are evidence inputs.  A missing, unreadable, or swapped input
    is unavailable evidence, rather than a completed-run oracle contradiction.
    """
    path = require_file(path)
    try:
        before = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as artifact:
            opened = os.fstat(artifact.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise BlockedError(f"BLOCKED: required diagnostic artifact is not a regular file: {path.name}")
            data = artifact.read()
        after = path.lstat()
    except BlockedError:
        raise
    except OSError as error:
        raise BlockedError(f"BLOCKED: required diagnostic artifact is unreadable: {path.name}") from error
    if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or stat.S_ISLNK(after.st_mode)):
        raise BlockedError(f"BLOCKED: required diagnostic artifact changed while being read: {path.name}")
    return data


def read_json_artifact(path):
    """Decode a complete JSON build artifact or classify it as unavailable."""
    try:
        value = json.loads(read_artifact(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BlockedError(f"BLOCKED: required diagnostic artifact has partial or unreadable JSON: {Path(path).name}") from error
    if type(value) is not dict:
        raise BlockedError(f"BLOCKED: required diagnostic artifact JSON is not an object: {Path(path).name}")
    return value


RECORD_LISTS = ("CAP", "HIT", "PHASE", "RUN", "BLOCK", "INJECT")
BASE_ROW_KEYS = ("name", "cycle", "step", "pc", "a", "x", "y", "p", "sp", "mpr",
                 "scsi_phase", "scsi_signals", "load_sector", "load_count", "cd_irq_active", "cd_irq_enabled")
ROW_KEYS = {
    "CAP": ("instruction_step", "bios_loaded", "loader", "cdrom_hardware", "cdrom_media", "disc_given", "drive_ready"),
    "HIT": BASE_ROW_KEYS + ("buffer_head_hex",),
    "PHASE": BASE_ROW_KEYS,
    "RUN": BASE_ROW_KEYS + ("reason", "halt_confirmed", "halted_after_step", "halt_confirmation_steps",
                              "halt_confirmation_required", "result_hex", "buffer_hex"),
    "BLOCK": (),
    "INJECT": ("cycle", "step", "event"),
}


def require_row(row, tag, index):
    if type(row) is not dict:
        raise BlockedError(f"BLOCKED: probe {tag}[{index}] is not an observation object")
    missing = [key for key in ROW_KEYS[tag] if key not in row]
    if missing:
        raise BlockedError(f"BLOCKED: probe {tag}[{index}] is missing keys: {', '.join(missing)}")


def require_complete_records(records, case):
    """Reject a truncated probe transcript as unavailable evidence, not an oracle failure."""
    if type(records) is not dict:
        raise BlockedError(f"BLOCKED: {case}: probe returned no observation record")
    missing = [key for key in RECORD_LISTS if type(records.get(key)) is not list]
    if missing:
        raise BlockedError(f"BLOCKED: {case}: partial probe record missing or malformed {', '.join(missing)}")
    for tag in RECORD_LISTS:
        for index, row in enumerate(records[tag]):
            require_row(row, tag, index)
    if len(records["CAP"]) != 1 or [run["cycle"] for run in records["RUN"]] != ["cold", "warm"]:
        raise BlockedError(f"BLOCKED: {case}: partial probe record lacks one cold/warm capability observation")
    for tag in ("HIT", "PHASE", "INJECT"):
        if any(row["cycle"] not in ("cold", "warm") for row in records[tag]):
            raise BlockedError(f"BLOCKED: {case}: probe {tag} row lacks a cold/warm cycle")


def exit_for_error(error):
    """Keep unavailable prerequisites separate from completed-run oracle failures."""
    return EXIT_BLOCKED if isinstance(error, (BlockedError, OSError, subprocess.SubprocessError)) \
        or str(error).startswith("BLOCKED:") else 1


def execute_complete(*args):
    """Run the probe, translating an unreadable/truncated transcript to BLOCKED."""
    try:
        records = execute(*args)
    except (ValueError, json.JSONDecodeError, PermissionError, IsADirectoryError, OSError) as error:
        raise BlockedError(f"BLOCKED: probe observation is partial or unreadable: {error}") from error
    require_complete_records(records, "Geargrafx READ(6)")
    return records


def run_selected(probe, rom, labels, symbols, cue, selected):
    """Run and fully judge one selected INDEX 01 sector on cold and warm reset."""
    variant = with_request(rom, symbols, selected["lba"], 1)
    path = cue.parent / "selected-read-not-bios.pce"
    path.write_bytes(variant)
    records = execute_complete(probe, path, cue, labels)
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
    rom = read_artifact(output / "cdread-probe-not-bios.pce")
    manifest = read_json_artifact(output / "manifest.json")
    if type(manifest.get("symbols")) is not dict or "code_sha256_excluding_request" not in manifest:
        raise BlockedError("BLOCKED: required diagnostic manifest is partial")
    labels, symbols = require_file(output / "trace-labels.txt"), manifest["symbols"]
    base = require_file(ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce")
    digest = verify_cdread(rom, read_artifact(base), symbols, manifest["code_sha256_excluding_request"])
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
        audio_records = execute_complete(probe, audio_path, audio / CUE_NAME, labels)
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
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        status = "BLOCKED" if exit_for_error(error) == EXIT_BLOCKED else "FAIL"
        print(f"synthetic track selection {status}: {error}", file=sys.stderr)
        raise SystemExit(exit_for_error(error)) from error
