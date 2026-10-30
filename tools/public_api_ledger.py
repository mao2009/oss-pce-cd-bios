#!/usr/bin/env python3
"""Public API ledger: an allowlist builder and validator (owner ruling, Issue #324/#476).

The public output is built only from explicitly allowed fields. It never copies a record and then deletes keys.
Inputs are the contract ledger (slot, candidate name, candidate entry) and the repository's own jump-table layout
rule (tools/build_rom.py: entries at $E000 + 3 x slot). No secondary-source table is read.

Names are not published. The names come from a secondary source whose reuse is not approved (provenance PENDING),
so the public output carries only name_source and name_publication. Alias and author-label facts are published
because they describe the status of a name, not the name itself.

Fail closed: the validator rejects any unknown key at any depth, any value outside its closed vocabulary, any
contract whose entries differ from the layout rule or whose alias structure differs from the owner's set. No entry
value is published: the public output lists slots and status only.

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

SCHEMA = "oss-pce-cd-bios/public-api-ledger/v3"
TOP_KEYS = frozenset({"schema", "release_eligible", "apis"})
# No entry value is published here (owner ruling): the DEV table rule is checked on the contract side only.
API_KEYS = frozenset({"slot", "entry_source", "name_source", "name_publication", "source_confidence",
                      "abi_verification", "execution_gate"})
ENTRY_SOURCE = "DEV ROM generated-table structure (tools/build_rom.py); not reference-BIOS ABI evidence"
NAME_PUBLICATION = "WITHHELD_PROVENANCE_PENDING"
# SECONDARY_SOURCE: one name. UNRESOLVED_ALTERNATIVES: the name is an unresolved pair. UNCONFIRMED_AUTHOR_LABEL:
# the name is the author's own label for a 2.0-added slot (this takes precedence over the pair status).
NAME_SOURCES = frozenset({"SECONDARY_SOURCE", "UNRESOLVED_ALTERNATIVES", "UNCONFIRMED_AUTHOR_LABEL"})
SOURCE_CONFIDENCES = frozenset({"SECONDARY", "UNCONFIRMED_NAME"})
ABI_VERIFICATION = "UNVERIFIED"
EXECUTION_GATE = "BLOCKED"
# Slots whose name is not confirmed: the author's labels (77-80) and the unresolved pair (74).
UNCONFIRMED_NAME_SLOTS = frozenset({74, 77, 78, 79, 80})
# Slots whose name is the author's own label (the author's additions, not an official name).
AUTHOR_LABEL_SLOTS = frozenset({77, 78, 79, 80})
# Slots whose candidate name is an unresolved pair of names. The owner's set; the contract must agree with it.
ALTERNATIVE_NAME_SLOTS = frozenset({13, 74, 75, 76, 80})
NAME_PART = re.compile(r"[A-Z][A-Z0-9_]*")
SLOTS = 81


class Refused(Exception):
    """Raised for every public document that must not be published."""


def _require(condition, message):
    if not condition:
        raise Refused(message)


def entry_for(slot):
    """The jump-table entry the repository layout gives a slot (the only entry rule used here)."""
    return f"0x{FIRST_ENTRY + 3 * slot:04X}"


def check_contracts(contracts):
    """The contract ledger must agree with the layout rule and the owner's alias structure. Refused otherwise."""
    apis = contracts["apis"]
    _require(isinstance(apis, list) and len(apis) == SLOTS, "contract: exactly 81 slots")
    alias_slots = set()
    for slot, api in enumerate(apis):
        _require(api.get("slot") == slot and type(api.get("slot")) is int, f"slot {slot}: contract slot number")
        _require(api.get("candidate_entry") == entry_for(slot), f"slot {slot}: entry differs from the layout rule")
        name = api.get("candidate_name")
        _require(isinstance(name, str), f"slot {slot}: name is not text")
        parts = name.split(" / ")
        _require(all(NAME_PART.fullmatch(part) for part in parts), f"slot {slot}: name shape")
        _require(len(parts) == len(set(parts)), f"slot {slot}: duplicate name")
        if len(parts) > 1:
            alias_slots.add(slot)
    _require(alias_slots == ALTERNATIVE_NAME_SLOTS, "contract: alias slots differ from the owner's set")


def build(contracts):
    """Build the public ledger from explicitly allowed fields only."""
    check_contracts(contracts)
    apis = []
    for slot in range(SLOTS):
        # The stronger status wins: an author label is reported as such even where it is also a pair.
        if slot in AUTHOR_LABEL_SLOTS:
            name_source = "UNCONFIRMED_AUTHOR_LABEL"
        elif slot in ALTERNATIVE_NAME_SLOTS:
            name_source = "UNRESOLVED_ALTERNATIVES"
        else:
            name_source = "SECONDARY_SOURCE"
        apis.append({
            "slot": slot,
            "entry_source": ENTRY_SOURCE,
            "name_source": name_source,
            "name_publication": NAME_PUBLICATION,
            "source_confidence": "UNCONFIRMED_NAME" if slot in UNCONFIRMED_NAME_SLOTS else "SECONDARY",
            "abi_verification": ABI_VERIFICATION,
            "execution_gate": EXECUTION_GATE,
        })
    return {"schema": SCHEMA, "release_eligible": False, "apis": apis}


def validate(document, contracts):
    """Raise Refused unless the document is exactly the allowed public shape with consistent values."""
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
        _require(api["name_source"] in NAME_SOURCES, f"slot {index}: name source vocabulary")
        _require(api["source_confidence"] in SOURCE_CONFIDENCES, f"slot {index}: confidence vocabulary")
        _require((api["source_confidence"] == "UNCONFIRMED_NAME") == (index in UNCONFIRMED_NAME_SLOTS),
                 f"slot {index}: the unconfirmed-name set is fixed (slots 74 and 77-80)")
        if index in AUTHOR_LABEL_SLOTS:
            _require(api["name_source"] == "UNCONFIRMED_AUTHOR_LABEL", f"slot {index}: author label")
        elif index in ALTERNATIVE_NAME_SLOTS:
            _require(api["name_source"] == "UNRESOLVED_ALTERNATIVES", f"slot {index}: alternative name")
        else:
            _require(api["name_source"] == "SECONDARY_SOURCE", f"slot {index}: secondary name")
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
