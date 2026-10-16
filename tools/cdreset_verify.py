"""Fail-closed judge for future CD_RESET ($01, candidate $E003) execution records (Issue #10).

No CD_RESET callee exists yet. This module fixes, ahead of any implementation, what an
observation must contain and which verdict it may earn, so a mock or an unpinned core can
never be reported as PASS. The contract is HYPOTHESIS (docs/cd-reset-contract.md); the best
verdict is therefore PASS_HYPOTHESIS, never plain PASS.
"""
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


def judge(record: dict) -> str:
    """Return PASS_HYPOTHESIS / OBSERVED / FAIL / SKIP / BLOCKED; raise ValueError on malformed input."""
    missing = [key for key in REQUIRED if key not in record]
    if missing:
        raise ValueError(f"CD_RESET record missing {missing}")
    if record["scenario"] not in SCENARIOS:
        raise ValueError(f"unknown CD_RESET scenario {record['scenario']!r}")
    if not isinstance(record["poll_budget"], int) or record["poll_budget"] <= 0:
        raise ValueError("poll_budget must be a positive integer")
    if record["source"] != REAL_SOURCE:
        return "SKIP"  # mock/static/HLE evidence never verifies behaviour
    if record["core_revision"] != DEPENDENCIES["geargrafx"]["revision"] or not record["rom_sha256"]:
        return "BLOCKED"  # unpinned core or unidentified ROM: not reproducible
    if record["entry_pc"] != CANDIDATE_ENTRY:
        return "FAIL"
    if record["returned"]:
        if record["return_pc"] != record["expected_return_pc"] or record["sp_exit"] != record["sp_entry"]:
            return "FAIL"  # JSR/RTS imbalance is wrong for any scenario
        if record["polls"] > record["poll_budget"]:
            return "FAIL"  # returned, but only after overrunning its own bound
    if not SCENARIOS[record["scenario"]]:
        return "OBSERVED"
    if not record["returned"] or record["bus_after"] & PHASE_MASK:
        return "FAIL"  # disc_ready must come back with the bus free
    return "PASS_HYPOTHESIS"
