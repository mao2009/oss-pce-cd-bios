#!/usr/bin/env python3
"""Validator for the Phase ABI-1 reference review ledger.

``spec/abi1_reference_review.json`` records what the **static** audit of the
private reference System Card BIOS established, what it did not establish, and
why no reference execution was performed.

Two axes are kept apart, because conflating them is what would over-claim:

* the **static review** axis. A static profile of the reference callee is an
  observation *of the private image*, not of a call. It can say "this callee
  performs no store that the guarded probe could not contain". It can never
  say "this call may be executed".
* the **verification** axis. ``OBSERVED`` and ``PARTIAL`` describe a
  *reproducible* real core observation, and ``VERIFIED`` describes a promoted,
  owner approved one. A static review never reaches either state, and a prior
  local only observation never reaches them either.

The validator therefore refuses a ledger that:

* claims ``VERIFIED`` (the reference ABI axis does not represent it, #171);
* claims ``OBSERVED`` or ``PARTIAL`` without a reproducible real core
  observation, or keeps a reproducible observation while the state says
  ``NOT_RUN``;
* records an API outside the Phase ABI-1 scope, or counts one API twice;
* records an aggregate that does not match the ledger it summarises;
* records an execution permission other than ``BLOCKED``, so that a static
  ``EXECUTION_SAFE`` verdict can never be read as a permission to call the
  reference BIOS;
* drifts against ``spec/api_contracts.json``;
* publishes a decoded instruction or a non public address;
* publishes any per slot detail derived from the private image (instruction
  counts, hazard counts, store classes, static verdicts, item prose). The
  public ledger keeps management state only (Issue #324, owner ruling A).

The validator is host only. It never opens a BIOS, an emulator or a ROM.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "spec" / "abi1_reference_review.json"
CONTRACTS_PATH = ROOT / "spec" / "api_contracts.json"
DEV_LEDGER_PATH = ROOT / "spec" / "dev_implementations.json"

SCHEMA = "oss-pce-cd-bios/abi1-reference-review/v1"
TOP_KEYS = {"schema", "phase", "release_eligible", "trust", "target", "method",
            "scope", "summary", "apis"}
# Identity of the private image (hashes, checksums, sizes) is never published (owner ruling).
TARGET_KEYS = {"image"}
METHOD_KEYS = {"static_audit_tool", "decoder", "core_revision", "execution",
               "execution_blockers", "evidence_classes", "verification_states",
               "independent_approval_procedure"}
SCOPE_KEYS = {"priority_api_ids", "note"}
SUMMARY_KEYS = {"reproducible_real_core_observations", "reference_abi_verified",
                "dev_rom_implemented_apis", "owner_approved_dev_callable"}
# No field records that an analysis of the private image was performed (owner ruling: such metadata is not published).
API_KEYS = {"api_id", "slot", "name", "safety", "observation", "abi_state"}
SAFETY_KEYS = {"scope", "execution_permission", "execution_permission_basis",
               "approval_requirement"}
OBSERVATION_KEYS = {"reproducible_real_core", "note"}

# Keys that carry detail derived from the private image. None may appear anywhere in the
# published ledger, at any depth. The check is a negative guard, so a renamed or added
# field of the same kind cannot slip in under a fresh name without a test change.
FORBIDDEN_PUBLIC_KEYS = frozenset({
    "callee_instructions", "instructions_decoded", "states_visited", "states_without_guaranteed_return",
    "non_returning_states", "return_kinds", "returns_observed", "max_stack_bytes", "cross_page_count",
    "hazard_counts", "hazard", "store_target_classes", "zero_page_operand_stores", "register_swap_ops",
    "unresolved", "budget_exhausted", "static_items", "static_verdict", "basis", "residual_risks",
    "mpr_write", "mpr_read",
    # Entry addresses and entry-table evidence derived from the private image (owner ruling).
    "candidate_entry", "entry_evidence",
})

# The static evidence classes. ``OBSERVED_STATIC`` is an observation of the
# private image, not of a call.
ITEM_STATUSES = {"OBSERVED_STATIC", "SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
# The verification axis. NOT_RUN and BLOCKED are the only states a static
# review can reach.
ABI_STATES = {"NOT_RUN", "BLOCKED", "OBSERVED", "PARTIAL", "VERIFIED"}
EXECUTION_PERMISSIONS = {"BLOCKED"}
STATIC_SCOPE = "STATIC_PROFILE_ONLY"
APPROVAL_REQUIREMENT = "A_SEPARATE_INDEPENDENT_SAFETY_APPROVAL_PROCEDURE_IS_REQUIRED_AND_IS_NOT_IMPLEMENTED"
# Only NOT_RUN is accepted. OBSERVED would need an independent certification, which is not implemented.
REPRODUCIBLE_OBSERVATION = {"NOT_RUN"}
# Words that would state a result. The note of a NOT_RUN slot must not claim one.
EVIDENCE_CLAIMS = re.compile(r"OBSERVED|PARTIAL|VERIFIED|PASS|reproduced|was measured|was executed|returned")

# The Phase ABI-1 scope: the seven priority slots of the phase. A ledger that
# registers a different set of APIs is refused, so a static review cannot grow
# into a larger "reviewed" claim by accident.
PRIORITY_API_IDS = ("slot-1e", "slot-3c", "slot-3d", "slot-3f", "slot-40", "slot-41",
                    "slot-42")

# Every mnemonic of the HuC6280 instruction set that can carry an operand.
# The list is compared against the pinned cc65 opcode table by a host test, so
# a new opcode cannot slip through silently. A decoded instruction in the
# published text would be reference code, and the ledger may not carry any.
FORBIDDEN_MNEMONICS = (
    "ADC", "AND", "ASL", "BCC", "BCS", "BEQ", "BMI", "BNE", "BPL", "BRA", "BRK",
    "BVC", "BVS", "BIT", "CLC", "CLD", "CLI", "CLV", "CLA", "CLX", "CLY", "CMP",
    "CPX", "CPY", "CSH", "CSL", "DEC", "DEY", "DEX", "EOR", "INC", "INX", "INY",
    "JMP", "JSR", "LDA", "LDX", "LDY", "LSR", "NOP", "ORA", "PHA", "PHP", "PHX",
    "PHY", "PLA", "PLP", "PLX", "PLY", "ROL", "ROR", "RTI", "RTS", "SBC", "SEC",
    "SED", "SEI", "SET", "SAX", "SAY", "SXY", "ST0", "ST1", "ST2", "STA", "STX",
    "STY", "STZ", "TAM", "TMA", "TAX", "TAY", "TDD", "TIA", "TAI", "TII", "TIN",
    "TRB", "TSB", "TST", "TSX", "TXA", "TXS", "TYA",
) + tuple(f"RMB{index}" for index in range(8)) + tuple(f"SMB{index}" for index in range(8)) \
  + tuple(f"BBR{index}" for index in range(8)) + tuple(f"BBS{index}" for index in range(8))
FORBIDDEN_INSTRUCTION = re.compile(
    r"\b(?:" + "|".join(sorted(FORBIDDEN_MNEMONICS)) + r")\s+(?:\$|#|\()",
    re.IGNORECASE)
# Any four digit address in the published text must be a public one: a jump
# table entry address, or a documented CPU/hardware constant.
ADDRESS = re.compile(r"(?:\$|0x)([0-9A-Fa-f]{4})(?![0-9A-Fa-f])")
# Only documented CPU and hardware constants and the DEV-defined public entry of slot $1E. The candidate entries of
# the other slots are not public here (owner ruling, Issue #324): they are pending the source audit.
PUBLIC_ADDRESSES = {"0000", "0100", "1000", "1FFF", "2000", "2100", "3FFF",
                    "8000", "BFFF", "C000", "DFFF", "E000", "E05A", "FF80", "FFFF"}


class Invalid(Exception):
    """Raised for every ledger that must not be accepted."""


def _require(condition, message):
    if not condition:
        raise Invalid(message)


def _strings(node, path=""):
    """Every string value in the document, with its path."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{path}[{index}]")


def _public_text(node):
    """Check the published prose for reference code and internal addresses."""
    for path, text in _strings(node):
        if FORBIDDEN_INSTRUCTION.search(text):
            raise Invalid(f"{path}: published text contains a decoded instruction")
        for match in ADDRESS.finditer(text):
            if match.group(1).upper() not in PUBLIC_ADDRESSES:
                # The address itself is not echoed: a refusal log must not carry the value it refuses.
                raise Invalid(f"{path}: published text contains a non public address")


def _public_keys(node, path=""):
    """Refuse any key that carries detail derived from the private image, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_PUBLIC_KEYS:
                raise Invalid(f"{here}: private-image detail is not published")
            _public_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _public_keys(value, f"{path}[{index}]")


def compute_metrics(document, contracts, dev_ledger):
    """Derive the Phase ABI-1 aggregates from the ledger and the source ledgers.

    Nothing here is a hand written constant: the static review count and the
    observation counts come from the per API records, and the DEV counts come
    from ``spec/dev_implementations.json``.
    """
    apis = document["apis"]
    return {
        "reproducible_real_core_observations": sum(
            1 for api in apis if api["observation"]["reproducible_real_core"] == "OBSERVED"),
        "reference_abi_verified": sum(1 for api in apis if api["abi_state"] == "VERIFIED"),
        "dev_rom_implemented_apis": sum(
            1 for slot in dev_ledger["slots"] if slot.get("implementation") is not None),
        "owner_approved_dev_callable": sum(
            1 for slot in dev_ledger["slots"] if slot.get("state") == "DEV_CALLABLE"),
    }


def validate(document, contracts, dev_ledger):
    """Raise Invalid for any ledger that must not be accepted; return the ledger."""
    _require(isinstance(document, dict), "review must be an object")
    _require(set(document) == TOP_KEYS, "review keys")
    _require(document["schema"] == SCHEMA, "review schema")
    _require(document["phase"] == "ABI-1", "review phase")
    _require(document["release_eligible"] is False, "release_eligible must stay false")
    _require(document["trust"] == "LOCAL_ONLY_NOT_VERIFIED", "review trust")
    _require(isinstance(document["target"], dict) and set(document["target"]) == TARGET_KEYS,
             "target keys")
    _require(isinstance(document["method"], dict) and set(document["method"]) == METHOD_KEYS,
             "method keys")
    _require(document["method"]["execution"] == "NOT_RUN", "method execution must be NOT_RUN")
    _require(isinstance(document["method"]["execution_blockers"], list)
             and document["method"]["execution_blockers"], "method execution_blockers")
    _require(isinstance(document["method"]["evidence_classes"], dict)
             and set(document["method"]["evidence_classes"]) == ITEM_STATUSES,
             "method evidence_classes")
    _require(isinstance(document["method"]["verification_states"], dict)
             and set(document["method"]["verification_states"]) == ABI_STATES,
             "method verification_states")
    _require(document["method"]["independent_approval_procedure"] == APPROVAL_REQUIREMENT,
             "method independent_approval_procedure")

    # --- the scope of the phase ------------------------------------------------
    scope = document["scope"]
    _require(isinstance(scope, dict) and set(scope) == SCOPE_KEYS, "scope keys")
    _require(isinstance(scope["priority_api_ids"], list), "scope priority_api_ids")
    _require(tuple(scope["priority_api_ids"]) == PRIORITY_API_IDS,
             "scope priority_api_ids differs from the Phase ABI-1 scope")

    # --- the aggregates --------------------------------------------------------
    _require(isinstance(document["summary"], dict) and set(document["summary"]) == SUMMARY_KEYS,
             "summary keys")


    apis = document["apis"]
    _require(isinstance(apis, list) and apis, "apis must be a non empty list")
    seen = []
    for api in apis:
        _require(isinstance(api, dict) and set(api) == API_KEYS, "api keys")
        api_id = api["api_id"]
        _require(isinstance(api_id, str) and re.fullmatch(r"slot-[0-9a-f]{2}", api_id),
                 f"api_id {api_id!r} is malformed")
        _require(api_id not in seen, f"duplicate api_id {api_id}")
        seen.append(api_id)
        _require(api_id in PRIORITY_API_IDS,
                 f"{api_id} is outside the Phase ABI-1 scope")
        slot = api["slot"]
        _require(type(slot) is int and 0 <= slot < len(contracts["apis"]), f"{api_id}: slot range")
        _require(api_id == f"slot-{slot:02x}", f"{api_id}: api_id does not match the slot")
        claim = contracts["apis"][slot]
        _require(api["name"] == claim["candidate_name"], f"{api_id}: name differs from the claim ledger")

        # --- the permission axis: management state only --------------------------
        safety = api["safety"]
        _require(isinstance(safety, dict) and set(safety) == SAFETY_KEYS, f"{api_id}: safety keys")
        _require(safety["scope"] == STATIC_SCOPE, f"{api_id}: safety scope")
        _require(safety["execution_permission"] in EXECUTION_PERMISSIONS,
                 f"{api_id}: the execution permission of a static review is always BLOCKED")
        _require(isinstance(safety["execution_permission_basis"], str)
                 and safety["execution_permission_basis"].strip(),
                 f"{api_id}: execution permission basis")
        _require(safety["approval_requirement"] == APPROVAL_REQUIREMENT,
                 f"{api_id}: approval requirement")

        # --- the verification axis ---------------------------------------------
        observation = api["observation"]
        _require(isinstance(observation, dict) and set(observation) == OBSERVATION_KEYS,
                 f"{api_id}: observation keys")
        # No independent evidence-certification method is implemented, so a self-declared reproducible observation
        # is refused, and so is any note that makes an evidence claim (owner ruling: no self-declared promotion).
        _require(observation["reproducible_real_core"] in REPRODUCIBLE_OBSERVATION,
                 f"{api_id}: a self-declared reproducible observation is refused (no certification method)")
        _require(isinstance(observation["note"], str) and observation["note"].strip(),
                 f"{api_id}: observation note")
        _require(not EVIDENCE_CLAIMS.search(observation["note"]),
                 f"{api_id}: the observation note makes an evidence claim")
        _require(api["abi_state"] in ABI_STATES, f"{api_id}: abi state vocabulary")
        _require(api["abi_state"] != "VERIFIED",
                 f"{api_id}: VERIFIED is not reachable from a static review")
        _require(api["abi_state"] not in ("OBSERVED", "PARTIAL"),
                 f"{api_id}: {api['abi_state']} needs an independent evidence certification (not implemented)")

    _require(tuple(seen) == PRIORITY_API_IDS,
             "the reviewed APIs are not exactly the Phase ABI-1 scope")
    # The recorded aggregates are cross-checked against the ledger they
    # summarise, so a hand written or stale number cannot survive.
    metrics = compute_metrics(document, contracts, dev_ledger)
    for key, value in metrics.items():
        _require(document["summary"][key] == value,
                 f"summary.{key} is {document['summary'][key]}, the ledger says {value}")
    _public_text(document)
    _public_keys(document)
    return document


def load(review_path=REVIEW_PATH, contracts_path=CONTRACTS_PATH,
         dev_ledger_path=DEV_LEDGER_PATH):
    review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    contracts = json.loads(Path(contracts_path).read_text(encoding="utf-8"))
    dev_ledger = json.loads(Path(dev_ledger_path).read_text(encoding="utf-8"))
    validate(review, contracts, dev_ledger)
    return review, contracts, dev_ledger


def summary(document, contracts, dev_ledger):
    metrics = compute_metrics(document, contracts, dev_ledger)
    return {
        "schema": document["schema"],
        "phase": document["phase"],
        "reviewed_apis": len(document["apis"]),
        "metrics": metrics,
        "static_review_scope": "STATIC_PROFILE_ONLY",
        "execution": document["method"]["execution"],
        "execution_permission": "BLOCKED",
        "release_eligible": document["release_eligible"],
        "trust": document["trust"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the committed ledger")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("--check is required")
    try:
        document, contracts, dev_ledger = load()
    except Exception as error:  # noqa: BLE001 - fail closed; only the class is shown (an error text can carry a path)
        print(f"BLOCKED: {type(error).__name__} (details withheld)", file=sys.stderr)
        return 2
    try:
        report = summary(document, contracts, dev_ledger)
    except Exception as error:  # noqa: BLE001 - same rule: no traceback, which could show an absolute path
        print(f"BLOCKED: {type(error).__name__} (details withheld)", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
