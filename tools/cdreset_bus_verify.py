"""Judge for the real-core CD_RESET bus-reset observations (Issue #10, B2, diagnostic only).

Reads the per-pulse records that src/cd_reset_probe.s writes into the result block and decides
whether the two bus-reset pulses behaved as the Geargrafx core is observed to do. It does NOT
judge the System Card CD_RESET API: a bus reset that behaves here is necessary evidence for the
low-level register effect only, never proof of API compatibility (docs/cd-reset-diagnostic.md).
The data path itself is judged by tools/cdread_verify.check_success, which must also pass.
"""
import re

RESET_PULSES = 2
PHASE_MASK = 0xF8          # $1800 BSY/REQ/MSG/CD/IO; 0 = bus free
RST_BIT = 0x02             # $1804 bit 1 = SCSI bus reset
RECORD_OFFSET = 8          # first per-pulse record in the result block (CPU $2308)
RECORD_BYTES = 8           # one record per pulse
RECORD_FIELDS = ("bus_asserted", "bus_released", "wait_result", "readback_asserted", "readback_released")
MIN_BLOCK_BYTES = RECORD_OFFSET + RESET_PULSES * RECORD_BYTES  # 24: header + two pulse records


def pulse_records(run: dict) -> list:
    """Per-pulse records; raises ValueError on malformed hex/length or a pulse count other than RESET_PULSES."""
    text = run["result_hex"]
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", text) or len(text) // 2 < MIN_BLOCK_BYTES:
        raise ValueError(f"{run.get('cycle')}: malformed result_hex (need even-length hex, at least {MIN_BLOCK_BYTES} bytes)")
    block = bytes.fromhex(text)
    if block[7] != RESET_PULSES:
        raise ValueError(f"{run.get('cycle')}: guest completed {block[7]} reset pulses, expected {RESET_PULSES}")
    rows = []
    for index in range(RESET_PULSES):
        base = RECORD_OFFSET + index * RECORD_BYTES
        rows.append(dict(zip(RECORD_FIELDS, (block[base], block[base + 1], block[base + 2],
                                             block[base + 3], block[base + 4]))))
    return rows


def check_reset_pulses(run: dict) -> None:
    """Raise ValueError unless every pulse asserted $1804 bit 1, released it, and left the bus free."""
    cycle = run.get("cycle")
    for index, row in enumerate(pulse_records(run)):
        if row["readback_asserted"] != RST_BIT:
            raise ValueError(f"{cycle}: pulse {index}: $1804 read back {row['readback_asserted']:#04x} "
                             f"while asserted, expected {RST_BIT:#04x} (reset not asserted)")
        if row["readback_released"] != 0:
            raise ValueError(f"{cycle}: pulse {index}: $1804 still {row['readback_released']:#04x} after release")
        if row["wait_result"] != 0:
            raise ValueError(f"{cycle}: pulse {index}: bus not free after reset (bus-free wait timed out)")
        if row["bus_released"] & PHASE_MASK:
            raise ValueError(f"{cycle}: pulse {index}: bus busy right after release ({row['bus_released']:#04x})")


def reset_observations(run: dict) -> list:
    """Raw per-pulse values for the evidence file (no verdict)."""
    return pulse_records(run)
