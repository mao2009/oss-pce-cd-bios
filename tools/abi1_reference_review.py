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
* counts a prior local only observation as a reproducible one;
* records an API outside the Phase ABI-1 scope, or counts one API twice;
* records an aggregate that does not match the ledger it summarises;
* records an execution permission other than ``BLOCKED``, so that a static
  ``EXECUTION_SAFE`` verdict can never be read as a permission to call the
  reference BIOS;
* drifts against ``spec/api_contracts.json``;
* publishes a decoded instruction or a non public address.

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
            "scope", "summary", "jump_table", "apis"}
TARGET_KEYS = {"product", "image", "image_sha256", "image_bytes", "header_bytes",
               "body_bytes", "bank_bytes", "bank_count"}
METHOD_KEYS = {"static_audit_tool", "decoder", "core_revision", "execution",
               "execution_blockers", "evidence_classes", "verification_states",
               "independent_approval_procedure"}
SCOPE_KEYS = {"priority_api_ids", "note"}
SUMMARY_KEYS = {"static_review_apis", "prior_local_only_observations",
                "reproducible_real_core_observations", "reference_abi_verified",
                "dev_rom_implemented_apis", "owner_approved_dev_callable"}
JUMP_TABLE_KEYS = {"first_entry_cpu", "entry_count", "entry_stride_bytes",
                   "all_entries_are_jmp_abs", "all_targets_distinct", "status", "note"}
API_KEYS = {"api_id", "slot", "name", "candidate_entry", "entry_evidence", "static",
            "static_items", "safety", "observation", "abi_state"}
STATIC_KEYS = {"status", "callee_instructions", "return_kinds", "max_stack_bytes",
               "hazard_counts", "store_target_classes", "zero_page_operand_stores",
               "register_swap_ops", "non_returning_states", "unresolved",
               "budget_exhausted"}
SAFETY_KEYS = {"static_verdict", "scope", "basis", "residual_risks",
               "execution_permission", "execution_permission_basis",
               "approval_requirement"}
OBSERVATION_KEYS = {"reproducible_real_core", "prior_local_only", "prior_evidence",
                    "note"}
ITEM_KEYS = {"entry_address_and_basis", "caller_supplied_registers",
             "caller_preconditions", "expected_return_value",
             "possibly_changed_registers", "cpu_flag_handling", "stack_usage",
             "mpr_change", "ram_io_vdc_cd_side_effects",
             "interrupt_or_timing_dependence",
             "executable_conditions_and_blocked_conditions"}

# The static evidence classes. ``OBSERVED_STATIC`` is an observation of the
# private image, not of a call.
ITEM_STATUSES = {"OBSERVED_STATIC", "SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
# The verification axis. NOT_RUN and BLOCKED are the only states a static
# review can reach.
ABI_STATES = {"NOT_RUN", "BLOCKED", "OBSERVED", "PARTIAL", "VERIFIED"}
# A static verdict about the callee's side effect profile. It is not a
# permission: the only permission this ledger can express is BLOCKED.
STATIC_VERDICTS = {"EXECUTION_SAFE", "EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE",
                   "EXECUTION_BLOCKED"}
EXECUTION_PERMISSIONS = {"BLOCKED"}
STATIC_SCOPE = "STATIC_PROFILE_ONLY"
APPROVAL_REQUIREMENT = "A_SEPARATE_INDEPENDENT_SAFETY_APPROVAL_PROCEDURE_IS_REQUIRED_AND_IS_NOT_IMPLEMENTED"
REPRODUCIBLE_OBSERVATION = {"NOT_RUN", "OBSERVED"}
PRIOR_LOCAL_ONLY = {"ABSENT", "PRESENT"}
HAZARD_NAMES = {"BLOCK_TRANSFER", "VDC_DIRECT_WRITE", "MPR_WRITE", "MPR_READ",
                "IRQ_ENABLE", "IRQ_DISABLE", "ZERO_PAGE_STORE", "PAGE1_STORE",
                "OTHER_STORE", "DATA_DEPENDENT_STORE", "CROSS_PAGE_CALL",
                "INDIRECT_CONTROL_FLOW", "UNRESOLVED_TARGET", "NO_RETURN"}
ENTRY_EVIDENCE = {"JUMP_TABLE_3BYTE_JMP"}

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
PUBLIC_ADDRESSES = {"0000", "0100", "1000", "1FFF", "2000", "2100", "3FFF",
                    "8000", "BFFF", "C000", "DFFF", "E000", "E05A", "E0B4", "E0B7",
                    "E0BD", "E0C0", "E0C3", "E0C6", "FF80", "FFFF"}


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
                raise Invalid(f"{path}: published text contains a non public address "
                              f"${match.group(1).upper()}")


def compute_metrics(document, contracts, dev_ledger):
    """Derive the Phase ABI-1 aggregates from the ledger and the source ledgers.

    Nothing here is a hand written constant: the static review count and the
    observation counts come from the per API records, and the DEV counts come
    from ``spec/dev_implementations.json``.
    """
    apis = document["apis"]
    return {
        "static_review_apis": sum(1 for api in apis if api["static"]["status"] == "OBSERVED_STATIC"),
        "prior_local_only_observations": sum(
            1 for api in apis if api["observation"]["prior_local_only"] == "PRESENT"),
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
    digest = document["target"]["image_sha256"]
    _require(isinstance(digest, str) and len(digest) == 64
             and all(c in "0123456789abcdef" for c in digest), "image_sha256 must be lowercase sha256")
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

    table = document["jump_table"]
    _require(isinstance(table, dict) and set(table) == JUMP_TABLE_KEYS, "jump_table keys")
    _require(table["first_entry_cpu"] == "0xE000", "jump_table first entry")
    _require(table["entry_count"] == 81 and table["entry_stride_bytes"] == 3,
             "jump_table geometry")
    _require(table["all_entries_are_jmp_abs"] is True, "jump_table entries must be JMP abs")
    _require(table["all_targets_distinct"] is True, "jump_table targets must be distinct")
    _require(table["status"] == "OBSERVED_STATIC", "jump_table status")

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
        _require(api["candidate_entry"] == claim["candidate_entry"] == f"0x{0xE000 + 3 * slot:04X}",
                 f"{api_id}: candidate entry differs from the claim ledger")
        _require(api["entry_evidence"] in ENTRY_EVIDENCE, f"{api_id}: entry evidence vocabulary")

        # --- the static review axis --------------------------------------------
        static = api["static"]
        _require(isinstance(static, dict) and set(static) == STATIC_KEYS, f"{api_id}: static keys")
        _require(static["status"] == "OBSERVED_STATIC", f"{api_id}: static status")
        _require(type(static["callee_instructions"]) is int and static["callee_instructions"] >= 0,
                 f"{api_id}: callee instructions")
        _require(isinstance(static["return_kinds"], list), f"{api_id}: return kinds")
        _require(isinstance(static["hazard_counts"], dict)
                 and set(static["hazard_counts"]) == HAZARD_NAMES, f"{api_id}: hazard names")
        _require(all(type(v) is int and v >= 0 for v in static["hazard_counts"].values()),
                 f"{api_id}: hazard counts")
        _require(isinstance(static["store_target_classes"], dict), f"{api_id}: store classes")
        _require(static["budget_exhausted"] is False, f"{api_id}: budget must not be exhausted")
        _require(type(static["non_returning_states"]) is int
                 and static["non_returning_states"] >= 0, f"{api_id}: non returning states")
        _require(isinstance(static["unresolved"], list), f"{api_id}: unresolved")
        items = api["static_items"]
        _require(isinstance(items, dict) and set(items) == ITEM_KEYS, f"{api_id}: static item keys")
        for key, item in items.items():
            _require(isinstance(item, dict) and set(item) == {"status", "value"},
                     f"{api_id}: static item {key} keys")
            _require(item["status"] in ITEM_STATUSES, f"{api_id}: static item {key} status")
            _require(isinstance(item["value"], str) and item["value"].strip(),
                     f"{api_id}: static item {key} value")

        # --- the static safety axis, and the permission axis --------------------
        safety = api["safety"]
        _require(isinstance(safety, dict) and set(safety) == SAFETY_KEYS, f"{api_id}: safety keys")
        _require(safety["static_verdict"] in STATIC_VERDICTS, f"{api_id}: static verdict vocabulary")
        _require(safety["scope"] == STATIC_SCOPE,
                 f"{api_id}: a static verdict may only carry the static scope")
        _require(isinstance(safety["basis"], list) and safety["basis"], f"{api_id}: safety basis")
        _require(isinstance(safety["residual_risks"], list) and safety["residual_risks"],
                 f"{api_id}: safety residual risks")
        _require(safety["execution_permission"] in EXECUTION_PERMISSIONS,
                 f"{api_id}: the execution permission of a static review is always BLOCKED")
        _require(isinstance(safety["execution_permission_basis"], str)
                 and safety["execution_permission_basis"].strip(),
                 f"{api_id}: execution permission basis")
        _require(safety["approval_requirement"] == APPROVAL_REQUIREMENT,
                 f"{api_id}: approval requirement")
        if safety["static_verdict"] != "EXECUTION_BLOCKED":
            _require(not static["unresolved"],
                     f"{api_id}: an unresolved static profile cannot be EXECUTION_SAFE")
            _require(static["non_returning_states"] == 0,
                     f"{api_id}: a non returning path cannot be EXECUTION_SAFE")
        for hazard in ("MPR_WRITE", "VDC_DIRECT_WRITE", "BLOCK_TRANSFER", "IRQ_ENABLE",
                       "DATA_DEPENDENT_STORE", "OTHER_STORE"):
            if static["hazard_counts"][hazard]:
                _require(safety["static_verdict"] == "EXECUTION_BLOCKED",
                         f"{api_id}: {hazard} forces EXECUTION_BLOCKED")

        # --- the verification axis ---------------------------------------------
        observation = api["observation"]
        _require(isinstance(observation, dict) and set(observation) == OBSERVATION_KEYS,
                 f"{api_id}: observation keys")
        _require(observation["reproducible_real_core"] in REPRODUCIBLE_OBSERVATION,
                 f"{api_id}: reproducible observation vocabulary")
        _require(observation["prior_local_only"] in PRIOR_LOCAL_ONLY,
                 f"{api_id}: prior local only vocabulary")
        _require(isinstance(observation["note"], str) and observation["note"].strip(),
                 f"{api_id}: observation note")
        prior = observation["prior_evidence"]
        _require(isinstance(prior, str) and prior.strip(), f"{api_id}: prior evidence")
        if observation["prior_local_only"] == "PRESENT":
            _require(prior.strip().upper() != "NONE",
                     f"{api_id}: a prior observation has to name its source")
            source = (ROOT / prior.strip()).resolve()
            _require(source.is_file() and source.is_relative_to(ROOT.resolve()),
                     f"{api_id}: the prior observation source is not a repository file")
        else:
            _require(prior.strip().upper() == "NONE",
                     f"{api_id}: a slot without a prior observation must not cite prior evidence")
        _require(api["abi_state"] in ABI_STATES, f"{api_id}: abi state vocabulary")
        _require(api["abi_state"] != "VERIFIED",
                 f"{api_id}: VERIFIED is not reachable from a static review")
        reproducible = observation["reproducible_real_core"] == "OBSERVED"
        if api["abi_state"] in ("OBSERVED", "PARTIAL"):
            _require(reproducible,
                     f"{api_id}: {api['abi_state']} needs a reproducible real core observation")
        else:
            _require(not reproducible,
                     f"{api_id}: a reproducible observation cannot stay in {api['abi_state']}")
        if observation["prior_local_only"] == "PRESENT" and not reproducible:
            _require(api["abi_state"] in ("NOT_RUN", "BLOCKED"),
                     f"{api_id}: a prior local only observation is not a verification")

    _require(tuple(seen) == PRIORITY_API_IDS,
             "the reviewed APIs are not exactly the Phase ABI-1 scope")
    # The recorded aggregates are cross-checked against the ledger they
    # summarise, so a hand written or stale number cannot survive.
    metrics = compute_metrics(document, contracts, dev_ledger)
    for key, value in metrics.items():
        _require(document["summary"][key] == value,
                 f"summary.{key} is {document['summary'][key]}, the ledger says {value}")
    _public_text(document)
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
    except (Invalid, OSError, ValueError, KeyError, TypeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    print(json.dumps(summary(document, contracts, dev_ledger), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
