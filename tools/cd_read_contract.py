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
compat_status() keeps every declared VERIFIED claim BLOCKED, whatever it is given: gh attestation output and run
records handed to a function are caller-supplied JSON, so their authenticity cannot be verified in-process (#153).
compat_reason() runs tools/evidence_provenance.verify() on such inputs with this checkout's pins only to explain the
BLOCKED. See docs/cd-read-contract.md section 4.

Issue #148 adds the caller-side usage ledger (CALLER_LEDGER): what independent caller sources write before the
call and read afterwards. It is CALLER evidence only, is pinned by revision and line, and is validated so that
no row can be promoted to VERIFIED.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_provenance as ep  # noqa: E402  (#125 verifier; diagnostics only, no path to VERIFIED)
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

# Issue #148: pinned caller-side usage ledger. Each row fixes an *independent caller source* by revision and
# line and records only what that source shows the caller doing with CD_READ ($03 / candidate $E009): the
# inputs it writes before the call and the return values it reads afterwards. This is CALLER evidence (a
# third-party SDK convention); it never proves the BIOS callee. No row is VERIFIED. direction is one of:
#   CALLER_IN   - the caller writes this input before JSR (HYPOTHESIS about the callee)
#   CALLER_OUT  - the caller reads this value from the call (HYPOTHESIS about the callee)
#   CALLER_SAVE - the caller saves/restores this itself (caller defence, not callee proof)
#   CALLER_DECLARATION - a declaration/prototype (not a runtime caller write before the call)
#   UNOBSERVED  - no cited caller reads it; stays UNKNOWN
# The human-readable twin table lives in 撤去済み文書 section 2; tests/host/
# test_cd_read_contract.py checks both against this module and spec/api_contracts.json.
HUC_REV = "54f5c73606c1e7142e8095c727cab2767e2b3cb3"
HUC_INC = "huc:include/hucc/pcengine.inc"
HUC_ASM = "huc:include/hucc/hucc-systemcard.asm"
HUC_H = "huc:include/hucc/hucc-systemcard.h"
HUC_CORE = "huc:include/hucc/core.inc"
HUC_KERNEL = "huc:include/hucc/core-kernel.asm"
HUC_STARTUP = "huc:include/hucc/core-startup.asm"
CALLER_LEDGER_DIRECTIONS = ("CALLER_IN", "CALLER_OUT", "CALLER_SAVE", "CALLER_DECLARATION", "UNOBSERVED")
CALLER_LEDGER = (
    {"id": "C01", "source": HUC_INC, "revision": HUC_REV, "line": 471, "slot": "$E009 entry", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "cd_read equated to $00:E009 (FUNC): the caller's entry candidate, not a callee observation"},
    {"id": "C02", "source": HUC_CORE, "revision": HUC_REV, "line": 42, "slot": "Y", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "system macro loads Y with the vector lo-byte: Y is the $E000 jump-table selector, not a CD_READ argument"},
    {"id": "C03", "source": HUC_KERNEL, "revision": HUC_REV, "line": 509, "slot": "Y", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "call_bios JSRs $E000 with Y=selector; the dispatcher is BIOS code, unobserved here"},
    {"id": "C04", "source": HUC_INC, "revision": HUC_REV, "line": 329, "slot": "$20F8-$20FF", "direction": "CALLER_DECLARATION", "status": "HYPOTHESIS",
     "note": "pcengine.inc equates _ax/_bx/_cx/_dx at $F8:20F8-$20FF: SDK declaration/alias, not a runtime caller write before the call"},
    {"id": "C05", "source": HUC_H, "revision": HUC_REV, "line": 95, "slot": "$20F8-$20FF", "direction": "CALLER_DECLARATION", "status": "HYPOTHESIS",
     "note": "cd_fastvram/loadvram/loaddata/loadbank prototypes fix which ZP field each wrapper fills: declaration, not a runtime write"},
    {"id": "C06", "source": HUC_ASM, "revision": HUC_REV, "line": 384, "slot": "_dh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 sets _dh=0 (local bytes in _ax)"},
    {"id": "C07", "source": HUC_ASM, "revision": HUC_REV, "line": 441, "slot": "_dh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loadbank.4 sets _dh=3 (MPR3 bank mode)"},
    {"id": "C08", "source": HUC_ASM, "revision": HUC_REV, "line": 322, "slot": "_dh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loadvram.4 sets _dh=$FE (VRAM bytes, count in _ax)"},
    {"id": "C09", "source": HUC_ASM, "revision": HUC_REV, "line": 294, "slot": "_dh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_fastvram.4 sets _dh=$FF (VRAM sectors, count in _al)"},
    {"id": "C10", "source": HUC_KERNEL, "revision": HUC_REV, "line": 593, "slot": "_dh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "exec_overlay sets _dh=6 (MPR6 bank mode)"},
    {"id": "C11", "source": HUC_ASM, "revision": HUC_REV, "line": 381, "slot": "_ax", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 sets _ax to the byte count of the next chunk"},
    {"id": "C12", "source": HUC_ASM, "revision": HUC_REV, "line": 436, "slot": "_al", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loadbank.4 copies the entry _dh count into _al (0=whole file): the count field is reused"},
    {"id": "C13", "source": HUC_ASM, "revision": HUC_REV, "line": 361, "slot": "_bx", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 sets _bx as the RAM destination pointer (TII _bp->_bx)"},
    {"id": "C14", "source": HUC_KERNEL, "revision": HUC_REV, "line": 589, "slot": "_bl/_bh", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "exec_overlay sets _bl=core_1stbank and _bh=0 before _dh=6"},
    {"id": "C15", "source": HUC_KERNEL, "revision": HUC_REV, "line": 539, "slot": "_cl/_ch/_dl", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "get_file_lba fills the 3-byte LBA triple; _cl is the record-base high byte and also carried the overlay index"},
    {"id": "C16", "source": HUC_ASM, "revision": HUC_REV, "line": 594, "slot": "_dl/_ch/_cl", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "_add_sectors adds the sector offset into the LBA triple _dl/_ch/_cl"},
    {"id": "C17", "source": HUC_KERNEL, "revision": HUC_REV, "line": 585, "slot": "P.I", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "exec_overlay executes CLI before cd_read; no caller deliberately sets the other P bits"},
    {"id": "C18", "source": HUC_STARTUP, "revision": HUC_REV, "line": 125, "slot": "MPR0/MPR1", "direction": "CALLER_IN", "status": "HYPOTHESIS",
     "note": "startup maps MPR0=$FF and MPR1=$F8 as the caller's general BIOS-call prerequisite"},
    {"id": "C19", "source": HUC_KERNEL, "revision": HUC_REV, "line": 503, "slot": "MPR7", "direction": "CALLER_SAVE", "status": "HYPOTHESIS",
     "note": "call_bios saves caller MPR7 (L503-504), maps bank 0, restores it after the call (L511-514)"},
    {"id": "C20", "source": HUC_ASM, "revision": HUC_REV, "line": 346, "slot": "MPR3/MPR4", "direction": "CALLER_SAVE", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 saves and restores MPR3/MPR4 itself (caller defence, not callee proof)"},
    {"id": "C21", "source": HUC_ASM, "revision": HUC_REV, "line": 443, "slot": "MPR3", "direction": "CALLER_SAVE", "status": "HYPOTHESIS",
     "note": "_cd_loadbank.4 preserves MPR3 across the call"},
    {"id": "C22", "source": HUC_ASM, "revision": HUC_REV, "line": 386, "slot": "$20FA-$20FE", "direction": "CALLER_SAVE", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 preserves 5 bytes (_bl,_bh,_cl,_ch,_dl); it does not preserve _ax or _dh"},
    {"id": "C23", "source": HUC_ASM, "revision": HUC_REV, "line": 389, "slot": "A", "direction": "CALLER_OUT", "status": "HYPOTHESIS",
     "note": "callers TAX the return, assuming the status is in A"},
    {"id": "C24", "source": HUC_ASM, "revision": HUC_REV, "line": 390, "slot": "A/NZ", "direction": "CALLER_OUT", "status": "HYPOTHESIS",
     "note": "_cd_loaddata.4 branches on NZ after its own TAX, not on a BIOS flag"},
    {"id": "C25", "source": HUC_KERNEL, "revision": HUC_REV, "line": 600, "slot": "A", "direction": "CALLER_OUT", "status": "HYPOTHESIS",
     "note": "exec_overlay CMPs A with 0 and retries: 0 is assumed success"},
    {"id": "C26", "source": HUC_ASM, "revision": HUC_REV, "line": 300, "slot": "Y:A", "direction": "CALLER_OUT", "status": "HYPOTHESIS",
     "note": "caller return-shaping: CLY zeroes Y then LEAVE moves X to A; the caller never reads callee Y, so this is not callee-output evidence"},
    {"id": "C27", "source": HUC_ASM, "revision": HUC_REV, "line": 448, "slot": "MPR3", "direction": "CALLER_SAVE", "status": "HYPOTHESIS",
     "note": "_cd_loadbank.4 restores MPR3 after the call; its comment asserts MPR3 is destroyed on a CD error (callee unverified)"},
    {"id": "C28", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "X", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "callers overwrite X with TAX, so the callee's X is never observed"},
    {"id": "C29", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "Y", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "Y is consumed as the selector; no caller reads Y after the call"},
    {"id": "C30", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "P (N/V/Z/C/D/B/T)", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "no caller branches on a BIOS flag; branches use the caller's own TAX/CMP result"},
    {"id": "C31", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "S (stack)", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "only JSR/RTS are visible at the call site; callee stack depth/balance unobserved"},
    {"id": "C32", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "MPR0-2,5,6", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "no caller reads these MPRs after cd_read; only MPR3/MPR4 are defended"},
    {"id": "C33", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "IRQ/completion", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "callers neither wait on nor observe an interrupt/completion signal"},
    {"id": "C34", "source": HUC_ASM, "revision": HUC_REV, "line": 388, "slot": "$20F8-$20F9,$20FF", "direction": "UNOBSERVED", "status": "UNKNOWN",
     "note": "the caller re-sets _ax and _dh each loop; callee writes to them are unobserved"},
)

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
        cid, status = claim["id"], claim["status"]  # read once: a Mapping may change between accesses
        if type(status) is not str or status not in ("VERIFIED", "HYPOTHESIS", "UNKNOWN"):
            out[cid] = "BLOCKED"  # abnormal status object or unlisted state: fail closed, never pass through
        elif status != "VERIFIED":
            out[cid] = status
        elif problem:
            out[cid] = "BLOCKED"
        else:
            tests = bundle["tests"]
            covered = all(s in SCENARIO_KIND and tests.get(s, {}).get("status") == SCENARIO_KIND[s]
                          for s in claim["scenarios"])
            out[cid] = TRUST_LABEL if covered else "UNKNOWN"
    return out


REPOSITORY = "mao2009/oss-pce-cd-bios"  # the only producer #125 may accept; a fork never matches
IN_PROCESS_REASON = ("BLOCKED: attestation authenticity cannot be checked in-process. The #125 verifier found the "
                     "supplied gh attestation output and run record consistent, but they are caller-supplied JSON "
                     "(gh output is not a signature this function can check), so the gate stays BLOCKED")
DECLARED_UNPROVEN = frozenset({"HYPOTHESIS", "UNKNOWN"})


def _no_status_words(text):
    """#125 reasons quote result kinds; the gate's diagnostics must never read as an acceptance."""
    return re.sub(r"\b(PASS|VERIFIED)\b", "<accepted-kind>", text, flags=re.I)


def compat_reason(artifact_bytes=None, attestation=None, rom_bytes=None, run=None) -> str:
    """Why the gate is BLOCKED for these inputs: a #125 finding, or IN_PROCESS_REASON when #125 finds them consistent.

    Diagnostic only; it never changes compat_status(). Repository, commit (git HEAD of this checkout), Geargrafx pins,
    signer workflow, denylist and the regenerated fixture hashes come from this checkout, never from the caller.
    """
    if not isinstance(artifact_bytes, bytes):
        return ("#125 verifier could not run: artifact_bytes missing or not bytes "
                "(call compat_reason(artifact_bytes, attestation, rom_bytes, run))")
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        policy = {"repository": REPOSITORY, "commit": commit, "signer_workflow": ep.SIGNER_WORKFLOW,
                  "geargrafx_revision": PINNED["revision"], "geargrafx_sha256": PINNED["sha256"],
                  "denied_sha256": load_denylist(ROOT)}
        result = ep.verify(artifact_bytes, attestation, policy, rom_bytes, run, ep.recompute_fixture())
    except Exception as error:  # fail closed: a verifier crash is never evidence
        return _no_status_words(f"#125 verifier could not run: {type(error).__name__}: {error}")
    if not isinstance(result, dict):
        return "#125 verifier could not run: it returned a non-dict result"
    if result.get("result") == ep.PASS:
        return IN_PROCESS_REASON
    return _no_status_words(f"#125 verifier result {result.get('result')!r}: {result.get('reasons')}")


def compat_status(bundle=None, rom_sha256=None, claims=TRANSPORT_CLAIMS) -> dict:
    """Status for a compatibility gate. Every declared VERIFIED claim is BLOCKED, whatever the inputs.

    No data a caller can hand to this function (bundle, gh attestation output, run record, branch rules) proves who
    produced it, so nothing here reaches VERIFIED (see compat_reason and docs/cd-read-contract.md section 4).
    A claim keeps its declared status only when it is a plain dict whose status, read once, is exactly the str
    HYPOTHESIS or UNKNOWN; anything else (VERIFIED, look-alike strings, str subclasses, other mappings) is BLOCKED.
    """
    out = {}
    for claim in claims:
        status = claim.get("status") if type(claim) is dict else None
        out[claim["id"]] = status if type(status) is str and status in DECLARED_UNPROVEN else "BLOCKED"
    return out


def validate_caller_ledger(rows=CALLER_LEDGER, api_contracts=None) -> None:
    """Raise ValueError unless every #148 caller-ledger row pins a real caller source and stays unverified.

    A row must name a `source` key present in spec/api_contracts.json with the same pinned `revision`, that
    source must be a CALLER_SOURCE (an HLE or emulator source is not caller evidence), `line` must be a
    positive integer at that revision, `direction` must be known, and `status` must be UNKNOWN only for an
    UNOBSERVED row and HYPOTHESIS otherwise. No caller row can be VERIFIED or carry any other state.
    """
    if api_contracts is None:
        api_contracts = json.loads((ROOT / "spec/api_contracts.json").read_text(encoding="utf-8"))
    sources = api_contracts["sources"]
    seen = set()
    for row in rows:
        rid = row.get("id")
        if type(rid) is not str or not rid or rid in seen:
            raise ValueError(f"caller ledger id missing or duplicate: {rid!r}")
        seen.add(rid)
        source = sources.get(row.get("source"))
        if not isinstance(source, dict):
            raise ValueError(f"{rid}: source {row.get('source')!r} is not a pinned source")
        if source["kind"] != "CALLER_SOURCE":
            raise ValueError(f"{rid}: {row['source']} is not caller evidence")
        if row.get("revision") != source["revision"]:
            raise ValueError(f"{rid}: revision does not pin {row['source']}")
        if type(row.get("line")) is not int or row["line"] < 1:
            raise ValueError(f"{rid}: line must be a positive integer at the pinned revision")
        if row.get("direction") not in CALLER_LEDGER_DIRECTIONS:
            raise ValueError(f"{rid}: unknown direction {row.get('direction')!r}")
        expected = "UNKNOWN" if row["direction"] == "UNOBSERVED" else "HYPOTHESIS"
        if row.get("status") != expected:
            raise ValueError(f"{rid}: {row['direction']} rows must be {expected}")
        if type(row.get("note")) is not str or not row["note"]:
            raise ValueError(f"{rid}: note required")


# Issue #188: measured contract for ONE transfer mode (`_dh` = 2, destination RAM bank `_bl`) and ONE sector.
# Real-core evidence is the READ(6) transport layer only (T1-T4 scenarios). The callee rows (R*) are never measured:
# the callee gate (#173, #365-#367) is closed, so the genuine $E009 is not run (CALLEE_REAL_CORE_RUNS = 0). Every R
# row stays HYPOTHESIS or UNKNOWN. A READ(6) example is never evidence for the callee ABI.
RAM_MODE = 2
CALLEE_REAL_CORE_RUNS = 0
FIXTURE_SECTORS = 16  # tools/make_cd_image.py DEFAULT_SECTORS (the real-core fixture size)
RAM_MODE_ITEMS = [
    {"id": "R1", "item": "entry JSR $E009 with slot $03", "status": "HYPOTHESIS", "source": "HuCC pcengine.inc"},
    {"id": "R2", "item": "block number 24-bit _cl:_ch:_dl ($20FC-$20FE)", "status": "HYPOTHESIS", "source": "HuCC callers"},
    {"id": "R3", "item": "_dh=2 selects RAM bank _bl, _al sectors", "status": "UNKNOWN", "source": "Hu-Go HLE only (license unconfirmed, not a basis; HuCC include/hucc/ does not write _dh=2)"},
    {"id": "R4", "item": "destination address in the bank (register pair, end address)", "status": "UNKNOWN", "source": None},
    {"id": "R5", "item": "block base: CD_BASE-relative (#11) or absolute LBA", "status": "UNKNOWN", "source": None},
    {"id": "R6", "item": "A=0 on success", "status": "HYPOTHESIS", "source": "HuCC caller expectation (A=0 treated as success at C23/C24: hucc-systemcard.asm TAX/BNE); callee return value unmeasured; Hu-Go not a basis (license unconfirmed)"},
    {"id": "R7", "item": "error codes (no disc, bad block, read error, busy)", "status": "UNKNOWN", "source": None},
    {"id": "R8", "item": "N/Z/C/T on return", "status": "UNKNOWN", "source": "Hu-Go HLE only (license unconfirmed, not a basis)"},
    {"id": "R9", "item": "MPR state after success and after error (bank _bl mapping)", "status": "UNKNOWN", "source": None},
    {"id": "R10", "item": "stack: callee depth and JSR/RTS balance", "status": "UNKNOWN", "source": None},
    {"id": "R11", "item": "X/Y and $20F8-$20FF preservation", "status": "UNKNOWN", "source": None},
    {"id": "R12", "item": "interrupt prerequisite (CLI before call)", "status": "UNKNOWN", "source": None},
]
# Transport examples: `measured_by` names the real-core scenario (bus behaviour only). None = not measured, so the
# expectation is UNKNOWN and transport_outcome must refuse the input.
RAM_MODE_EXAMPLES = [
    {"id": "P1", "polarity": "positive", "lba": 5, "count": 1, "expect": (1, False), "measured_by": "single_sector_lba5"},
    {"id": "P2", "polarity": "positive", "lba": 0, "count": 1, "expect": (1, False), "measured_by": "first_sector_lba0"},
    {"id": "P3", "polarity": "positive", "lba": 15, "count": 1, "expect": (1, False), "measured_by": "last_sector_lba15"},
    {"id": "N1", "polarity": "negative", "lba": 16, "count": 1, "expect": (0, True), "measured_by": "invalid_sector_lba16"},
    {"id": "N2", "polarity": "negative", "lba": LBA_LIMIT - 1, "count": 1, "expect": (0, True),
     "measured_by": "invalid_sector_lba_max"},
    {"id": "N3", "polarity": "negative", "lba": 5, "count": 0, "expect": None, "measured_by": None},
    {"id": "N4", "polarity": "negative", "lba": 5, "count": 4, "expect": None, "measured_by": None},
]


def validate_ram_mode_contract(items=RAM_MODE_ITEMS, examples=RAM_MODE_EXAMPLES) -> None:
    """Raise ValueError unless the #188 one-sector RAM-mode contract stays unpromoted and its examples match the model."""
    if CALLEE_REAL_CORE_RUNS != 0:
        raise ValueError("callee executed on the real core: gate #173 is not satisfied, update the contract first")
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate R id")
    for item in items:
        if item["status"] not in STATUSES - {"VERIFIED"}:
            raise ValueError(f"{item['id']}: callee item promoted to {item['status']}")
    for ex in examples:
        if ex["polarity"] not in ("positive", "negative"):
            raise ValueError(f"{ex['id']}: polarity must be positive or negative")
        if ex["measured_by"] is None:
            if ex["expect"] is not None:
                raise ValueError(f"{ex['id']}: unmeasured example must have expect None (UNKNOWN)")
            try:
                transport_outcome(FIXTURE_SECTORS, ex["lba"], ex["count"])
            except ValueError:
                continue
            raise ValueError(f"{ex['id']}: transport_outcome must refuse an unexecuted input")
        kind = SCENARIO_KIND.get(ex["measured_by"])
        want_kind = "PASS" if ex["polarity"] == "positive" else "PASS_EXPECTED_FAILURE"
        if kind != want_kind:
            raise ValueError(f"{ex['id']}: scenario kind {kind!r} is not {want_kind!r}")
        if transport_outcome(FIXTURE_SECTORS, ex["lba"], ex["count"]) != ex["expect"]:
            raise ValueError(f"{ex['id']}: expectation does not match the transport model")


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
    validate_caller_ledger(CALLER_LEDGER, api_contracts)


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
    parser.add_argument("--compat", action="store_true", help="gate: always BLOCKED (no path to VERIFIED)")
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
    print(f"{TRUST_LABEL} CD_READ contract ledger (consistent, not authenticated, not a compatibility result): "
          f"transport "
          f"{sorted(status.items())} from SELF-DECLARED bundle {evidence} (provenance not authenticated; "
          "tools/evidence_provenance.py checks the attestation); callee ABI unproven (CD_READ NOT_IMPLEMENTED)")
    sys.exit(EXIT_LOCAL_ONLY)


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError) as error:
        raise SystemExit(f"CD_READ contract FAIL: {error}") from error
