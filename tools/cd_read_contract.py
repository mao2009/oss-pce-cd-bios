"""CD_READ ($03) contract ledger check + transport-layer outcome model (Issue #12).

Only the *transport* layer (guest-driven SCSI READ(6) through $1800-$1804) can be VERIFIED, and only by
a real Geargrafx run of tests/integration/geargrafx_cdread.py. That run writes
build/<mode>/cdread-probe/cdread-evidence.json; this module accepts it only if it is PASS, was produced by
the pinned Geargrafx archive, belongs to the ROM on disk, and records every cited scenario as PASS.
Source text that merely mentions a scenario is never evidence. Without such a bundle VERIFIED claims are
reported BLOCKED; a bundle that lacks a cited scenario makes them UNKNOWN; a consistent bundle makes them
LOCAL_ONLY (never VERIFIED or PASS, because the bundle is not authenticated). The System Card 3.0 *callee*
ABI (spec/api_contracts.json slot 3) stays HYPOTHESIS/UNKNOWN.

The bundle is a plain JSON file. Its status, geargrafx_revision, source_archive_sha256, rom_sha256 and tests are
SELF-DECLARED: this module checks that they are consistent with the pinned values and the ROM on disk, but it does
not authenticate who wrote them. A bundle written by a modified or hand-made run passes these checks, so the best
local result is LOCAL_ONLY and the CLI exits non-zero for it. The trust meaning matches LOCAL_ONLY in
tools/evidence_provenance.py (#125: no attestation, never compatibility evidence), but the acceptance condition is
looser: #125 also requires disc hashes, its full scenario table and primary_observations.RUN, and FAILs without them.
compat_status() takes the raw #125 inputs and runs tools/evidence_provenance.verify() itself: only a #125 PASS for the
exact artifact bytes, with main's PR/non-fast-forward rules shown active, makes a declared VERIFIED claim VERIFIED;
everything else is BLOCKED. The --compat CLI passes no attestation, so it stays BLOCKED. See
docs/cd-read-contract.md section 4.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_provenance as ep  # noqa: E402  (#125 attestation verifier; the only way past BLOCKED)
from provenance_guard import load_denylist  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "tests/integration/geargrafx_cdread.py"
PINNED = json.loads((ROOT / "tools/dependencies.json").read_text(encoding="utf-8"))["geargrafx"]
GEARGRAFX = f"geargrafx@{PINNED['revision']}"
SECTOR_BYTES = 2048
LBA_LIMIT = 1 << 21          # READ(6) LBA field: byte1 bits 0-4, byte2, byte3
VERIFIED_COUNTS = range(1, 4)  # only counts 1..3 were executed on the real core
STATUSES = {"VERIFIED", "HYPOTHESIS", "UNKNOWN"}
EXIT_BLOCKED = 3  # same exit code as the #119 harness (BLOCKED 3)
EXIT_LOCAL_ONLY = 1  # same as tools/evidence_provenance.py (#125): anything but an attested PASS exits non-zero
TRUST_LABEL = "LOCAL_ONLY"
COMPAT_REASON = ("LOCAL_ONLY: the evidence bundle is SELF-DECLARED JSON (status, geargrafx_revision, "
                 "source_archive_sha256, rom_sha256, tests) with no authenticated runner or CI attestation; "
                 "it cannot back a compatibility result")
# The result kind each cited scenario must carry in the bundle. The kind is fixed per scenario, so a positive
# read recorded as PASS_EXPECTED_FAILURE (or a deliberate bad request recorded as PASS) never counts.
# tests/host/test_cd_read_contract.py checks this table against the integration suite's call sites.
SCENARIO_KIND = {
    "single_sector_lba5": "PASS", "first_sector_lba0": "PASS", "last_sector_lba15": "PASS",
    "three_sectors_lba5": "PASS", "disc_end_boundary_lba13_x3": "PASS",
    "wrong_sector_requested_lba6": "PASS_EXPECTED_FAILURE",
    "invalid_sector_lba16": "PASS_EXPECTED_FAILURE", "invalid_sector_lba_max": "PASS_EXPECTED_FAILURE",
    "read_past_disc_end_lba14_x3": "PASS_EXPECTED_FAILURE",
    "read6_on_audio_track_lba": "OBSERVED_EMULATOR_BEHAVIOUR",
}

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
    if not isinstance(bundle, dict):
        return "evidence bundle is not a JSON object"
    if bundle.get("status") != "PASS":
        return "evidence bundle status is not PASS"
    if bundle.get("geargrafx_revision") != PINNED["revision"] or bundle.get("source_archive_sha256") != PINNED["sha256"]:
        return "evidence bundle was not produced by the pinned Geargrafx"
    if rom_sha256 is None or bundle.get("rom_sha256") != rom_sha256:
        return "evidence bundle does not match the ROM on disk"
    tests = bundle.get("tests")
    if not isinstance(tests, dict):
        return "evidence bundle has no tests"
    if not all(isinstance(v, dict) for v in tests.values()):
        return "evidence bundle tests entries must be JSON objects"
    return None


def effective_status(bundle=None, rom_sha256=None, claims=TRANSPORT_CLAIMS) -> dict:
    """Local status per claim id after evidence. Not a compatibility result: see compat_status.

    A declared VERIFIED claim becomes LOCAL_ONLY when a consistent (but self-declared) bundle records every cited
    scenario with its kind; BLOCKED when there is no usable bundle; UNKNOWN when the bundle does not cover the
    cited scenarios. It never returns VERIFIED: that needs authenticated evidence this function does not take.
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
            covered = all(s in SCENARIO_KIND and tests.get(s, {}).get("status") == SCENARIO_KIND[s]
                          for s in claim["scenarios"])
            out[claim["id"]] = TRUST_LABEL if covered else "UNKNOWN"
    return out


# Rules that must be active on main (gh api repos/R/rules/branches/main). #125 cannot see them in the attestation.
MAIN_RULES = ("pull_request", "non_fast_forward")


def attested_problem(artifact_bytes, attestation, rom_bytes, run, fixture, repository, commit, branch_rules):
    """Why these raw inputs cannot back VERIFIED claims, or None when #125 verify() is PASS and main is protected.

    Every input is untrusted. The pins and the signer workflow come from this checkout, never from the caller.
    """
    if not isinstance(repository, str) or not ep.REPO.fullmatch(repository):
        return "expected repository missing or not owner/repo"
    if not isinstance(commit, str) or not ep.HEX40.fullmatch(commit):
        return "expected commit missing or not a 40-hex SHA"
    rules = {_rule_type(rule) for rule in branch_rules} if isinstance(branch_rules, list) else set()
    if not set(MAIN_RULES) <= rules:
        return f"main branch rules {sorted(MAIN_RULES)} not shown active (gh api repos/R/rules/branches/main)"
    if not isinstance(artifact_bytes, bytes):
        return "evidence artifact bytes missing"
    policy = {"repository": repository, "commit": commit, "signer_workflow": ep.SIGNER_WORKFLOW,
              "geargrafx_revision": PINNED["revision"], "geargrafx_sha256": PINNED["sha256"],
              "denied_sha256": load_denylist(ROOT)}
    try:
        result = ep.verify(artifact_bytes, attestation, policy, rom_bytes, run, fixture)
    except Exception as error:  # fail closed: a verifier crash is never evidence
        return f"#125 verifier raised {type(error).__name__}"
    if not isinstance(result, dict) or result.get("result") != ep.PASS:
        verdict, reasons = (result.get("result"), result.get("reasons")) if isinstance(result, dict) else (result, None)
        return f"#125 verifier result {verdict!r} is not PASS: {reasons}"
    return None


def _rule_type(rule):
    return rule.get("type") if isinstance(rule, dict) else None


def compat_status(artifact_bytes=None, attestation=None, *, rom_bytes=None, run=None, fixture=None,
                  repository=None, commit=None, branch_rules=None, claims=TRANSPORT_CLAIMS) -> dict:
    """Status for a compatibility gate, from raw #125 inputs (see attested_problem).

    A declared VERIFIED claim is VERIFIED only when the #125 verifier gives PASS for exactly these artifact bytes,
    main is protected, and the attested artifact records every cited scenario with its kind (else UNKNOWN).
    Otherwise it is BLOCKED; a self-declared bundle (#125 LOCAL_ONLY) never gets through. Other claims keep their
    declared status. This is transport-layer emulator evidence only, never a BIOS compatibility result.
    """
    if attested_problem(artifact_bytes, attestation, rom_bytes, run, fixture, repository, commit, branch_rules):
        return {c["id"]: "BLOCKED" if c["status"] == "VERIFIED" else c["status"] for c in claims}
    local = effective_status(json.loads(artifact_bytes), hashlib.sha256(rom_bytes).hexdigest(), claims)
    return {k: "VERIFIED" if v == TRUST_LABEL else v for k, v in local.items()}


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
    parser.add_argument("--compat", action="store_true", help="gate without #125 inputs: BLOCKED")
    args = parser.parse_args(argv)
    mode = args.mode
    api = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
    validate(api)
    out = ROOT / "build" / mode / "cdread-probe"
    rom = out / "cdread-probe-not-bios.pce"
    evidence = out / "cdread-evidence.json"
    bundle = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else None
    rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest() if rom.exists() else None
    if args.compat:
        print(f"BLOCKED CD_READ compatibility gate: {sorted(compat_status(bundle, rom_sha).items())}; {COMPAT_REASON}")
        sys.exit(EXIT_BLOCKED)
    status = effective_status(bundle, rom_sha)
    declared = {c["id"]: c["status"] for c in TRANSPORT_CLAIMS}
    downgraded = {k: v for k, v in status.items() if v != declared[k] and v != TRUST_LABEL}
    if downgraded:
        reason = provenance_problem(bundle, rom_sha) or "evidence bundle does not cover the cited scenarios"
        print(f"BLOCKED CD_READ contract ledger ({TRUST_LABEL}): downgraded {downgraded}; reason: {reason}")
        sys.exit(EXIT_BLOCKED)
    print(f"{TRUST_LABEL} CD_READ contract ledger (consistent, not verified, not a compatibility result): transport "
          f"{sorted(status.items())} from SELF-DECLARED bundle {evidence} (provenance not authenticated; "
          "tools/evidence_provenance.py checks the attestation); callee ABI not VERIFIED (CD_READ NOT_IMPLEMENTED)")
    sys.exit(EXIT_LOCAL_ONLY)


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError) as error:
        raise SystemExit(f"CD_READ contract FAIL: {error}") from error
