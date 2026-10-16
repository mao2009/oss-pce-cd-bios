#!/usr/bin/env python3
"""Generate reference-BIOS *plans*, not observations or compatibility evidence.

No proprietary BIOS content is read, imported, embedded or written.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PSG_SUBCOMMANDS = {
    0x00: "PSG_ON", 0x01: "PSG_OFF", 0x02: "PSG_INIT",
    0x03: "PSG_BANK", 0x04: "PSG_TRACK", 0x0B: "PSG_PLAY",
    0x0C: "PSG_MSTAT", 0x10: "PSG_ASTOP", 0x13: "PSG_FDOUT",
}
OBSERVATIONS = [
    "cpu_before_after:A,X,Y,P,SP,PC",
    "mpr_before_after:0-7",
    "ram_diff:bounded_explicit_ranges",
    "elapsed_cycles_or_instructions",
    "return_or_timeout",
]
DANGEROUS = {"CD_BOOT", "CD_RESET", "CD_EXEC", "BM_FORMAT", "BM_WRITE", "BM_DELETE"}
MEDIA = {"CD_BOOT", "CD_RESET", "CD_BASE", "CD_READ", "CD_SEEK",
         "CD_EXEC", "CD_PLAY", "CD_SEARCH", "CD_PAUSE", "CD_STAT",
         "CD_SUBQ", "CD_DINFO", "CD_CONTNTS", "CD_SUBRQ / CD_SUBRD",
         "CD_PCMRD", "CD_FADE"}


def generate(slots):
    if len(slots) != 81 or {x["id"] for x in slots} != set(range(81)):
        raise ValueError("expected exactly 81 unique main slots 0x00..0x50")
    result = []
    for slot in sorted(slots, key=lambda x: x["id"]):
        idx, name = slot["id"], slot["candidate_name"]
        result.append({
            "id": f"slot-{idx:02x}",
            "kind": "main_api",
            "slot": f"0x{idx:02x}",
            "candidate_name": name,
            "entry_candidate": f"0x{0xe000 + 3 * idx:04x}",
            "abi_status": "HYPOTHESIS",
            "execution_status": "NOT_RUN",
            "expectation_status": "NOT_MEASURED",
            "inputs": "TO_BE_DETERMINED_FROM_ABI",
            "preconditions": {
                "bios_revision": "USER_PROVIDED_SYSTEM_CARD_3_0",
                "bios_sha256": "CAPTURE_LOCALLY_NOT_COMMITTED",
                "emulator_revision": "PINNED_REVISION_TO_RECORD",
                "test_rom_sha256": "TO_BE_RECORDED",
                "disc": "SYNTHETIC_MEDIA_IF_REQUIRED" if name in MEDIA else "NOT_REQUIRED_UNLESS_OBSERVED",
                "isolated_instance": True,
            },
            "scenarios": [
                {"case": "candidate_nominal", "status": "DESIGN_PENDING"},
                {"case": "boundary", "status": "DESIGN_PENDING"},
                {"case": "invalid_or_timeout", "status": "DESIGN_PENDING"},
            ],
            "observe": OBSERVATIONS + (["cd_scsi_state", "disc_toc"] if name in MEDIA else []),
            "safety": {
                "execute_automatically": False,
                "requires_manual_fixture_and_abi_review": True,
                "side_effect_risk": "HIGH" if name in DANGEROUS else "UNKNOWN",
                "max_instructions": "MUST_SET_BEFORE_EXECUTION",
                "ram_write_allowlist": "MUST_SET_BEFORE_EXECUTION",
            },
        })
    for selector, name in PSG_SUBCOMMANDS.items():
        result.append({
            "id": f"psg-48-{selector:02x}",
            "kind": "psg_subcommand",
            "dispatch_slot": "0x48",
            "selector_candidate": f"0x{selector:02x}",
            "candidate_name": name,
            "abi_status": "HYPOTHESIS",
            "execution_status": "NOT_RUN",
            "expectation_status": "NOT_MEASURED",
            "inputs": "TO_BE_DETERMINED_FROM_ABI",
            "preconditions": {
                "bios_revision": "USER_PROVIDED_SYSTEM_CARD_3_0",
                "bios_sha256": "CAPTURE_LOCALLY_NOT_COMMITTED",
                "emulator_revision": "PINNED_REVISION_TO_RECORD",
                "isolated_instance": True,
            },
            "scenarios": [
                {"case": "candidate_nominal", "status": "DESIGN_PENDING"},
                {"case": "boundary", "status": "DESIGN_PENDING"},
                {"case": "invalid_or_timeout", "status": "DESIGN_PENDING"},
            ],
            "observe": OBSERVATIONS + ["psg_register_diff", "audio_output_if_available"],
            "safety": {
                "execute_automatically": False,
                "requires_manual_fixture_and_abi_review": True,
                "side_effect_risk": "UNKNOWN",
                "max_instructions": "MUST_SET_BEFORE_EXECUTION",
                "ram_write_allowlist": "MUST_SET_BEFORE_EXECUTION",
            },
        })
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, help="Write reproducible JSON outside tracked specs")
    p.add_argument("--check", action="store_true", help="Validate invariants without writing")
    args = p.parse_args(argv)
    slots = json.loads((ROOT / "spec/api_slots.json").read_text(encoding="utf-8"))["slots"]
    scenarios = generate(slots)
    assert len(scenarios) == 90
    assert len({s["id"] for s in scenarios}) == 90
    assert all(s["execution_status"] == "NOT_RUN"
               and s["expectation_status"] == "NOT_MEASURED"
               and s["safety"]["execute_automatically"] is False for s in scenarios)
    document = {
        "schema": "oss-pce-cd-bios/reference-bios-scenario-plan/v1",
        "type": "PLANNED_ONLY_NOT_EVIDENCE",
        "target": "user-supplied System Card 3.0; black-box reference observations",
        "source_inventory": "spec/api_slots.json",
        "main_api_count": 81,
        "psg_subcommand_count": 9,
        "status": "NOT_RUN",
        "scenarios": scenarios,
    }
    if args.output:
        if args.check:
            raise SystemExit("--check and --output cannot be combined")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print(f"Validated {len(scenarios)} scenario definitions; 0 measurements; 0 executions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
