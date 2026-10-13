"""Pure-Python judge for Geargrafx CD read observations (Issue #5, diagnostic only).

Accepts a read ONLY when real guest execution reached the success halt AND every
transferred byte equals the independently generated sector data. Used both by the
integration runner (positive and negative runs) and by host tests on synthetic records.
"""
if __package__:
    from .build_cdread import BUFFER_BYTES, EXPECTED_MPR, HALT_CONFIRM_STEPS, SECTOR_BYTES
else:
    from build_cdread import BUFFER_BYTES, EXPECTED_MPR, HALT_CONFIRM_STEPS, SECTOR_BYTES

SENTINEL = 0x5A
# Geargrafx ScsiPhase enum order (debugger-visible, pinned revision).
PHASE_BUS_FREE, PHASE_COMMAND, PHASE_DATA_IN, PHASE_MESSAGE_IN, PHASE_STATUS = 0, 3, 4, 6, 7


def _first_difference(actual: bytes, expected: bytes):
    for index, (a, b) in enumerate(zip(actual, expected)):
        if a != b:
            return index
    return None if len(actual) == len(expected) else min(len(actual), len(expected))


def result_block(run: dict) -> dict:
    block = bytes.fromhex(run["result_hex"])
    return {"code": block[0], "stage": block[1], "bytes": block[2] | block[3] << 8,
            "scsi_status": block[4], "scsi_message": block[5], "last_bus": block[6]}


def check_halt_confirmation(run: dict) -> None:
    """A success needs HALT_CONFIRM_STEPS consecutive instructions observed on the halt loop (fail-closed)."""
    cycle = run.get("cycle")
    try:
        confirmed, steps = run["halt_confirmed"], run["halt_confirmation_steps"]
        required, arrival, step = run["halt_confirmation_required"], run["halted_after_step"], run["step"]
    except KeyError as missing:
        raise ValueError(f"{cycle}: halt confirmation evidence missing ({missing})") from None
    if confirmed is not True:
        raise ValueError(f"{cycle}: halt not confirmed (halt_confirmed={confirmed!r})")
    if required != HALT_CONFIRM_STEPS or steps != HALT_CONFIRM_STEPS:
        raise ValueError(f"{cycle}: halt confirmation covered {steps} of {required} steps, need {HALT_CONFIRM_STEPS}")
    if not isinstance(arrival, int) or arrival < 0 or step != arrival + steps:
        raise ValueError(f"{cycle}: halt confirmation step accounting inconsistent "
                         f"(arrival={arrival}, steps={steps}, executed={step})")


def check_success(records: dict, symbols: dict, disc: list, lba: int, count: int) -> None:
    """Raise ValueError unless cold AND warm runs read `count` sectors from `lba` exactly."""
    runs = records["RUN"]
    if [run["cycle"] for run in runs] != ["cold", "warm"]:
        raise ValueError("missing cold/warm CD read runs")
    if len(records["CAP"]) != 1 or not records["CAP"][0].get("instruction_step") \
            or not records["CAP"][0].get("cdrom_hardware") or not records["CAP"][0].get("cdrom_media"):
        raise ValueError("real instruction stepping / System Card CD path capability missing")
    for run in runs:
        if run.get("reason") != "cd_halt_ok" or run.get("pc") != symbols["cd_halt_ok"]:
            raise ValueError(f"{run.get('cycle')}: guest did not stop at the success checkpoint ({run.get('reason')})")
        check_halt_confirmation(run)
    if count < 1 or lba + count > len(disc):
        raise ValueError("the requested sectors are not on the reference disc")
    expected = b"".join(disc[lba + i] for i in range(count))
    for run in runs:
        cycle = run["cycle"]
        if run["x"] != 0xC0 or run["sp"] != 0xFF or run["mpr"] != EXPECTED_MPR:
            raise ValueError(f"{cycle}: X/SP/MPR at the success checkpoint differ")
        if not run["p"] & 4 or run["p"] & 8:
            raise ValueError(f"{cycle}: interrupt/decimal flags differ")
        block = result_block(run)
        if (block["code"], block["stage"], block["scsi_status"], block["scsi_message"]) != (0, 9, 0, 0):
            raise ValueError(f"{cycle}: guest result block is not a clean success: {block}")
        if block["bytes"] != len(expected):
            raise ValueError(f"{cycle}: guest transferred {block['bytes']} bytes, expected {len(expected)}")
        buffer = bytes.fromhex(run["buffer_hex"])
        if len(buffer) != BUFFER_BYTES:
            raise ValueError(f"{cycle}: buffer dump has {len(buffer)} bytes")
        bad = _first_difference(buffer[:len(expected)], expected)
        if bad is not None:
            raise ValueError(f"{cycle}: RAM differs from sector data at byte {bad} (sector {lba + bad // SECTOR_BYTES})")
        if any(byte != SENTINEL for byte in buffer[len(expected):]):
            raise ValueError(f"{cycle}: guest wrote beyond the requested length")
    check_scsi_sequence(records, lba, count)


def check_scsi_sequence(records: dict, lba: int, count: int) -> None:
    """The emulator itself saw COMMAND -> DATA_IN(READ lba/count) -> STATUS -> MESSAGE_IN -> BUS_FREE."""
    for cycle in ("cold", "warm"):
        rows = [row for row in records["PHASE"] if row["cycle"] == cycle]
        phases = [row["scsi_phase"] for row in rows]
        want = [PHASE_COMMAND, PHASE_DATA_IN, PHASE_STATUS, PHASE_MESSAGE_IN, PHASE_BUS_FREE]
        # a bus-reset pulse may add leading BUS_FREE events; the transfer itself must be exactly this order
        if phases[-len(want):] != want:
            raise ValueError(f"{cycle}: SCSI phase sequence {phases} does not end with {want}")
        data_in = rows[phases.index(PHASE_DATA_IN)]
        if (data_in["load_sector"], data_in["load_count"]) != (lba, count):
            raise ValueError(f"{cycle}: emulator saw READ request {(data_in['load_sector'], data_in['load_count'])}, "
                             f"expected {(lba, count)}")


def describe_failure(records: dict) -> list:
    """Per-cycle (reason, error code, stage, bytes) for negative-test assertions."""
    out = []
    for run in records["RUN"]:
        block = result_block(run)
        out.append((run["reason"], block["code"], block["stage"], block["bytes"]))
    return out
