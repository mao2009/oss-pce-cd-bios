"""Pinned real-core test for the DEV-only host-selected synthetic data-track contract.

The host analyses original synthetic CUE files and embeds the resulting INDEX 01
LBA in each experimental ROM. The guest validates that record, then uses its
internal variable READ(6) helper. It does not read a TOC and is not a BIOS API.

Besides the selected-sector transfer, every positive case re-checks the boot
path the image's reset vector takes (reset -> init -> selection -> selected
entry -> READ(6) -> copy -> payload handoff -> halt), so a regression in the
experimental reset/init order, stack or copy is detected here and not only by
the separate #501 DEV-boot regression.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(Path(__file__).resolve().parent)]

from build_cdload_exec import EXEC_ADDRESS, encode_sector
from build_experimental_bios_boot import ROM_NAME, build_experimental_bios_boot, build_experimental_payload
from cdread_verify import (PHASE_BUS_FREE, PHASE_COMMAND, PHASE_DATA_IN, PHASE_MESSAGE_IN, PHASE_STATUS,
                           check_halt_confirmation, result_block)
from environment import DEPENDENCIES
from experimental_track_selection import SelectionError, selection_from_cue, snapshot_media, verify_media_identity
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image
from synthetic_boot_media import (AUDIO_FRAMES, BIN_NAME as MIXED_BIN, CUE_NAME as MIXED_CUE,
                                  USER_OFFSET, V2_PREGAP_FRAMES, write_case)

STEP_BUDGET = 12_000_000
BUFFER_BASE, MARK_OFF = 0x2800, 0x3F00 - 0x2800
SECTOR_BYTES = 2048
SENTINEL = 0x5A
MARKER = (0xA5, 0x5A, 0xF8, 0xFF, 0x3C)
CYCLES = ["cold", "warm"]
# First-arrival order of the boot path; every label here has its own PC in this image.
BOOT_ORDER = ("experimental_bios_reset", "experimental_bios_init", "cd_set_read_lba", "cd_entry_selected",
              "cd_buffer_ready", "cd_data_done", "payload_entry", "payload_done", "cd_payload_return", "cd_halt_ok")
# Labels a rejected selection must never reach: nothing past the guest's record validation.
NOT_AFTER_REJECT = ("cd_set_read_lba", "cd_entry", "cd_entry_selected", "cd_buffer_ready", "cd_data_done",
                    "payload_entry", "cd_halt_ok", "cd_halt_fail")
# Selection record byte offsets: "XSEL"(0..3), version(4), format(5), kind(6), track(7), LBA hi/mid/lo(8..10).
GUEST_NEGATIVES = (
    ("magic", 0, ord("Y")),
    ("version", 4, 2),
    ("kind_zero", 6, 0),
    ("track_zero", 7, 0),
    ("track_100", 7, 100),
    ("lba_above_21_bits", 8, 0x20),
)


def patch_selected_payload(cue, lba, code, bin_name=BIN_NAME):
    raw = bytearray((cue.parent / bin_name).read_bytes())
    raw[lba * RAW_BYTES + USER_OFFSET:lba * RAW_BYTES + USER_OFFSET + 2048] = encode_sector(lba, code)
    (cue.parent / bin_name).write_bytes(raw)


def require_cycles(records):
    if [r["cycle"] for r in records["RUN"]] != CYCLES:
        raise ValueError(f"missing cold/warm observations: {[r['cycle'] for r in records['RUN']]}")
    if records.get("BLOCK"):
        raise ValueError(f"probe reported BLOCK records: {records['BLOCK']}")


def assert_boot_path(records, symbols, cycle):
    """The reset vector's whole path, by first arrival step and PC, with the plain entry never taken."""
    hits = [h for h in records["HIT"] if h["cycle"] == cycle]
    names = [h["name"] for h in hits]
    if "cd_entry" in names:
        raise ValueError(f"{cycle}: plain cd_entry was reached; the selected CDB path must use cd_entry_selected")
    ordered = [h for h in hits if h["name"] in BOOT_ORDER]
    if [h["name"] for h in ordered] != list(BOOT_ORDER):
        raise ValueError(f"{cycle}: label order {[h['name'] for h in ordered]} differs from {list(BOOT_ORDER)}")
    steps = [h["step"] for h in ordered]
    if steps != sorted(steps) or len(set(steps)) != len(steps):
        raise ValueError(f"{cycle}: label arrival steps not strictly increasing: {steps}")
    by_name = {h["name"]: h for h in ordered}
    for name in BOOT_ORDER:
        if by_name[name]["pc"] != symbols[name]:
            raise ValueError(f"{cycle}: {name} reached at {by_name[name]['pc']:#x}, expected {symbols[name]:#x}")
    if by_name["payload_entry"]["pc"] != EXEC_ADDRESS:
        raise ValueError(f"{cycle}: payload entered at {by_name['payload_entry']['pc']:#x}, not {EXEC_ADDRESS:#x}")
    if bytes.fromhex(by_name["cd_buffer_ready"]["buffer_head_hex"]) != bytes([SENTINEL]) * 32:
        raise ValueError(f"{cycle}: staging RAM was not the sentinel before the transfer")


def assert_single_read(records, cycle, lba):
    rows = [p for p in records["PHASE"] if p["cycle"] == cycle]
    reads = [(p["load_sector"], p["load_count"]) for p in rows if p["scsi_phase"] == PHASE_DATA_IN]
    if reads != [(lba, 1)]:
        raise ValueError(f"{cycle}: READ(6) requests {reads}, expected exactly the selected LBA {[(lba, 1)]}")
    bus = [p["scsi_phase"] for p in rows if p["scsi_phase"] != PHASE_BUS_FREE]
    if bus != [PHASE_COMMAND, PHASE_DATA_IN, PHASE_STATUS, PHASE_MESSAGE_IN]:
        raise ValueError(f"{cycle}: SCSI phases {bus} differ from one complete READ(6) transfer")


def assert_success(records, symbols, code, lba):
    require_cycles(records)
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "cd_halt_ok" or run["pc"] != symbols["cd_halt_ok"]:
            raise ValueError(f"{cycle}: did not complete load/handoff: {run['reason']} at {run['pc']:#x}, {result_block(run)}")
        check_halt_confirmation(run)
        if run["sp"] != 0xFF:
            raise ValueError(f"{cycle}: stack pointer {run['sp']:#x} at halt, expected 0xff")
        block = result_block(run)
        if (block["code"], block["stage"], block["bytes"], block["scsi_status"], block["scsi_message"]) \
                != (0, 16, SECTOR_BYTES, 0, 0):
            raise ValueError(f"{cycle}: load/execute result block is not a clean success: {block}")
        buffer = bytes.fromhex(run["buffer_hex"])
        if len(buffer) != 6144:
            raise ValueError(f"{cycle}: RAM dump has {len(buffer)} bytes")
        if buffer[:SECTOR_BYTES] != encode_sector(lba, code):
            raise ValueError(f"{cycle}: selected LBA {lba} data differs")
        if buffer[EXEC_ADDRESS - BUFFER_BASE:EXEC_ADDRESS - BUFFER_BASE + len(code)] != code:
            raise ValueError(f"{cycle}: code in RAM at {EXEC_ADDRESS:#x} differs from the payload")
        mark = buffer[MARK_OFF:MARK_OFF + 7]
        if (mark[0], mark[1], mark[4], mark[5], mark[6]) != MARKER:
            raise ValueError(f"{cycle}: payload handoff marker differs")
        assert_boot_path(records, symbols, cycle)
        assert_single_read(records, cycle, lba)


def assert_invalid(records, symbols):
    require_cycles(records)
    for run in records["RUN"]:
        cycle = run["cycle"]
        if run["reason"] != "dev_selection_halt" or run["pc"] != symbols["dev_selection_halt"]:
            raise ValueError(f"{cycle}: invalid record was not rejected at selection halt: {run['reason']} {run['pc']:#x}")
        check_halt_confirmation(run)
        reached = sorted({h["name"] for h in records["HIT"] if h["cycle"] == cycle} & set(NOT_AFTER_REJECT))
        if reached:
            raise ValueError(f"{cycle}: rejected record still reached {reached}")
        issued = [p["scsi_phase"] for p in records["PHASE"]
                  if p["cycle"] == cycle and p["scsi_phase"] in (PHASE_COMMAND, PHASE_DATA_IN)]
        if issued:
            raise ValueError(f"{cycle}: rejected record reached SCSI command/data phases {issued} (READ(6) must not be issued)")


def corrupt_selection(rom_path, symbols, offset, value):
    """Turn a host-valid image into a guest-negative fixture without accepting host metadata."""
    raw = bytearray(rom_path.read_bytes())
    at = (symbols["experimental_selection"] & 0x1fff) + offset
    if raw[at] == value:
        raise ValueError(f"corruption at record offset {offset} would not change the byte")
    raw[at] = value
    rom_path.write_bytes(raw)


def execute_verified(probe, rom, cue, labels, selection):
    """Last host-side content check before the core receives the CUE pathname.

    The mounted source is a test-private snapshot whose files are read-only and
    untouched on the normal path. A directory-access or privileged external
    writer could still race Geargrafx's pathname open, so this is a host
    contract rather than a guest cryptographic authenticity proof.
    """
    verify_media_identity(selection["media_identity"], cue)
    return execute(probe, rom, cue, labels, STEP_BUDGET)


def guest_negative(probe, root, base, mode, payload, name, offset, value, build_cue, selection, mounted_cue=None):
    """A ROM built for a host-valid selection, then one record byte corrupted; must halt before any READ(6).

    By default the valid medium stays mounted (`mounted_cue` None).  Mounting a different medium is an
    explicit corrupt-ROM fixture and skips the host identity check, which binds only the build medium."""
    out = root / f"negative-{name}-rom"
    build_experimental_bios_boot(base, out, mode, build_cue, payload)
    manifest = json.loads((out / "manifest.json").read_text())
    corrupt_selection(out / ROM_NAME, manifest["trace_symbols"], offset, value)
    if mounted_cue is None:
        records = execute_verified(probe, out / ROM_NAME, build_cue, out / "trace-labels.txt", selection)
    else:
        records = execute(probe, out / ROM_NAME, mounted_cue, out / "trace-labels.txt", STEP_BUDGET)
    assert_invalid(records, manifest["trace_symbols"])
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    output = ROOT / "build" / args.mode / "experimental-bios-boot"
    with tempfile.TemporaryDirectory(prefix="pce-experimental-track-contract-") as tmp:
        root = Path(tmp)
        probe = compile_probe(root, "geargrafx_cdread_probe.cpp", "geargrafx-experimental-track-contract")
        results = {}
        rom_digests = {}
        # Payload generation is independent of selection.  It is placed into a
        # medium before CUE analysis; the ROM builder rechecks identical bytes.
        payload, _ = build_experimental_payload(root / "payload", args.mode)
        # A single data-track baseline selects LBA 0; its payload is produced by the shared loader generator.
        single_dir = root / "single"
        single_out = root / "single-rom"
        build_image(single_dir)
        single_cue = single_dir / CUE_NAME
        patch_selected_payload(single_cue, 0, payload)
        single_cue = snapshot_media(single_cue, root / "single-mounted")
        single_selected = selection_from_cue(single_cue, single_cue)
        build_experimental_bios_boot(base, single_out, args.mode, single_cue, payload)
        single_manifest = json.loads((single_out / "manifest.json").read_text())
        single_records = execute_verified(probe, single_out / ROM_NAME, single_cue, single_out / "trace-labels.txt", single_selected)
        assert_success(single_records, single_manifest["trace_symbols"], payload, single_selected["lba"])
        rom_digests["single_data_lba0"] = single_records["rom_sha256"]
        results["single_data_lba0"] = {"status": "PASS", "lba": single_selected["lba"], "cycles": CYCLES,
                                       "boot_path": list(BOOT_ORDER), "media_identity": single_selected["media_identity"]}

        # The mixed fixture starts its MODE1 INDEX 01 well beyond LBA 5; patch only original synthetic user data.
        mixed_dir = root / "mixed"
        write_case(mixed_dir, "NONE", "V2")
        mixed_cue = mixed_dir / MIXED_CUE
        # Finalize the selected data sector before deriving either LBA or identity.
        patch_selected_payload(mixed_cue, AUDIO_FRAMES + V2_PREGAP_FRAMES, payload, MIXED_BIN)
        mixed_cue = snapshot_media(mixed_cue, root / "mixed-mounted")
        selected = selection_from_cue(mixed_cue, mixed_cue)
        if selected["lba"] == 5:
            raise ValueError("dynamic-LBA fixture accidentally selected the old fixed LBA")
        mixed_out = root / "mixed-rom"
        build_experimental_bios_boot(base, mixed_out, args.mode, mixed_cue, payload)
        mixed_manifest = json.loads((mixed_out / "manifest.json").read_text())
        mixed_records = execute_verified(probe, mixed_out / ROM_NAME, mixed_cue, mixed_out / "trace-labels.txt", selected)
        assert_success(mixed_records, mixed_manifest["trace_symbols"], payload, selected["lba"])
        rom_digests["mixed_audio_pregap_dynamic_non_lba5"] = mixed_records["rom_sha256"]
        results["mixed_audio_pregap_dynamic_non_lba5"] = {"status": "PASS", "track": selected["track"], "lba": selected["lba"],
                                                           "cycles": CYCLES, "boot_path": list(BOOT_ORDER),
                                                           "media_identity": selected["media_identity"]}

        # Host-side refusal: the normal record construction never accepts an audio-only CUE.
        audio_dir = root / "audio"
        write_case(audio_dir, "NO_TRACK", "V1")
        try:
            selection_from_cue(audio_dir / MIXED_CUE, audio_dir / MIXED_CUE)
        except SelectionError as error:
            host_reason = str(error)
        else:
            raise ValueError("audio-only fixture unexpectedly selected a data track")
        results["host_audio_only_cue_refused"] = {"status": "PASS_EXPECTED_FAILURE", "layer": "host",
                                                  "reason": host_reason}

        # Guest-side refusal: a host-valid ROM with one record field corrupted.  These are corrupt-ROM
        # negatives, not normal host selections.  The valid data medium stays mounted so a guest that
        # failed to reject the record would really issue READ(6) and be detected by the SCSI trace.
        for name, offset, value in GUEST_NEGATIVES:
            guest_negative(probe, root, base, args.mode, payload, name, offset, value, single_cue, single_selected)
            results[f"guest_{name}_record_halts_before_read6"] = {
                "status": "PASS_EXPECTED_FAILURE", "layer": "guest", "record_offset": offset, "cycles": CYCLES,
                "read6": "not issued", "scsi_command_or_data_phase": "none", "medium": "valid single data track"}
        # The kind-zero record with the audio-only medium mounted (the record, not the medium, is what is refused).
        guest_negative(probe, root, base, args.mode, payload, "kind_zero_audio_medium", 6, 0, single_cue, single_selected,
                       mounted_cue=audio_dir / MIXED_CUE)
        results["guest_kind_zero_record_with_audio_medium_halts_before_read6"] = {
            "status": "PASS_EXPECTED_FAILURE", "layer": "guest", "record_offset": 6, "cycles": CYCLES,
            "read6": "not issued", "scsi_command_or_data_phase": "none", "medium": "audio-only",
            "note": "the guest refuses the corrupted record; it does not inspect the medium's track type"}

    evidence = {"scope": "DEV-only experimental image: host-selected synthetic CUE INDEX 01 record, guest record validation, "
                         "internal READ(6), payload handoff; no BIOS TOC command or API",
                "rom_sha256": rom_digests, "geargrafx_revision": DEPENDENCIES["geargrafx"]["revision"],
                "source_archive_sha256": DEPENDENCIES["geargrafx"]["sha256"], "tests": results,
                "cd_read_api_slot_03": "NOT_IMPLEMENTED", "bios_track_selection": "NOT_IMPLEMENTED",
                "system_card_compatibility": "NO_CLAIM", "real_hardware_or_second_emulator": "NOT_TESTED",
                "proprietary_content": "NONE", "status": "PASS"}
    output.mkdir(parents=True, exist_ok=True)
    (output / "experimental-track-contract-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print("PASS real Geargrafx experimental host-selected synthetic tracks: single/mixed/pregap/dynamic LBA cold+warm with boot "
          f"path; host audio-only CUE refusal; {len(GUEST_NEGATIVES) + 1} guest corrupt-record halts before READ(6); "
          "NOT BIOS TOC/API")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"experimental track contract FAIL/BLOCKED: {error}") from error
