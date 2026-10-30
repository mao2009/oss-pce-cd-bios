#!/usr/bin/env python3
"""Public API ledger: an allowlist builder and validator (owner ruling, Issue #324/#476).

The public output is built only from explicitly allowed fields. It never copies a record and then deletes keys.
Inputs are the contract ledger (slot, candidate name, candidate entry) and the repository's own jump-table layout
rule (tools/build_rom.py). No secondary-source table is read.

The output carries the status keys only, and every slot has the same status. A per-slot classification (a name pair,
an author label, a confirmed name) is derived from secondary-source structure, so publishing it would reconstruct
that structure. It is checked internally against the contract ledger and is never printed.

Fail closed: the validator rejects any unknown key at any depth, any value outside the uniform status, any slot whose
status differs from the others, and any contract whose entries differ from the layout rule or whose alias structure
differs from the owner's set.

Host only. No ROM and no private image is read.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rom import FIRST_ENTRY  # noqa: E402  (the repository's own jump-table layout)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "spec" / "api_contracts.json"

SCHEMA = "oss-pce-cd-bios/public-api-ledger/v4"
TOP_KEYS = frozenset({"schema", "release_eligible", "apis"})
API_KEYS = frozenset({"slot", "entry_source", "name_source", "name_publication", "source_confidence",
                      "abi_verification", "execution_gate"})
# A fixed description of the DEV table structure. It is not reference-BIOS ABI evidence.
ENTRY_SOURCE = "DEV ROM generated-table structure (tools/build_rom.py); not reference-BIOS ABI evidence"
NAME_PUBLICATION = "WITHHELD_PROVENANCE_PENDING"
# Uniform for every slot: the per-slot classification is not published.
NAME_SOURCE = "WITHHELD_PENDING"
SOURCE_CONFIDENCE = "PENDING"
ABI_VERIFICATION = "UNVERIFIED"
EXECUTION_GATE = "BLOCKED"
SLOTS = 81

# Internal structure is read from the contract ledger (a name pair is a candidate name with ' / ') and is never
# printed. The tool keeps no per-slot list of its own.
NAME_PART = re.compile(r"[A-Z][A-Z0-9_]*")


class Refused(Exception):
    """Raised for every public document that must not be published."""


def _require(condition, message):
    if not condition:
        raise Refused(message)


def entry_for(slot):
    """The jump-table entry the repository layout gives a slot. Used only to check the contract ledger."""
    return f"0x{FIRST_ENTRY + 3 * slot:04X}"


def check_contracts(contracts):
    """The contract ledger must agree with the layout rule and the owner's alias structure. Refused otherwise."""
    apis = contracts["apis"]
    _require(isinstance(apis, list) and len(apis) == SLOTS, "contract: exactly 81 slots")
    for slot, api in enumerate(apis):
        _require(api.get("slot") == slot and type(api.get("slot")) is int, f"slot {slot}: contract slot number")
        _require(api.get("candidate_entry") == entry_for(slot), f"slot {slot}: entry differs from the layout rule")
        name = api.get("candidate_name")
        _require(isinstance(name, str), f"slot {slot}: name is not text")
        parts = name.split(" / ")
        _require(all(NAME_PART.fullmatch(part) for part in parts), f"slot {slot}: name shape")
        _require(len(parts) == len(set(parts)), f"slot {slot}: duplicate name")


def build(contracts):
    """Build the public ledger from explicitly allowed fields only. Every slot carries the same status."""
    check_contracts(contracts)
    apis = [{
        "slot": slot,
        "entry_source": ENTRY_SOURCE,
        "name_source": NAME_SOURCE,
        "name_publication": NAME_PUBLICATION,
        "source_confidence": SOURCE_CONFIDENCE,
        "abi_verification": ABI_VERIFICATION,
        "execution_gate": EXECUTION_GATE,
    } for slot in range(SLOTS)]
    return {"schema": SCHEMA, "release_eligible": False, "apis": apis}


def validate(document, contracts):
    """Raise Refused unless the document is exactly the allowed public shape with the uniform status."""
    check_contracts(contracts)
    _require(isinstance(document, dict) and set(document) == TOP_KEYS, "top-level keys")
    _require(document["schema"] == SCHEMA, "schema")
    _require(document["release_eligible"] is False, "release_eligible must be false")
    apis = document["apis"]
    _require(isinstance(apis, list) and len(apis) == SLOTS, "exactly 81 slots")
    for index, api in enumerate(apis):
        _require(isinstance(api, dict) and set(api) == API_KEYS, f"slot {index}: keys")
        _require(api["slot"] == index and type(api["slot"]) is int, f"slot {index}: slot number")
        _require(api["entry_source"] == ENTRY_SOURCE, f"slot {index}: entry source")
        _require(api["name_publication"] == NAME_PUBLICATION, f"slot {index}: names must stay withheld")
        _require(api["name_source"] == NAME_SOURCE, f"slot {index}: name status must be the uniform withheld state")
        _require(api["source_confidence"] == SOURCE_CONFIDENCE, f"slot {index}: confidence must be the uniform pending state")
        _require(api["abi_verification"] == ABI_VERIFICATION, f"slot {index}: abi verification")
        _require(api["execution_gate"] == EXECUTION_GATE, f"slot {index}: execution gate")
    return document


def main(argv=None):
    try:
        contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # The error text is withheld: it can carry an absolute path.
        print("BLOCKED: an input could not be read (details withheld)", file=sys.stderr)
        return 2
    try:
        document = validate(build(contracts), contracts)
    except Refused as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - any failure is refused without a traceback (a traceback shows the repo path)
        print("BLOCKED: an input could not be validated (details withheld)", file=sys.stderr)
        return 2
    print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
