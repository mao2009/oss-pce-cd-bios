#!/usr/bin/env python3
"""Validator for the Phase ABI-1 reference review ledger.

``spec/abi1_reference_review.json`` records what the **static** audit of the
private reference System Card BIOS established, what it did not establish and
why no reference execution was performed. This module keeps that ledger
honest:

* the file must match a strict schema (exact key sets, closed vocabularies);
* every candidate entry must still agree with ``spec/api_contracts.json``;
* ``VERIFIED`` may never appear: this ledger records static review, not a
  verified ABI, and the reference ABI axis refuses ``VERIFIED`` today
  (Issue #171);
* the published text may not contain a decoded instruction or an internal BIOS
  address, so the ledger can be committed without shipping reference code.

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

SCHEMA = "oss-pce-cd-bios/abi1-reference-review/v1"
TOP_KEYS = {"schema", "phase", "release_eligible", "trust", "target", "method",
            "jump_table", "apis"}
TARGET_KEYS = {"product", "image", "image_sha256", "image_bytes", "header_bytes",
               "body_bytes", "bank_bytes", "bank_count"}
METHOD_KEYS = {"static_audit_tool", "decoder", "core_revision", "execution",
               "execution_blockers", "evidence_classes"}
JUMP_TABLE_KEYS = {"first_entry_cpu", "entry_count", "entry_stride_bytes",
                   "all_entries_are_jmp_abs", "all_targets_distinct", "status", "note"}
API_KEYS = {"api_id", "slot", "name", "candidate_entry", "entry_evidence", "static",
            "static_items", "safety", "execution", "abi_state"}
STATIC_KEYS = {"status", "callee_instructions", "return_kinds", "max_stack_bytes",
               "hazard_counts", "store_target_classes", "zero_page_operand_stores",
               "register_swap_ops", "unresolved", "budget_exhausted"}
SAFETY_KEYS = {"verdict", "basis", "residual_risks"}
EXECUTION_KEYS = {"verdict", "observation_status", "note"}
ITEM_KEYS = {"entry_address_and_basis", "caller_supplied_registers",
             "caller_preconditions", "expected_return_value",
             "possibly_changed_registers", "cpu_flag_handling", "stack_usage",
             "mpr_change", "ram_io_vdc_cd_side_effects",
             "interrupt_or_timing_dependence",
             "executable_conditions_and_blocked_conditions"}
ITEM_STATUSES = {"OBSERVED_STATIC", "SOURCE_FACT", "HYPOTHESIS", "UNKNOWN"}
ABI_STATES = {"NOT_RUN", "BLOCKED", "OBSERVED", "PARTIAL", "VERIFIED"}
SAFETY_VERDICTS = {"EXECUTION_SAFE", "EXECUTION_SAFE_WITH_OBSERVABLE_ZERO_PAGE_WRITE",
                    "EXECUTION_BLOCKED", "EXECUTION_SAFE_WITH_OBSERVABLE_RAM_WRITE"}
EXECUTION_VERDICTS = {"BLOCKED_REFERENCE_JSR_GATE", "BLOCKED_MPR_CHANGE_AND_REFERENCE_JSR_GATE",
                      "NOT_RUN", "OBSERVED_LOCAL_ONLY"}
OBSERVATION_STATUSES = {"NOT_RUN", "OBSERVED_LOCAL_ONLY_PRIOR", "OBSERVED_LOCAL_ONLY"}
HAZARD_NAMES = {"BLOCK_TRANSFER", "VDC_DIRECT_WRITE", "MPR_WRITE", "MPR_READ",
                "IRQ_ENABLE", "IRQ_DISABLE", "ZERO_PAGE_STORE", "PAGE1_STORE",
                "OTHER_STORE", "DATA_DEPENDENT_STORE", "CROSS_PAGE_CALL",
                "INDIRECT_CONTROL_FLOW", "UNRESOLVED_TARGET", "NO_RETURN"}
ENTRY_EVIDENCE = {"JUMP_TABLE_3BYTE_JMP"}

# A decoded instruction with an operand, e.g. "LDA $FEC2" or "JSR $CD10".
FORBIDDEN_INSTRUCTION = re.compile(
    r"\b(?:LDA|LDX|LDY|STA|STX|STY|JSR|JMP|RTS|RTI|INC|DEC|AND|ORA|EOR|CMP|SBC|ASL|LSR"
    r"|ROL|ROR|TAX|TAY|TXA|TYA|PHA|PLA|PHP|PLP|SEI|CLI|TAM|TMA|TII|TDD|TIA|TAI|TIN|SAX"
    r"|SAY|SXY|ST0|ST1|ST2|BSR|BRA|BNE|BEQ|BCC|BCS)\s+(?:\$|#|\()")
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


def validate(document, contracts):
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
    seen = set()
    for api in apis:
        _require(isinstance(api, dict) and set(api) == API_KEYS, "api keys")
        api_id = api["api_id"]
        _require(isinstance(api_id, str) and re.fullmatch(r"slot-[0-9a-f]{2}", api_id),
                 f"api_id {api_id!r} is malformed")
        _require(api_id not in seen, f"duplicate api_id {api_id}")
        seen.add(api_id)
        slot = api["slot"]
        _require(type(slot) is int and 0 <= slot < len(contracts["apis"]), f"{api_id}: slot range")
        _require(api_id == f"slot-{slot:02x}", f"{api_id}: api_id does not match the slot")
        claim = contracts["apis"][slot]
        _require(api["name"] == claim["candidate_name"], f"{api_id}: name differs from the claim ledger")
        _require(api["candidate_entry"] == claim["candidate_entry"] == f"0x{0xE000 + 3 * slot:04X}",
                 f"{api_id}: candidate entry differs from the claim ledger")
        _require(api["entry_evidence"] in ENTRY_EVIDENCE, f"{api_id}: entry evidence vocabulary")
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
        items = api["static_items"]
        _require(isinstance(items, dict) and set(items) == ITEM_KEYS, f"{api_id}: static item keys")
        for key, item in items.items():
            _require(isinstance(item, dict) and set(item) == {"status", "value"},
                     f"{api_id}: static item {key} keys")
            _require(item["status"] in ITEM_STATUSES, f"{api_id}: static item {key} status")
            _require(isinstance(item["value"], str) and item["value"].strip(),
                     f"{api_id}: static item {key} value")
        safety = api["safety"]
        _require(isinstance(safety, dict) and set(safety) == SAFETY_KEYS, f"{api_id}: safety keys")
        _require(safety["verdict"] in SAFETY_VERDICTS, f"{api_id}: safety verdict vocabulary")
        _require(isinstance(safety["basis"], list) and safety["basis"], f"{api_id}: safety basis")
        _require(isinstance(safety["residual_risks"], list) and safety["residual_risks"],
                 f"{api_id}: safety residual risks")
        execution = api["execution"]
        _require(isinstance(execution, dict) and set(execution) == EXECUTION_KEYS,
                 f"{api_id}: execution keys")
        _require(execution["verdict"] in EXECUTION_VERDICTS, f"{api_id}: execution verdict vocabulary")
        _require(execution["observation_status"] in OBSERVATION_STATUSES,
                 f"{api_id}: observation status vocabulary")
        _require(isinstance(execution["note"], str) and execution["note"].strip(),
                 f"{api_id}: execution note")
        _require(api["abi_state"] in ABI_STATES, f"{api_id}: abi state vocabulary")
        _require(api["abi_state"] != "VERIFIED",
                 f"{api_id}: VERIFIED is not reachable from a static review")
        if api["abi_state"] == "OBSERVED":
            _require(execution["observation_status"] != "NOT_RUN",
                     f"{api_id}: OBSERVED needs a recorded observation")
        if execution["observation_status"] == "NOT_RUN":
            _require(api["abi_state"] in ("NOT_RUN", "BLOCKED", "PARTIAL"),
                     f"{api_id}: a slot without an observation may not claim OBSERVED")
        if api["abi_state"] == "PARTIAL":
            _require(execution["observation_status"] != "OBSERVED_LOCAL_ONLY",
                     f"{api_id}: PARTIAL may not carry a full observation")
    _public_text(document)
    return document


def load(review_path=REVIEW_PATH, contracts_path=CONTRACTS_PATH):
    review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    contracts = json.loads(Path(contracts_path).read_text(encoding="utf-8"))
    return validate(review, contracts)


def summary(document):
    counts = {}
    for api in document["apis"]:
        counts[api["abi_state"]] = counts.get(api["abi_state"], 0) + 1
    return {
        "schema": document["schema"],
        "reviewed_apis": len(document["apis"]),
        "abi_states": dict(sorted(counts.items())),
        "execution": document["method"]["execution"],
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
        document = load()
    except (Invalid, OSError, ValueError, KeyError, TypeError) as error:
        print(f"BLOCKED: {error}")
        return 2
    print(json.dumps(summary(document), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
