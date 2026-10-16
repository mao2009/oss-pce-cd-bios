"""CD_READ ($03) contract ledger check + transport-layer outcome model (Issue #12).

Only the *transport* layer (guest-driven SCSI READ(6) through $1800-$1804) can be VERIFIED, and only by
a real Geargrafx run of tests/integration/geargrafx_cdread.py. That run writes
build/<mode>/cdread-probe/cdread-evidence.json; this module accepts it only if it is PASS, was produced by
the pinned Geargrafx archive, belongs to the ROM on disk, and records every cited scenario as PASS.
Source text that merely mentions a scenario is never evidence. Without such a bundle VERIFIED claims are
reported BLOCKED; a bundle that lacks a cited scenario makes them UNKNOWN. The System Card 3.0 *callee*
ABI (spec/api_contracts.json slot 3) stays HYPOTHESIS/UNKNOWN.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "tests/integration/geargrafx_cdread.py"
PINNED = json.loads((ROOT / "tools/dependencies.json").read_text(encoding="utf-8"))["geargrafx"]
GEARGRAFX = f"geargrafx@{PINNED['revision']}"
SECTOR_BYTES = 2048
LBA_LIMIT = 1 << 21          # READ(6) LBA field: byte1 bits 0-4, byte2, byte3
VERIFIED_COUNTS = range(1, 4)  # only counts 1..3 were executed on the real core
STATUSES = {"VERIFIED", "HYPOTHESIS", "UNKNOWN"}

# Transport claims. VERIFIED is effective only with a matching PASS evidence bundle (see effective_status).
TRANSPORT_CLAIMS = [
    {"id": "T1", "status": "VERIFIED", "evidence": GEARGRAFX,
     "claim": "READ(6) LBA n on the fixture (one MODE1/2352 track, INDEX 01 00:00:00) delivers BIN sector n user data (2048 bytes)",
     "scenarios": ["single_sector_lba5", "first_sector_lba0", "last_sector_lba15", "wrong_sector_requested_lba6"]},
    {"id": "T2", "status": "VERIFIED", "evidence": GEARGRAFX,
     "claim": "count 1..3 delivers count consecutive sectors in LBA order, count*2048 bytes",
     "scenarios": ["three_sectors_lba5", "disc_end_boundary_lba13_x3"]},
    {"id": "T3", "status": "VERIFIED", "evidence": GEARGRAFX,
     "claim": "start LBA >= disc end: STATUS phase instead of DATA_IN, 0 bytes, destination untouched",
     "scenarios": ["invalid_sector_lba16", "invalid_sector_lba_max"]},
    {"id": "T4", "status": "VERIFIED", "evidence": GEARGRAFX,
     "claim": "request running off the disc end: sectors up to the end are delivered intact, then STATUS phase",
     "scenarios": ["read_past_disc_end_lba14_x3"]},
    {"id": "T5", "status": "VERIFIED", "evidence": GEARGRAFX,
     "claim": "READ(6) on a CD-DA LBA is not an error in Geargrafx (GOOD + 2048 raw PCM bytes); emulator behaviour only",
     "scenarios": ["read6_on_audio_track_lba"]},
    {"id": "T6", "status": "HYPOTHESIS", "evidence": "Geargrafx source reading only; guest never reads the byte",
     "claim": "SCSI status byte on the end-of-disc paths is GOOD (T3) / CHECK CONDITION (T4)", "scenarios": []},
    {"id": "T7", "status": "UNKNOWN", "evidence": "Geargrafx does not run the card without media (NOT_TESTED)",
     "claim": "no-disc / zero-sector behaviour", "scenarios": []},
    {"id": "T8", "status": "UNKNOWN", "evidence": "not executed",
     "claim": "READ(6) count 0 (SCSI: 256 sectors), counts > 3, drive busy/spun-down, retry policy, real hardware",
     "scenarios": []},
]


def transport_outcome(disc_sectors: int, lba: int, count: int) -> tuple:
    """(sectors delivered, ended early by STATUS phase) per T1-T4.

    Raises ValueError outside the executed domain instead of guessing (T7/T8).
    """
    if disc_sectors < 1:
        raise ValueError("UNKNOWN: no-disc/zero-sector behaviour was never executed (T7)")
    if not 0 <= lba < LBA_LIMIT:
        raise ValueError("READ(6) LBA is 21 bits")
    if count not in VERIFIED_COUNTS:
        raise ValueError("UNKNOWN: only counts 1..3 were executed on the real core (T8)")
    delivered = max(0, min(count, disc_sectors - lba))
    return delivered, delivered < count


def provenance_problem(bundle, rom_sha256):
    """Why the bundle cannot back VERIFIED claims, or None when it can."""
    if bundle is None:
        return "no real-core evidence bundle (run make dev-cdread-integration)"
    if bundle.get("status") != "PASS":
        return "evidence bundle status is not PASS"
    if bundle.get("geargrafx_revision") != PINNED["revision"] or bundle.get("source_archive_sha256") != PINNED["sha256"]:
        return "evidence bundle was not produced by the pinned Geargrafx"
    if rom_sha256 is None or bundle.get("rom_sha256") != rom_sha256:
        return "evidence bundle does not match the ROM on disk"
    if not isinstance(bundle.get("tests"), dict):
        return "evidence bundle has no tests"
    return None


def effective_status(bundle=None, rom_sha256=None, claims=TRANSPORT_CLAIMS) -> dict:
    """Status per claim id after evidence.

    VERIFIED only when a valid bundle records every cited scenario as PASS; BLOCKED when there is no
    usable bundle; UNKNOWN when a valid bundle does not cover the cited scenarios.
    """
    problem = provenance_problem(bundle, rom_sha256)
    out = {}
    for claim in claims:
        if claim["status"] != "VERIFIED":
            out[claim["id"]] = claim["status"]
        elif problem:
            out[claim["id"]] = "BLOCKED"
        else:
            tests = bundle["tests"]
            covered = all(str(tests.get(s, {}).get("status", "")).startswith("PASS") for s in claim["scenarios"])
            out[claim["id"]] = "VERIFIED" if covered else "UNKNOWN"
    return out


def validate(api_contracts: dict, claims=TRANSPORT_CLAIMS) -> None:
    """Raise ValueError on malformed claims, a VERIFIED claim outside the pinned emulator, or a callee ABI promotion."""
    for claim in claims:
        if claim["status"] not in STATUSES or not claim.get("evidence") or not claim.get("claim"):
            raise ValueError(f"{claim.get('id')}: status/evidence/claim required")
        if claim["status"] == "VERIFIED" and (claim["evidence"] != GEARGRAFX or not claim["scenarios"]):
            raise ValueError(f"{claim['id']}: VERIFIED needs the pinned Geargrafx and cited real-core scenarios")
    api = api_contracts["apis"][3]
    if api["candidate_name"] != "CD_READ" or api["candidate_entry"] != "0xE009":
        raise ValueError("slot 3 is not the CD_READ candidate")
    if api["verification"]["executed_callee"] or api["implementation_status"] != "NOT_STARTED":
        raise ValueError("CD_READ callee executed/implemented: update docs/cd-read-contract.md first")
    bad = [k for k, v in api["contract"].items() if v["status"] not in STATUSES - {"VERIFIED"}]
    if bad:
        raise ValueError(f"callee ABI fields promoted without executed-callee evidence: {bad}")


def real_core_requests(integration_text: str) -> list:
    """(name, lba, count, transferred bytes or None) for positive and end-of-disc (error 5) real-core runs."""
    rows = [(n, int(l, 0), int(c, 0), None) for n, l, c in
            re.findall(r'suite\.positive\("(\w+)", (\w+), (\w+)\)', integration_text)]
    for n, l, c, k, unit in re.findall(
            r'suite\.failing_guest\("(\w+)", suite\.variant\([^)]*\), (\w+), (\w+), 5, 5,\s*(\d+)( \* SECTOR_BYTES)?\)',
            integration_text):
        rows.append((n, int(l, 0), int(c, 0), int(k) * (SECTOR_BYTES if unit else 1)))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    mode = parser.parse_args(argv).mode
    api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
    validate(api)
    out = ROOT / "build" / mode / "cdread-probe"
    rom = out / "cdread-probe-not-bios.pce"
    evidence = out / "cdread-evidence.json"
    bundle = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else None
    rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest() if rom.exists() else None
    status = effective_status(bundle, rom_sha)
    declared = {c["id"]: c["status"] for c in TRANSPORT_CLAIMS}
    downgraded = {k: v for k, v in status.items() if v != declared[k]}
    if downgraded:
        reason = provenance_problem(bundle, rom_sha) or "evidence bundle does not cover the cited scenarios"
        print(f"BLOCKED CD_READ contract ledger: downgraded {downgraded}; reason: {reason}")
        sys.exit(3)
    print(f"PASS CD_READ contract ledger: transport {sorted(status.items())} backed by {evidence}; "
          "callee ABI not VERIFIED (CD_READ NOT_IMPLEMENTED)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError) as error:
        raise SystemExit(f"CD_READ contract FAIL: {error}") from error
