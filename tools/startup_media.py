"""Classify a startup-probe run as NO_MEDIA or MEDIA_PRESENT from separately recorded evidence (#142).

Diagnostic only: neither verdict is a BIOS CD-boot claim. A record whose loader, disc flags and
media flag disagree, or that lacks CPU/CD-state/RAM/stop-PC evidence, raises ValueError (BLOCKED/FAIL).
"""
try:
    from .build_startup import MARKER
except ImportError:
    from build_startup import MARKER

DISC_LOADER = "LoadBiosFromBuffer(syscard)+LoadMedia(cue)"
NO_MEDIA_LOADER = "SetCDROMType(STANDARD)+LoadHuCardFromBuffer"
CD_INIT_STATE = 1  # startup_cd_state at $2205
# media verdict -> (loader, disc_given, cdrom_media)
BRANCHES = {"MEDIA_PRESENT": (DISC_LOADER, True, True), "NO_MEDIA": (NO_MEDIA_LOADER, False, False)}


def classify_startup_media(records, checkpoint_pc):
    caps = records.get("CAP", [])
    if len(caps) != 1:
        raise ValueError("expected exactly one CAP record")
    cap = caps[0]
    if cap.get("instruction_step") is not True or cap.get("cdrom_hardware") is not True:
        raise ValueError("instruction-step or CD hardware capability missing")
    triple = (cap.get("loader"), cap.get("disc_given"), cap.get("cdrom_media"))
    media = next((name for name, expected in BRANCHES.items() if triple == expected), None)
    if media is None:
        raise ValueError(f"loader/disc_given/cdrom_media are inconsistent: {triple}")
    if not cap.get("pc_after") or cap.get("pc_after") == cap.get("pc_before"):
        raise ValueError("CPU did not execute an instruction")
    cd_state = {key: cap.get(key) for key in ("scsi_phase", "cd_irq_active")}
    if any(type(v) is not int for v in cd_state.values()):
        raise ValueError("CD controller state not readable")
    if cd_state != {"scsi_phase": 0, "cd_irq_active": 0}:  # BUS_FREE, no pending CD IRQ: startup never touches the drive
        raise ValueError(f"CD controller not idle at startup: {cd_state}")
    obs = records.get("OBS", [])
    if [row.get("phase") for row in obs] != ["loaded", "cold", "nonreturning", "warm"]:
        raise ValueError("missing cold/warm observations")
    stops = [row["pc"] for row in obs[1:]]
    if stops != [checkpoint_pc] * 3:
        raise ValueError(f"stop PCs {stops} differ from the startup checkpoint")
    for row in obs[1:]:
        ram = bytes.fromhex(row["work_ram_0200_hex"])
        if ram[:len(MARKER)] != MARKER or ram[5:6] != bytes([CD_INIT_STATE]):
            raise ValueError("RAM marker/CD init state mismatch")
    return {"media": media, "cpu_executed": True, "cd_state": cd_state,
            "ram_marker": MARKER.hex(), "stop_pcs": stops}
