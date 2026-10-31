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
import re
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_cdread import BUFFER_BYTES, REQUIRED_SYMBOLS, verify_cdread, with_request
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

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_int(value, field, minimum=0, maximum=None):
    """Accept an actual integer in the consumer's domain, never bool."""
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise BlockedError(f"BLOCKED: {field} is not an integer in {bounds}")
    return value


def require_hex(value, field, byte_length=None, minimum_bytes=None):
    """Decode a complete, even-length hexadecimal observation before any oracle sees it."""
    if type(value) is not str or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
        raise BlockedError(f"BLOCKED: {field} is not valid even-length hexadecimal")
    decoded = bytes.fromhex(value)
    if byte_length is not None and len(decoded) != byte_length:
        raise BlockedError(f"BLOCKED: {field} has {len(decoded)} bytes, expected {byte_length}")
    if minimum_bytes is not None and len(decoded) < minimum_bytes:
        raise BlockedError(f"BLOCKED: {field} has {len(decoded)} bytes, need at least {minimum_bytes}")
    return decoded


def require_manifest(manifest):
    """Validate every build-manifest value consumed below before structural/oracle checks."""
    symbols = manifest.get("symbols")
    if type(symbols) is not dict:
        raise BlockedError("BLOCKED: required diagnostic manifest symbols is not an object")
    missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
    if missing:
        raise BlockedError(f"BLOCKED: required diagnostic manifest symbols missing: {', '.join(missing)}")
    # All diagnostic labels are 16-bit CPU addresses.  The ROM verifier relies
    # on these addresses for slices and vectors, so do not let bool/strings reach it.
    for name in REQUIRED_SYMBOLS:
        require_int(symbols[name], f"manifest symbol {name}", 0xE000, 0xFFFF)
    if symbols["cd_entry"] != 0xF000:
        raise BlockedError("BLOCKED: manifest symbol cd_entry is outside the diagnostic code base")
    if not (0xF000 < symbols["cd_cdb"] <= 0xFFF6 - 8):
        raise BlockedError("BLOCKED: manifest symbol cd_cdb cannot contain the complete READ(6) request")
    digest = manifest.get("code_sha256_excluding_request")
    if type(digest) is not str or not SHA256_RE.fullmatch(digest):
        raise BlockedError("BLOCKED: manifest code_sha256_excluding_request is not a SHA-256 string")
    return symbols, digest


def require_base_row(row, tag, index):
    prefix = f"probe {tag}[{index}]"
    if type(row["name"]) is not str or not row["name"]:
        raise BlockedError(f"BLOCKED: {prefix}.name is not a non-empty string")
    if row["cycle"] not in ("cold", "warm"):
        raise BlockedError(f"BLOCKED: {prefix}.cycle is not cold or warm")
    require_int(row["step"], f"{prefix}.step", 0)
    require_int(row["pc"], f"{prefix}.pc", 0, 0xFFFF)
    for field in ("a", "x", "y", "p", "sp", "scsi_phase", "scsi_signals", "cd_irq_active", "cd_irq_enabled"):
        require_int(row[field], f"{prefix}.{field}", 0, 0xFF)
    if type(row["mpr"]) is not list or len(row["mpr"]) != 8:
        raise BlockedError(f"BLOCKED: {prefix}.mpr is not an eight-byte list")
    for item, value in enumerate(row["mpr"]):
        require_int(value, f"{prefix}.mpr[{item}]", 0, 0xFF)
    require_int(row["load_sector"], f"{prefix}.load_sector", 0, 0x1FFFFF)
    require_int(row["load_count"], f"{prefix}.load_count", 0, 0xFF)


def require_row(row, tag, index):
    if type(row) is not dict:
        raise BlockedError(f"BLOCKED: probe {tag}[{index}] is not an observation object")
    missing = [key for key in ROW_KEYS[tag] if key not in row]
    if missing:
        raise BlockedError(f"BLOCKED: probe {tag}[{index}] is missing keys: {', '.join(missing)}")
    if tag in ("HIT", "PHASE", "RUN"):
        require_base_row(row, tag, index)
    if tag == "CAP":
        for field in ("instruction_step", "bios_loaded", "cdrom_hardware", "cdrom_media", "disc_given", "drive_ready"):
            if type(row[field]) is not bool:
                raise BlockedError(f"BLOCKED: probe CAP[{index}].{field} is not boolean")
        if type(row["loader"]) is not str or not row["loader"]:
            raise BlockedError(f"BLOCKED: probe CAP[{index}].loader is not a non-empty string")
    elif tag == "HIT":
        require_hex(row["buffer_head_hex"], f"probe HIT[{index}].buffer_head_hex", byte_length=32)
    elif tag == "RUN":
        if type(row["reason"]) is not str or not row["reason"]:
            raise BlockedError(f"BLOCKED: probe RUN[{index}].reason is not a non-empty string")
        if type(row["halt_confirmed"]) is not bool:
            raise BlockedError(f"BLOCKED: probe RUN[{index}].halt_confirmed is not boolean")
        # The producer uses -1 when the complete instruction budget expires
        # before it reaches a halt.  That is a completed negative observation
        # for the oracle to FAIL, not missing evidence.
        require_int(row["halted_after_step"], f"probe RUN[{index}].halted_after_step", -1)
        require_int(row["halt_confirmation_steps"], f"probe RUN[{index}].halt_confirmation_steps", 0)
        require_int(row["halt_confirmation_required"], f"probe RUN[{index}].halt_confirmation_required", 0)
        # The probe snapshots 32 bytes of result RAM; the oracle consumes its
        # first seven bytes, but accepting a shortened snapshot loses evidence.
        require_hex(row["result_hex"], f"probe RUN[{index}].result_hex", byte_length=32)
        require_hex(row["buffer_hex"], f"probe RUN[{index}].buffer_hex", byte_length=BUFFER_BYTES)
    elif tag == "INJECT":
        if row["cycle"] not in ("cold", "warm"):
            raise BlockedError(f"BLOCKED: probe INJECT[{index}].cycle is not cold or warm")
        require_int(row["step"], f"probe INJECT[{index}].step", 0)
        if type(row["event"]) is not str or not row["event"]:
            raise BlockedError(f"BLOCKED: probe INJECT[{index}].event is not a non-empty string")


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
    symbols, code_digest = require_manifest(manifest)
    labels = require_file(output / "trace-labels.txt")
    base = require_file(ROOT / "build" / args.mode / "bios-dev/dev-only-not-compatible-syscard3.pce")
    digest = verify_cdread(rom, read_artifact(base), symbols, code_digest)
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
