"""Issue #692: detect a stop at the DEV unimplemented-API stub on the pinned Geargrafx core, distinct from budget.

Synthetic media only, DEV 256 KiB image (unchanged build), loaded through LoadBiosFromBuffer + LoadMedia.
Each case is a synthetic IPL payload that calls one jump-table entry (or spins). The expectations are:
- slot 02 (unimplemented, spec name CD_BASE): stub reached, X == 2, pc == stub, marker NOT written (no return)
- boundary: the last unimplemented slot below the first implemented slot (derived from src/overrides): same rule
- boundary: the first implemented slot: the stub is NOT reached
- spin (no halt label): BUDGET, never API_UNIMPLEMENTED
No case is a success claim for an API. A PASS here means the expected classification was observed.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "tools"), str(HERE)]
from api_halt_classify import (API_UNIMPLEMENTED_CONFIRMED, BUDGET, CD_HALT_OK, classify,  # noqa: E402
                               implemented_slots, unimplemented_slots)
from build_cdload_exec import parse_labels  # noqa: E402
from build_dev_ipl_loader_bios import build_ipl_loader_bios  # noqa: E402
from environment import executable  # noqa: E402
from geargrafx_api import compile_probe  # noqa: E402
from geargrafx_cdread import execute  # noqa: E402
import geargrafx_ipl_loader as L  # noqa: E402

PAYLOAD_SRC = HERE / "ipl_api_call_payload.s"
PRES_OFFSET = 0x2F80 - 0x2800  # payload marker offset inside the probe dump ($2800-$3FFF)
MARKER = 0xAB
API_STATES = {API_UNIMPLEMENTED_CONFIRMED, "API_SLOT_MISMATCH", "API_PC_MISMATCH", "API_NOT_CONFIRMED"}


def entry_of(slot: int) -> int:
    """DEV jump table: 3-byte JMP per slot at $E000 + slot*3 (tools/build_rom.py)."""
    return 0xE000 + slot * 3


def build_payload(out: Path, name: str, defines: list) -> tuple:
    out.mkdir(parents=True, exist_ok=True)
    obj, rom, lbl, cfg = out / f"{name}.o", out / f"{name}.bin", out / f"{name}.lbl", out / f"{name}.cfg"
    cfg.write_text("MEMORY {\n    RAM: start = $3000, size = $0F00, file = %O;\n}\n"
                   "SEGMENTS {\n    CODE: load = RAM, type = ro, start = $3000;\n}\n", encoding="ascii")
    subprocess.run([executable("ca65"), str(PAYLOAD_SRC), *defines, "-o", str(obj), "-g"], check=True)
    subprocess.run([executable("ld65"), "-C", str(cfg), "-o", str(rom), "-Ln", str(lbl), str(obj)], check=True)
    code = rom.read_bytes()
    labels = parse_labels(lbl)
    if labels.get("payload_entry") != 0x3000 or not 1 <= len(code) <= 2048:
        raise ValueError("payload must start at $3000 and fit one sector")
    return code, labels["payload_halt"]


def run_case(probe, bios, stub, folder: Path, name: str, code: bytes, halt: int, budget: int) -> list:
    body = L.body_for(code, 1)
    info = L.info_sector(2, 1, 0x3000, 0x3000, L.MPRS_STD, 0, sum(body) & 0xFFFF)
    cue = L.make_disc(folder / name, L.case_disc({"n": 1, "iplblk": 2}, body, info))
    labels = folder / f"{name}.labels"
    labels.write_text(f"cd_halt_ok {halt:04x}\n"
                      f"cd_halt_fail {stub['cd_halt_fail']:04x}\n"
                      f"cd_unexpected_interrupt {stub['cd_unexpected_interrupt']:04x}\n"
                      f"api_unimplemented {stub['api_unimplemented']:04x}\n", encoding="ascii")
    records = execute(probe, bios, str(cue), labels, budget, None)
    if len(records["RUN"]) != 2:
        raise ValueError(f"{name}: expected cold and warm runs")
    return records["RUN"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    original = ROOT / "build" / args.mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out = ROOT / "build" / args.mode / "api-unimplemented"
    bios, _ = build_ipl_loader_bios(original, out, L.SEL, args.mode, "main")
    bios_sha = hashlib.sha256(bios.read_bytes()).hexdigest()
    stub = parse_labels(out / "ipl-loader-bios-main.lbl")
    for needed in ("api_unimplemented", "cd_halt_fail", "cd_unexpected_interrupt"):
        if needed not in stub:
            raise SystemExit(f"BLOCKED: label {needed} missing from the DEV image")
    implemented = implemented_slots()
    unimpl = unimplemented_slots()
    first_impl = min(implemented)
    below = first_impl - 1
    if 0x02 not in unimpl or below not in unimpl or first_impl not in implemented:
        raise SystemExit("FAIL: ledger boundary derivation does not match src/overrides")
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-api-unimplemented-probe")

    cases = [
        ("slot02_unimplemented_stub", ("API_ENTRY", entry_of(0x02)), 0x02, "api", 20_000_000),
        ("boundary_last_unimplemented_below_first_impl", ("API_ENTRY", entry_of(below)), below, "api", 20_000_000),
        ("boundary_first_implemented_no_stub", ("API_ENTRY", entry_of(first_impl)), first_impl, "impl", 20_000_000),
        ("budget_spin_not_misread", ("SPIN", None), None, "budget", 6_000_000),
    ]
    results, failures = {}, []
    with tempfile.TemporaryDirectory(prefix="pce-api-unimpl-") as tmp:
        tmp = Path(tmp)
        for name, (kind, value), slot, expect, budget in cases:
            defines = ["-DSPIN"] if kind == "SPIN" else ["-DAPI_ENTRY=$%04X" % value]
            code, halt = build_payload(tmp, name, defines)
            runs = run_case(probe, bios, stub, tmp, name, code, halt, budget)
            verdicts = []
            for run in runs:
                verdict = classify(run, stub_address=stub["api_unimplemented"], expected_slot=slot)
                dump = bytes.fromhex(run["buffer_hex"])
                if len(dump) <= PRES_OFFSET:
                    raise ValueError(f"{name} {run['cycle']}: probe dump too short for the payload marker")
                marker = dump[PRES_OFFSET]
                verdicts.append({"cycle": run["cycle"], "state": verdict["state"], "reason": run["reason"],
                                 "halt_confirmed": run["halt_confirmed"], "x": run["x"], "pc": run["pc"],
                                 "marker_written": marker == MARKER})
            results[name] = verdicts
            for v in verdicts:
                if expect == "api":
                    ok = v["state"] == API_UNIMPLEMENTED_CONFIRMED and v["x"] == slot and not v["marker_written"]
                elif expect == "impl":
                    # the implemented override returns to the payload, the marker is written, and the run
                    # reaches cd_halt_ok; the stub is not reached.
                    ok = v["state"] == CD_HALT_OK and v["marker_written"] and v["state"] not in API_STATES
                else:
                    ok = v["state"] == BUDGET
                if not ok:
                    failures.append(f"{name} {v['cycle']}: {v}")
    evidence = {"scope": "synthetic media, DEV 256 KiB image, pinned Geargrafx; NOT compatible, NOT BIOS ABI",
                "bios_image_sha256": bios_sha, "stub_label_address": stub["api_unimplemented"],
                "implemented_slots": sorted(implemented), "boundary": {"below": below, "first_impl": first_impl},
                "cases": results, "status": "FAIL" if failures else "PASS"}
    (out / "api-unimplemented-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                                                         encoding="utf-8")
    if failures:
        raise SystemExit("FAIL api-unimplemented: " + "; ".join(failures[:3]))
    print(f"PASS api-unimplemented: {len(cases)} cases (cold+warm each); stub is a halt, not a success; "
          f"NOT compatible, release_eligible=false")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"api-unimplemented FAIL/BLOCKED: {error}") from error
