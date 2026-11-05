"""Real Geargrafx: DEV-only IPL boot from RESET (Issue #668). Synthetic media only. NOT compatible, NOT a BIOS ABI.

One ROM, built once from the host selection record only (no payload bytes embedded). Then, on the pinned core:
  RESET -> init -> host-selected LBA -> READ(6) -> guest PCEX validation -> copy to $3000 -> execute -> return.
Two different synthetic IPLs on the SAME ROM must produce different observed results. Every negative must be
rejected without copying or executing. The ROM bytes are re-verified after all runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geargrafx_cdload_exec as T  # judges and synthetic payload helpers, shared with #5/#14 and PR #658
from build_cdload_exec import DISC_SECTORS, HDR_BYTES, SECTOR_BYTES, USER_OFFSET, build_exec_disc, encode_sector
from build_experimental_bios_boot import PAYLOAD_LABELS, build_experimental_payload
from build_ipl_boot import build_ipl_rom
from experimental_track_selection import SelectionError, selection_from_cue
from geargrafx_api import compile_probe
from geargrafx_cdread import execute
from make_cd_image import BIN_NAME, CUE_NAME, RAW_BYTES, build_image

STEP_BUDGET = T.STEP_BUDGET


def tamper_bin(cue, lba, edits):
    bin_path = cue.parent / BIN_NAME
    raw = bytearray(bin_path.read_bytes())
    for offset, delta in edits:
        raw[lba * RAW_BYTES + USER_OFFSET + offset] ^= delta
    bin_path.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("debug", "release"), default="debug")
    args = parser.parse_args()
    mode = args.mode
    out = ROOT / "build" / mode / "dev-ipl-boot"
    original = ROOT / "build" / mode / "bios-dev" / "dev-only-not-compatible-syscard3.pce"
    if not original.is_file():
        raise SystemExit("BLOCKED: run make dev-rom first")
    out.mkdir(parents=True, exist_ok=True)
    probe = compile_probe(out, "geargrafx_cdread_probe.cpp", "geargrafx-dev-ipl-probe")
    results = {}

    with tempfile.TemporaryDirectory(prefix="pce-dev-ipl-") as tmp:
        tmp = Path(tmp)
        # Learn the host-selected LBA from the synthetic layout (same for every disc built with build_image/build_exec_disc).
        layout_dir = tmp / "layout"
        build_image(layout_dir, DISC_SECTORS)
        layout_cue = layout_dir / CUE_NAME
        lba = selection_from_cue(layout_cue, layout_cue)["lba"]

        rom_path, rom_symbols = build_ipl_rom(original, out, mode, layout_cue)
        rom_sha = hashlib.sha256(rom_path.read_bytes()).hexdigest()
        results["rom"] = {"name": rom_path.name, "sha256": rom_sha, "bytes": rom_path.stat().st_size,
                          "payload_embedded_in_rom": False, "selected_lba": lba}

        code_a, payload_symbols = build_experimental_payload(out / "payload", mode)
        code_b = T.payload_variant(code_a, T.VARIANT_TRAILER)
        symbols = dict(rom_symbols)
        symbols.update({name: payload_symbols[name] for name in PAYLOAD_LABELS})
        labels = out / "trace-labels.txt"
        labels.write_text("".join(f"{name} {value:04x}\n" for name, value in symbols.items()), encoding="ascii")

        def disc(name, code, entry=0, edits=(), blank=False):
            directory = tmp / name
            if blank:
                build_image(directory, DISC_SECTORS)
            else:
                build_exec_disc(directory, lba, code, entry)
            cue = directory / CUE_NAME
            tamper_bin(cue, lba, edits)
            return cue

        def run(cue, inject=None, budget=STEP_BUDGET):
            records = execute(probe, rom_path, cue, labels, budget, inject)
            if records["rom_sha256"] != rom_sha:
                raise ValueError("probe did not execute the single IPL ROM")
            return records

        # ---- positive: IPL A, cold and warm ----
        records_a = run(disc("ipl-a", code_a))
        T.judge_success(records_a, code_a, symbols, lba, 1)
        results["ipl_a"] = {"status": "PASS", "lba": lba, "payload_bytes": len(code_a),
                            "sector_sha256": hashlib.sha256(encode_sector(lba, code_a)).hexdigest()}

        # ---- positive: IPL B on the same ROM must produce a different observed result ----
        records_b = run(disc("ipl-b", code_b))
        T.judge_marker_mismatch(records_b, code_b, symbols, lba, T.VARIANT_TRAILER)
        try:
            T.judge_success(records_b, code_b, symbols, lba, 1)
        except ValueError as error:
            results["ipl_b"] = {"status": "PASS_EXPECTED_DIFFERENT_RESULT", "guest_result": "PAYLOAD_MARKER_MISMATCH(30)",
                                "success_judge_rejected_because": str(error)}
        else:
            raise ValueError("success judge accepted IPL B")

        # ---- negatives: rejected before copy/execution (reuse the #5/#14 expectations) ----
        negatives = [
            ("bad_magic", dict(edits=[(0, 0x01)]), 20, 11, SECTOR_BYTES),
            ("bad_checksum_code_byte", dict(edits=[(HDR_BYTES + 3, 0x01)]), 23, 11, SECTOR_BYTES),
            ("bad_length_zero", dict(edits=[(5, len(code_a))]), 22, 11, SECTOR_BYTES),
            ("bad_entry_offset_equals_length", dict(entry=len(code_a)), 21, 11, SECTOR_BYTES),
            ("no_header_on_disc", dict(blank=True), 20, 11, SECTOR_BYTES),
        ]
        for name, kwargs, code, stage, transferred in negatives:
            records = run(disc(name, code_a, **kwargs))
            T.judge_failure(records, code, stage, transferred)
            results[name] = {"status": "PASS_EXPECTED_FAILURE", "guest_error": code, "stage": stage, "bytes": transferred}

        records = run(disc("abort", code_a), inject="eject-in-data")
        T.judge_failure(records, 4, 5, 0)
        results["drive_abort_at_data_in"] = {"status": "PASS_EXPECTED_FAILURE", "guest_error": 4, "stage": 5, "bytes": 0}

        records = run(disc("budget", code_a), budget=1000)
        if any(run_["reason"] != "budget" for run_ in records["RUN"]) or any(
                h["name"] in T.ORDER[2:] for h in records["HIT"]):
            raise ValueError("step-budget run did not stop before the IPL was entered")
        results["step_budget_exhausted"] = {"status": "PASS_EXPECTED_FAILURE", "budget": 1000}

        # ---- host refusal: audio-only medium never yields a selection record ----
        audio = tmp / "audio"
        audio.mkdir()
        (audio / "audio.bin").write_bytes(b"\x00" * (2352 * 16))
        audio_cue = audio / CUE_NAME
        audio_cue.write_text('FILE "audio.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n', encoding="ascii")
        try:
            selection_from_cue(audio_cue, audio_cue)
        except SelectionError as error:
            results["audio_only_medium"] = {"status": "PASS_EXPECTED_FAILURE", "host_refusal": str(error)[:120]}
        else:
            raise ValueError("audio-only medium produced a selection record")

    # ROM must be byte-identical after every run (no rebuild per payload).
    if hashlib.sha256(rom_path.read_bytes()).hexdigest() != rom_sha:
        raise ValueError("ROM changed during the test")
    results["rom"]["unchanged_after_all_runs"] = True
    passed = sum(1 for value in results.values() if isinstance(value, dict) and str(value.get("status", "")).startswith("PASS"))
    evidence = {"scope": "DEV-only IPL boot, synthetic media only; NOT compatible, NOT a BIOS ABI, NOT commercial",
                "mode": mode, "results": results, "status": "PASS"}
    (out / "dev-ipl-boot-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"PASS DEV-only IPL boot from RESET ({passed} scenarios; one ROM, two synthetic IPLs; cold+warm); "
          f"NOT compatible, NOT BIOS, NOT DEV_CALLABLE")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, SelectionError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"dev-ipl-boot integration FAIL/BLOCKED: {error}") from error
