"""Fail-closed judge for future CD_RESET ($01, candidate $E003) execution records (Issue #10).

No CD_RESET callee exists yet. This module fixes, ahead of any implementation, what an
observation must contain and which verdict it may earn, so a mock or an unpinned core can
never be reported as PASS. The contract is HYPOTHESIS (docs/cd-reset-contract.md); the best
verdict is therefore PASS_HYPOTHESIS, never plain PASS.

Malformed records (missing keys, wrong types including bool-for-int, out-of-range or negative
values, rom_sha256 that is not 64 lowercase hex) raise ValueError before any verdict is reached.
"""
import re

if __package__:
    from .environment import DEPENDENCIES
else:
    from environment import DEPENDENCIES

CANDIDATE_ENTRY = 0xE003
REAL_SOURCE = "geargrafx-real"
PHASE_MASK = 0xF8  # $1800 BSY/REQ/MSG/CD/IO; 0 = bus free (measured in Geargrafx by the #5 diagnostic)
# Scenario -> does the HYPOTHESIS contract define a pass condition? Only "drive answers the reset" does;
# the BIOS result for no-disc / busy drive is UNKNOWN, so those can only be OBSERVED (or FAIL structurally).
SCENARIOS = {"disc_ready": True, "no_disc": False, "drive_busy": False}
REQUIRED = ("source", "core_revision", "rom_sha256", "scenario", "entry_pc", "returned", "return_pc",
            "expected_return_pc", "sp_entry", "sp_exit", "bus_after", "polls", "poll_budget")
# (lo, hi) inclusive bounds per integer field; bool is rejected separately (bool is an int subclass).
PC_RANGE = (0x0000, 0xFFFF)
BYTE_RANGE = (0x00, 0xFF)
COUNT_RANGE = (0, 0xFFFFFFFF)
INT_RANGES = {"entry_pc": PC_RANGE, "return_pc": PC_RANGE, "expected_return_pc": PC_RANGE,
              "sp_entry": BYTE_RANGE, "sp_exit": BYTE_RANGE, "bus_after": BYTE_RANGE,
              "polls": COUNT_RANGE, "poll_budget": (1, 0xFFFFFFFF)}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def validate(record: dict) -> None:
    """Raise ValueError unless every REQUIRED field is present with the expected type and range."""
    missing = [key for key in REQUIRED if key not in record]
    if missing:
        raise ValueError(f"CD_RESET record missing {missing}")
    for key in ("source", "core_revision", "scenario"):
        if not isinstance(record[key], str):
            raise ValueError(f"{key} must be a string")
    if record["scenario"] not in SCENARIOS:
        raise ValueError(f"unknown CD_RESET scenario {record['scenario']!r}")
    rom = record["rom_sha256"]
    if not isinstance(rom, str) or (rom and not SHA256_HEX.fullmatch(rom)):
        raise ValueError("rom_sha256 must be empty (unidentified) or 64 lowercase hex characters")
    if type(record["returned"]) is not bool:
        raise ValueError("returned must be a bool")
    for key, (lo, hi) in INT_RANGES.items():
        value = record[key]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"{key} must be an int in [{lo:#x}, {hi:#x}], got {value!r}")


def classify(record: dict) -> tuple[str, str]:
    """Return (verdict, reason). Verdict is PASS_HYPOTHESIS / OBSERVED / FAIL / SKIP / BLOCKED.

    Reasons separate outcomes: 'timeout' = not returned and polls == poll_budget (bound exhausted),
    'not_returned' = not returned before the bound, 'budget_overrun' = polls > poll_budget.
    """
    validate(record)
    if record["source"] != REAL_SOURCE:
        return "SKIP", "non_real_source"  # mock/static/HLE evidence never verifies behaviour
    if record["core_revision"] != DEPENDENCIES["geargrafx"]["revision"]:
        return "BLOCKED", "core_unpinned"
    if not record["rom_sha256"]:
        return "BLOCKED", "rom_unidentified"  # not reproducible without a ROM identity
    if record["entry_pc"] != CANDIDATE_ENTRY:
        return "FAIL", "wrong_entry"
    if record["polls"] > record["poll_budget"]:
        return "FAIL", "budget_overrun"  # checked for every scenario, returned or not
    passes = SCENARIOS[record["scenario"]]
    if not record["returned"]:
        reason = "timeout" if record["polls"] == record["poll_budget"] else "not_returned"
        return ("FAIL" if passes else "OBSERVED"), reason
    if record["return_pc"] != record["expected_return_pc"] or record["sp_exit"] != record["sp_entry"]:
        return "FAIL", "bad_return"  # JSR/RTS imbalance is wrong for any scenario
    if not passes:
        return "OBSERVED", "returned"
    if record["bus_after"] & PHASE_MASK:
        return "FAIL", "bus_busy"  # disc_ready must come back with the bus free
    return "PASS_HYPOTHESIS", "returned"


def judge(record: dict) -> str:
    """Return PASS_HYPOTHESIS / OBSERVED / FAIL / SKIP / BLOCKED; raise ValueError on malformed input."""
    return classify(record)[0]
