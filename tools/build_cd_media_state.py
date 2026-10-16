#!/usr/bin/env python3
"""Build, verify and judge the Issue #144 CD media-state observation point (DEVELOPMENT-ONLY).

The #143 reset entry calls src/cd_media_state.s once (ca65 -D CD_MEDIA_STATE). The default DEV ROM and
the plain reset-entry ROM are unchanged. No BIOS API / CD service is enabled; nothing here is VERIFIED.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from .build_reset_entry import RESET_CODE
    from .environment import DEPENDENCIES, executable
    from .make_cd_image import MAGIC
else:
    from build_rom import BANK_BYTES, IMAGE_BYTES, ROOT, assemble_api_objects, verify_dev_rom
    from build_reset_entry import RESET_CODE
    from environment import DEPENDENCIES, executable
    from make_cd_image import MAGIC

SIGNATURE_OK, UNRECOGNISED, NO_DATA = 0x01, 0x02, 0x03
STATES = {SIGNATURE_OK: "MEDIA_SIGNATURE_OK", UNRECOGNISED: "MEDIA_UNRECOGNISED", NO_DATA: "NO_DATA"}
ERRORS = {0x81: "BUS_NOT_FREE_AFTER_RESET", 0x82: "SELECT_TIMEOUT", 0x83: "COMMAND_REQ_TIMEOUT",
          0x84: "DATA_TIMEOUT", 0x85: "SHORT_READ", 0x86: "BAD_SCSI_STATUS_OR_MESSAGE",
          0x87: "STATUS_MESSAGE_TIMEOUT", 0x88: "BUS_NOT_FREED", 0x8A: "ACK_HANDSHAKE_TIMEOUT", 0xFF: "IN_PROGRESS"}
RESET_READY = 0xF010           # #143 reset_ready, moved by the 3-byte JSR
TRAP = "reset_unexpected_interrupt"
PAIRED_LOADER = "paired:SetCDROMType(STANDARD)+LoadHuCardFromBuffer+ResetMedia(false)"
SENTINEL = 0x5A
SECTOR = 2048
PHASE_DATA_IN = 4              # ScsiController::SCSI_PHASE_DATA_IN
SYMBOLS = ("reset_entry", "reset_mpr_ready", "reset_ready", TRAP, "cd_media_state", "ms_select",
           "ms_command_sent", "ms_status", "ms_done")


def verify_cd_media_state(data: bytes, original: bytes) -> str:
    verify_dev_rom(original)
    if len(data) != IMAGE_BYTES or data[:0x1000] != original[:0x1000]:
        raise ValueError("media-state image size or API table/fallback area differs")
    target = int.from_bytes(data[0x100E:0x1010], "little")
    if data[0x1000:0x100D] != RESET_CODE[:13] or data[0x100D] != 0x20 or data[0x1010:0x1012] != b"\x80\xfe" \
            or data[0x1012:0x1016] != bytes.fromhex("a2ee80fe") or target != 0xF016:
        raise ValueError("reset entry does not call cd_media_state exactly once before reset_ready")
    trap = (0xF012).to_bytes(2, "little")
    if data[0x1FF6:0x1FFE] != trap * 4 or data[0x1FFE:0x2000] != b"\x00\xf0":
        raise ValueError("media-state vectors differ")
    if data[BANK_BYTES:] != b"\xff" * (IMAGE_BYTES - BANK_BYTES):
        raise ValueError("media-state image modified unused ROM banks")
    return hashlib.sha256(data).hexdigest()


def build_cd_media_state(original_path: Path, output: Path, mode="debug") -> Path:
    if mode not in ("debug", "release"):
        raise ValueError("MODE must be debug or release")
    original = original_path.read_bytes()
    verify_dev_rom(original)
    ca65, ld65 = executable("ca65"), executable("ld65")
    output.mkdir(parents=True, exist_ok=True)
    objects = assemble_api_objects(output, mode)
    for source in (ROOT / "src/reset_entry.s", ROOT / "src/cd_media_state.s"):
        obj = output / (source.stem + ".o")
        subprocess.run([ca65, str(source), "-D", "CD_MEDIA_STATE", "-o", str(obj),
                        *(["-g"] if mode == "debug" else [])], check=True)
        objects.append(obj)
    linked, labels = output / "bank0.bin", output / "media-state.lbl"
    subprocess.run([ld65, "-C", str(ROOT / "src/bank0.cfg"), "-o", str(linked), "-Ln", str(labels),
                    "-m", str(output / "media-state.map"), *map(str, objects)], check=True)
    found = {n: int(a, 16) for a, n in re.findall(r"^al ([0-9A-Fa-f]{6}) \.([A-Za-z_][A-Za-z_0-9]*)$",
                                                  labels.read_text(), re.M) if n in SYMBOLS}
    if set(found) != set(SYMBOLS) or found["reset_ready"] != RESET_READY:
        raise ValueError(f"media-state symbols/placement mismatch: {found}")
    rom = output / "cd-media-state-not-bios.pce"
    rom.write_bytes(linked.read_bytes() + b"\xff" * (IMAGE_BYTES - BANK_BYTES))
    digest = verify_cd_media_state(rom.read_bytes(), original)
    (output / "trace-labels.txt").write_text("".join(f"{n} {v:04x}\n" for n, v in found.items()), encoding="ascii")
    (output / "manifest.json").write_text(json.dumps({
        "status": "development-cd-media-state-not-compatible-bios", "mode": mode, "rom_sha256": digest,
        "pinned_cc65": DEPENDENCIES["cc65"]["revision"], "symbols": found, "scsi_commands": ["READ(6) LBA 0 x1"],
        "api_implementation": "NONE", "release_eligible": False,
    }, indent=2, sort_keys=True) + "\n")
    return rom


def judge(records) -> str:
    """Return the media state both cycles agree on; raise ValueError for anything that is not a media state."""
    caps = records.get("CAP", [])
    if len(caps) != 1 or caps[0].get("instruction_step") is not True or caps[0].get("cdrom_hardware") is not True:
        raise ValueError("BLOCKED: CD hardware/instruction stepping not confirmed")
    runs = records.get("RUN", [])
    if [r.get("cycle") for r in runs] != ["cold", "warm"]:
        raise ValueError("cold and warm runs required")
    states = set()
    for run in runs:
        cycle = run["cycle"]
        if run.get("reason") != "reset_ready" or run.get("halt_confirmed") is not True or run.get("pc") != RESET_READY:
            raise ValueError(f"{cycle}: did not return to reset_ready ({run.get('reason')})")
        if run.get("sp") != 0xFF or run["mpr"][:2] != [0xFF, 0xF8] or not run["p"] & 0x04 or run["p"] & 0x08:
            raise ValueError(f"{cycle}: reset-entry SP/MPR0-1/I/D state changed by the observation point")
        if not any(h.get("name") == "ms_done" and h.get("cycle") == cycle for h in records.get("HIT", [])):
            raise ValueError(f"{cycle}: cd_media_state never returned")
        res = bytes.fromhex(run["result_hex"])
        buf = bytes.fromhex(run["buffer_hex"])[:SECTOR]
        state, nbytes, status, message = res[0], res[2] | res[3] << 8, res[4], res[5]
        if run.get("x") != state:
            raise ValueError(f"{cycle}: X differs from the stored state")
        if state not in STATES:
            raise ValueError(f"{cycle}: firmware error {ERRORS.get(state, hex(state))} is not a media state")
        if status or message:
            raise ValueError(f"{cycle}: SCSI status/message {status}/{message} not GOOD/COMPLETE")
        data_in = any(p.get("cycle") == cycle and p.get("scsi_phase") == PHASE_DATA_IN for p in records.get("PHASE", []))
        if state == NO_DATA:
            if nbytes or data_in or buf != bytes([SENTINEL]) * SECTOR:
                raise ValueError(f"{cycle}: NO_DATA claimed but data was transferred")
        else:
            if nbytes != SECTOR or not data_in:
                raise ValueError(f"{cycle}: media state without a full DATA_IN sector")
            if (buf[:len(MAGIC)] == MAGIC) != (state == SIGNATURE_OK):
                raise ValueError(f"{cycle}: state {STATES[state]} disagrees with the sector bytes")
        states.add(STATES[state])
    if len(states) != 1:
        raise ValueError(f"cold/warm judgment differs: {sorted(states)}")
    return states.pop()


def check_pair(with_disc, without_disc):
    """Paired control: same ROM, same loader, only the inserted disc differs; the judgment must differ."""
    a, b = with_disc["CAP"][0], without_disc["CAP"][0]
    if with_disc.get("rom_sha256") != without_disc.get("rom_sha256"):
        raise ValueError("pair uses different ROMs")
    if a.get("loader") != PAIRED_LOADER or b.get("loader") != PAIRED_LOADER:
        raise ValueError("pair does not use the same paired loader")
    if (a.get("disc_given"), a.get("drive_ready"), b.get("disc_given"), b.get("drive_ready")) != (True, True, False, False):
        raise ValueError("pair variable (disc inserted / drive ready) not exactly one-sided")
    result = judge(with_disc), judge(without_disc)
    if result != ("MEDIA_SIGNATURE_OK", "NO_DATA"):
        raise ValueError(f"paired judgment did not follow the disc: {result}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    args = parser.parse_args()
    base = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    rom = build_cd_media_state(base, ROOT / "build" / args.mode / "cd-media-state", args.mode)
    print(f"PASS CD media-state observation image (NOT complete BIOS): {rom}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"CD media-state build FAIL/BLOCKED: {error}") from error
