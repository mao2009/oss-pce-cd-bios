#!/usr/bin/env python3
"""Non-executing slot-$00 (CD_BOOT, candidate $E000) fixtures and boot-eligibility model (Issue #290).

Three locally generated media cases (valid synthetic, absent, malformed) are built in memory from
project-authored bytes; no ROM, disc image, reference firmware or emulator is read or written. The
reference callee is never executed: its ABI is BLOCKED_ABI, so execution stays BLOCKED and this module
never reports PASS or System Card compatibility. Evidence records are self-reports (LOCAL_ONLY). They are
compared with an expectation derived from the synthetic media; unknown, missing or malformed evidence
fails closed to BLOCKED, and contradicting or spoofed evidence is REJECTED.
"""
import argparse
import hashlib

EXIT_BLOCKED = 3  # repo BLOCKED convention (tools/cdreset_slot01_fixture.py)
CASE_SCHEMA = "oss-pce-cd-bios/cdboot-slot00-case/v1"
EVIDENCE_SCHEMA = "oss-pce-cd-bios/cdboot-slot00-evidence/v1"
SLOT, ENTRY_CANDIDATE, UNKNOWN = "$00", "$E000", "UNKNOWN"
MAGIC = b"OSS-PCE-SYNTH-CD-BOOT\0"  # project-authored marker: not ISO9660 and not a System Card signature
SECTOR = 2048
MEDIA_KINDS = ("valid_synthetic", "absent", "malformed")
CASE_FIELDS = frozenset({"schema", "case_id", "slot", "entry_candidate", "media_kind", "expected_media_sha256",
                         "model_classification", "boot_target", "return_path", "stack", "irq", "execution"})
EVIDENCE_FIELDS = frozenset({"schema", "slot", "media_sha256", "boot_claimed", "error_code", "executed_callee"})
ERROR_CODES = frozenset({"NO_MEDIA", "MALFORMED"})
# model classification -> (boot expected, error code expected)
EXPECTED = {"BOOT_CANDIDATE": (True, None), "NO_MEDIA": (False, "NO_MEDIA"), "MALFORMED": (False, "MALFORMED")}
HEX = set("0123456789abcdef")


def synthetic_media(kind):
    """Project-authored media bytes built in memory: one sector, or None for no media."""
    if kind not in MEDIA_KINDS:
        raise ValueError(f"unknown media kind: {kind}")
    if kind == "absent":
        return None
    sector = (MAGIC + b"synthetic-payload-only").ljust(SECTOR, b"\0")
    if kind == "malformed":
        sector = b"\xff" * len(MAGIC) + sector[len(MAGIC):]  # magic destroyed
    return sector


def classify_media(media):
    """Independent model classification of raw media bytes (None = no media)."""
    if media is None:
        return "NO_MEDIA"
    if len(media) == SECTOR and media.startswith(MAGIC):
        return "BOOT_CANDIDATE"
    return "MALFORMED"


def _case(media_kind):
    media = synthetic_media(media_kind)
    return {"schema": CASE_SCHEMA, "case_id": f"slot00_{media_kind}", "slot": SLOT,
            "entry_candidate": ENTRY_CANDIDATE, "media_kind": media_kind,
            "expected_media_sha256": None if media is None else hashlib.sha256(media).hexdigest(),
            "model_classification": classify_media(media),
            **{field: UNKNOWN for field in ("boot_target", "return_path", "stack", "irq")},
            "execution": "NOT_RUN"}


#: The three local cases for the single slot-$00 API. Definition-only; never executed.
CASES = tuple(_case(kind) for kind in MEDIA_KINDS)


def validate_case(case):
    """Structural errors for one case; [] means well formed. Measured fields must stay UNKNOWN."""
    if not isinstance(case, dict) or set(case) != CASE_FIELDS:
        return ["invalid case fields"]
    errors = []
    if (case["schema"], case["slot"], case["entry_candidate"]) != (CASE_SCHEMA, SLOT, ENTRY_CANDIDATE):
        errors.append("unknown schema or not the single slot $00 / $E000 API")
    if case["media_kind"] not in MEDIA_KINDS:
        errors.append("media_kind must be valid_synthetic, absent or malformed")
    cls = case["model_classification"]
    if not isinstance(cls, str) or cls not in EXPECTED:
        errors.append("model_classification must be BOOT_CANDIDATE, NO_MEDIA or MALFORMED")
    elif case["media_kind"] in MEDIA_KINDS and cls != classify_media(synthetic_media(case["media_kind"])):
        errors.append("model_classification does not match its media")
    if case["media_kind"] in MEDIA_KINDS:
        media = synthetic_media(case["media_kind"])
        if case["expected_media_sha256"] != (None if media is None else hashlib.sha256(media).hexdigest()):
            errors.append("expected_media_sha256 does not match its media")
    errors +=[f"{field} must stay UNKNOWN (no evidence)" for field in ("boot_target", "return_path", "stack", "irq")
               if case[field] != UNKNOWN]
    if case["execution"] != "NOT_RUN":
        errors.append("execution must be NOT_RUN (reference slot $00 is never executed)")
    return errors


def execution_status():
    """Slot $00 execution is always BLOCKED: the reference callee ABI is BLOCKED_ABI."""
    return "BLOCKED", ["reference slot $00 callee not executed: CD_BOOT ABI BLOCKED_ABI; "
                       "no System Card compatibility claimed"]


def _is_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in HEX for c in value)


def judge(case, evidence):
    """Return (status, reasons) for a self-reported evidence record.

    BLOCKED: no record, malformed record, unknown or unrecognised result, or a claim of execution.
    REJECTED: a well-formed record that contradicts the independent expectation (spoofed media, boot
    claimed for absent or malformed media, or a missing error). MODEL_CONSISTENT: agreement with the
    non-executing model only; it is never PASS and never execution evidence.
    """
    if validate_case(case):
        return "BLOCKED", ["malformed case"]
    if evidence is None:
        return "BLOCKED", ["evidence missing: unknown result is not a pass"]
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS:
        return "BLOCKED", ["evidence fields invalid"]
    if evidence["schema"] != EVIDENCE_SCHEMA or evidence["slot"] != SLOT:
        return "BLOCKED", ["evidence schema or slot unknown"]
    if evidence["executed_callee"] is not False:
        return "BLOCKED", ["executed_callee claim: slot $00 must never run in this model"]
    if type(evidence["boot_claimed"]) is not bool:
        return "BLOCKED", ["boot_claimed must be a bool"]
    error = evidence["error_code"]
    if error is not None and (not isinstance(error, str) or error not in ERROR_CODES):
        return "BLOCKED", [f"unrecognised result {error!r}: unknown result is not a pass"]
    sha = evidence["media_sha256"]
    if sha is not None and not _is_sha(sha):
        return "BLOCKED", ["media_sha256 must be null or lowercase sha256"]
    boot, expected_error = EXPECTED[case["model_classification"]]
    if sha != case["expected_media_sha256"]:
        return "REJECTED", ["media identity mismatch: spoofed or unrelated media"]
    if evidence["boot_claimed"] is not boot:
        return "REJECTED", [f"boot_claimed contradicts model ({case['model_classification']})"]
    if error != expected_error:
        return "REJECTED", [f"error_code {error!r} contradicts model ({case['model_classification']})"]
    return "MODEL_CONSISTENT", ["self-report agrees with the non-executing model; not execution evidence"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the built-in slot $00 cases")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    errors = [f"{case['case_id']}: {error}" for case in CASES for error in validate_case(case)]
    if errors:
        print("BLOCKED: " + "; ".join(errors))
        return EXIT_BLOCKED
    print(f"NOT_RUN: {len(CASES)} slot $00 fixture cases structurally valid; definition-only, never executed")
    status, reasons = execution_status()
    print(f"{status}: " + "; ".join(reasons))
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
